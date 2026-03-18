import Combine
import Foundation
import SwiftUI

final class SimEmuState: ObservableObject {
    @Published var allocations: [AllocationInfo] = []
    @Published var totalMemoryMB: Double = 0
    @Published var maintenanceActive: Bool = false
    @Published var maintenanceMessage: String = ""
    @Published var lureFact: LureFact = LureFact.random()
    @Published var daemonRunning: Bool = false

    private var refreshTimer: Timer?
    private let stateDir: URL
    private let apiBase = "http://127.0.0.1:8765"
    private var daemonCheckDone = false
    private let bgQueue = DispatchQueue(label: "simemu.state.bg", qos: .utility)

    init() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        if let envDir = ProcessInfo.processInfo.environment["SIMEMU_STATE_DIR"] {
            stateDir = URL(fileURLWithPath: envDir)
        } else {
            stateDir = home.appendingPathComponent(".simemu")
        }
        // Start polling after a short delay to avoid blocking SwiftUI scene setup
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.startPolling()
        }
    }

    // MARK: - Polling

    func startPolling() {
        refresh()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func refresh() {
        bgQueue.async { [weak self] in
            guard let self else { return }
            let allocs = self._readStateFile()
            let maint = self._readMaintenanceFile()
            let memoryResult = self._readProcessMemory(allocs: allocs)

            DispatchQueue.main.async {
                self.allocations = memoryResult.allocations
                self.maintenanceActive = maint.0
                self.maintenanceMessage = maint.1
                self.totalMemoryMB = memoryResult.totalMemoryMB
            }

            // Check daemon on background queue (uses URLSession which is async anyway)
            self._checkDaemon()
        }
    }

    // MARK: - Daemon auto-launch

    private func _checkDaemon() {
        let url = URL(string: "\(apiBase)/health")!
        var request = URLRequest(url: url, timeoutInterval: 1.5)
        request.httpMethod = "GET"

        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                    self.daemonRunning = true
                    self.daemonCheckDone = true
                } else {
                    self.daemonRunning = false
                    if !self.daemonCheckDone {
                        self.daemonCheckDone = true
                        self.bgQueue.async {
                            self._launchDaemon()
                        }
                    }
                }
            }
        }.resume()
    }

    private func _launchDaemon() {
        guard let simemu = _findSimemu() else { return }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: simemu)
        proc.arguments = ["serve"]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
            // Don't waitUntilExit — it's a long-running server
        } catch {
            // Silently fail — user can start manually
        }
    }

    private func _findSimemu() -> String? {
        let home = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        let candidates = [
            "\(home)/bin/simemu",
            "/usr/local/bin/simemu",
            "\(home)/.local/bin/simemu",
        ]

        for path in candidates {
            if FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        }

        // Resolve via user's login shell to get full PATH
        let output = _runCommand("/bin/zsh", args: ["-lc", "which simemu"])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !output.isEmpty && FileManager.default.isExecutableFile(atPath: output) {
            return output
        }
        return nil
    }

    // MARK: - State file (called on bg queue)

    private func _readStateFile() -> [AllocationInfo] {
        var result: [AllocationInfo] = []

        // Read v2 sessions (primary)
        let sessionsFile = stateDir.appendingPathComponent("sessions.json")
        if let data = try? Data(contentsOf: sessionsFile),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let sessions = json["sessions"] as? [String: [String: Any]] {
            for (sid, raw) in sessions.sorted(by: { $0.key < $1.key }) {
                let status = raw["status"] as? String ?? ""
                guard ["active", "idle", "parked"].contains(status) else { continue }
                let info = AllocationInfo(
                    slug: sid,
                    simId: raw["sim_id"] as? String ?? "",
                    platform: raw["platform"] as? String ?? "ios",
                    deviceName: raw["device_name"] as? String ?? "Unknown",
                    agent: raw["agent"] as? String ?? "",
                    acquiredAt: raw["created_at"] as? String ?? "",
                    sessionStatus: status,
                    label: raw["label"] as? String ?? "",
                    formFactor: raw["form_factor"] as? String ?? "phone"
                )
                result.append(info)
            }
        }

        // Also read legacy allocations (for projects not yet migrated)
        let legacyFile = stateDir.appendingPathComponent("state.json")
        if let data = try? Data(contentsOf: legacyFile),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let allocs = json["allocations"] as? [String: [String: Any]] {
            let sessionSimIds = Set(result.map(\.simId))
            for (slug, raw) in allocs.sorted(by: { $0.key < $1.key }) {
                let simId = raw["sim_id"] as? String ?? ""
                if sessionSimIds.contains(simId) { continue }
                let info = AllocationInfo(
                    slug: slug,
                    simId: simId,
                    platform: raw["platform"] as? String ?? "ios",
                    deviceName: raw["device_name"] as? String ?? "Unknown",
                    agent: raw["agent"] as? String ?? "",
                    acquiredAt: raw["acquired_at"] as? String ?? ""
                )
                result.append(info)
            }
        }

        return result
    }

    // MARK: - Maintenance (called on bg queue)

    private func _readMaintenanceFile() -> (Bool, String) {
        let file = stateDir.appendingPathComponent("maintenance.json")
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return (false, "")
        }
        return (true, json["message"] as? String ?? "Maintenance active")
    }

    // MARK: - Process memory via ps (called on bg queue)

    struct MemoryResult {
        var allocations: [AllocationInfo]
        var totalMemoryMB: Double
    }

    private func _readProcessMemory(allocs: [AllocationInfo]) -> MemoryResult {
        var allocations = allocs
        let qemuMem = _processMemory(matching: "qemu-system")
        let simMem = _processMemory(matching: "Simulator")

        // Map AVD names to qemu PIDs
        let psOutput = _runPS(args: ["-eo", "pid,args"])
        var avdPids: [String: Int] = [:]
        for line in psOutput.components(separatedBy: "\n") {
            if line.contains("qemu-system"), let range = line.range(of: "-avd ") {
                let after = line[range.upperBound...]
                let avdName = String(after.prefix(while: { !$0.isWhitespace }))
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if let firstPart = trimmed.components(separatedBy: " ").first,
                   let pid = Int(firstPart) {
                    avdPids[avdName] = pid
                }
            }
        }

        // Count booted iOS sims for memory splitting
        let bootedIOSCount = max(1, allocations.filter { a in
            ["ios", "watchos", "tvos", "visionos"].contains(a.platform) && a.isBooted
        }.count)

        let totalSimMem = simMem.values.reduce(0, +)

        for i in allocations.indices {
            let alloc = allocations[i]
            if ["ios", "watchos", "tvos", "visionos"].contains(alloc.platform) {
                let booted = _isIOSSimBooted(udid: alloc.simId)
                allocations[i].isBooted = booted
                if booted {
                    allocations[i].memoryMB = totalSimMem / Double(bootedIOSCount)
                }
            } else {
                if let pid = avdPids[alloc.simId], let mem = qemuMem[pid] {
                    allocations[i].isBooted = true
                    allocations[i].memoryMB = mem
                } else {
                    allocations[i].isBooted = _isAndroidBooted(simId: alloc.simId)
                }
            }
        }

        let totalMemoryMB = qemuMem.values.reduce(0, +) + totalSimMem
        return MemoryResult(allocations: allocations, totalMemoryMB: totalMemoryMB)
    }

    private func _processMemory(matching filter: String) -> [Int: Double] {
        var result: [Int: Double] = [:]
        let output = _runPS(args: ["-eo", "pid,rss,comm"])
        for line in output.components(separatedBy: "\n") {
            guard line.contains(filter) else { continue }
            let parts = line.trimmingCharacters(in: .whitespaces)
                .components(separatedBy: .whitespaces)
                .filter { !$0.isEmpty }
            guard parts.count >= 3,
                  let pid = Int(parts[0]),
                  let rssKB = Double(parts[1])
            else { continue }
            result[pid, default: 0] += rssKB / 1024
        }
        return result
    }

    private func _isIOSSimBooted(udid: String) -> Bool {
        let output = _runCommand("/usr/bin/xcrun", args: ["simctl", "list", "devices", "--json"])
        guard let data = output.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let devices = json["devices"] as? [String: [[String: Any]]]
        else { return false }

        for (_, deviceList) in devices {
            for dev in deviceList {
                if dev["udid"] as? String == udid {
                    return dev["state"] as? String == "Booted"
                }
            }
        }
        return false
    }

    private func _isAndroidBooted(simId: String) -> Bool {
        let output = _runCommand("/usr/bin/env", args: ["adb", "devices", "-l"])
        return output.contains(simId)
    }

    // MARK: - Actions

    func killAll() {
        bgQueue.async { [weak self] in
            self?._runCommand("/usr/bin/pkill", args: ["-9", "-f", "qemu-system"])
            self?._runCommand("/usr/bin/pkill", args: ["-9", "-f", "Genymotion.app"])
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                self?.refresh()
            }
        }
    }

    func toggleMaintenance() {
        bgQueue.async { [weak self] in
            guard let self else { return }
            let currentlyActive = DispatchQueue.main.sync { self.maintenanceActive }
            if currentlyActive {
                let file = self.stateDir.appendingPathComponent("maintenance.json")
                try? FileManager.default.removeItem(at: file)
            } else {
                let file = self.stateDir.appendingPathComponent("maintenance.json")
                let payload: [String: Any] = [
                    "message": "Maintenance enabled from menu bar",
                    "eta_minutes": 10,
                    "started_at": ISO8601DateFormatter().string(from: Date()),
                ]
                if let data = try? JSONSerialization.data(withJSONObject: payload, options: .prettyPrinted) {
                    try? data.write(to: file)
                }
            }
            self.refresh()
        }
    }

    func cycleLureFact() {
        lureFact = LureFact.random()
    }

    // MARK: - Helpers (called on bg queue)

    private func _runPS(args: [String]) -> String {
        _runCommand("/bin/ps", args: args)
    }

    @discardableResult
    private func _runCommand(_ path: String, args: [String]) -> String {
        let proc = Process()
        let pipe = Pipe()
        proc.executableURL = URL(fileURLWithPath: path)
        proc.arguments = args
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
            proc.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8) ?? ""
        } catch {
            return ""
        }
    }

    // MARK: - Computed

    var bootedCount: Int { allocations.filter(\.isBooted).count }

    var menuBarTitle: String {
        if totalMemoryMB < 100 { return "idle" }
        if totalMemoryMB >= 1024 { return String(format: "%.1f GB", totalMemoryMB / 1024) }
        return String(format: "%.0f MB", totalMemoryMB)
    }

    var memoryColor: Color {
        if totalMemoryMB < 4096 { return Design.dotGreen }
        if totalMemoryMB < 8192 { return Design.dotAmber }
        return Design.dotRed
    }
}

struct AllocationInfo: Identifiable {
    let id: String
    let slug: String
    let simId: String
    let platform: String
    let deviceName: String
    let agent: String
    let acquiredAt: String
    let sessionStatus: String   // "active", "idle", "parked", or "" for legacy
    let label: String
    let formFactor: String
    var isBooted: Bool = false
    var memoryMB: Double = 0

    var isV2Session: Bool { !sessionStatus.isEmpty }

    init(slug: String, simId: String, platform: String, deviceName: String, agent: String, acquiredAt: String,
         sessionStatus: String = "", label: String = "", formFactor: String = "phone") {
        self.id = slug
        self.slug = slug
        self.simId = simId
        self.platform = platform
        self.deviceName = deviceName
        self.agent = agent
        self.acquiredAt = acquiredAt
        self.sessionStatus = sessionStatus
        self.label = label
        self.formFactor = formFactor
    }

    var statusColor: Color {
        if !isBooted { return Design.dotGray }
        if memoryMB < 2048 { return Design.dotGreen }
        if memoryMB < 4096 { return Design.dotAmber }
        return Design.dotRed
    }

    var memoryText: String {
        if !isBooted { return "off" }
        if memoryMB < 1 { return "booted" }
        if memoryMB >= 1024 { return String(format: "%.1f GB", memoryMB / 1024) }
        return String(format: "%.0f MB", memoryMB)
    }

    var platformIcon: String {
        switch platform {
        case "android": return "phone.fill"
        case "watchos": return "applewatch"
        case "tvos": return "appletv.fill"
        case "visionos": return "visionpro"
        default: return "iphone"
        }
    }
}
