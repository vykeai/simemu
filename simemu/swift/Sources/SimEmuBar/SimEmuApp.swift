import SwiftUI
import Foundation

// MARK: - Session Model

private struct Session {
    let id: String
    let platform: String
    let formFactor: String
    let status: String
    let label: String
    let agent: String
    let createdAt: Date?
    let heartbeatAt: Date?
    let expiresAt: Date?
    let osVersion: String
    let deviceName: String

    var platformIcon: String {
        switch platform {
        case "android": return "\u{1F916}" // 🤖
        default:        return "\u{1F4F1}" // 📱
        }
    }

    var statusDot: String {
        switch status {
        case "active": return "\u{1F7E2}" // 🟢
        case "idle":   return "\u{1F7E1}" // 🟡
        default:       return "\u{26AA}"  // ⚪
        }
    }

    var idleString: String {
        guard let hb = heartbeatAt else { return "" }
        let seconds = Int(Date().timeIntervalSince(hb))
        if seconds < 60 { return "idle \(seconds)s" }
        let minutes = seconds / 60
        if minutes < 60 { return "idle \(minutes)m" }
        let hours = minutes / 60
        return "idle \(hours)h\(minutes % 60)m"
    }

    var expiresString: String {
        guard let exp = expiresAt else { return "" }
        let seconds = Int(exp.timeIntervalSince(Date()))
        guard seconds > 0 else { return "expired" }
        let minutes = seconds / 60
        if minutes < 60 { return "expires \(minutes)m" }
        let hours = minutes / 60
        return "expires \(hours)h\(minutes % 60)m"
    }

    var project: String {
        if !agent.isEmpty { return agent }
        return label.components(separatedBy: " ").first ?? "?"
    }

    /// Short OS label like "iOS 26.3" or "Android 15"
    var osLabel: String {
        if !osVersion.isEmpty { return osVersion }
        return platform == "android" ? "Android" : "iOS"
    }

    var truncatedLabel: String {
        if label.count <= 36 { return label }
        return String(label.prefix(33)) + "..."
    }

    // Primary line:  🟢 📱 goala · s-794bc6 · iOS 26.3
    var primaryLine: String {
        "\(statusDot) \(platformIcon) \(project) \u{00B7} \(id) \u{00B7} \(osLabel)"
    }

    // Detail line:   goala local auth fresh · idle 2m
    var detailLine: String {
        var parts: [String] = []
        parts.append(truncatedLabel)
        if status == "parked" {
            parts.append("parked")
        } else {
            let idle = idleString
            if !idle.isEmpty { parts.append(idle) }
        }
        let exp = expiresString
        if !exp.isEmpty { parts.append(exp) }
        return parts.joined(separator: " \u{00B7} ")
    }
}

// MARK: - Data Loading

private func loadSessions() -> [Session] {
    let home = FileManager.default.homeDirectoryForCurrentUser
    let file = home.appendingPathComponent(".simemu/sessions.json")
    guard let data = try? Data(contentsOf: file),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let sessions = json["sessions"] as? [String: [String: Any]] else {
        return []
    }

    let iso = ISO8601DateFormatter()
    iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let isoBasic = ISO8601DateFormatter()
    isoBasic.formatOptions = [.withInternetDateTime]

    func parseDate(_ value: Any?) -> Date? {
        guard let str = value as? String else { return nil }
        return iso.date(from: str) ?? isoBasic.date(from: str)
    }

    var result: [Session] = []
    for (sid, raw) in sessions {
        let status = raw["status"] as? String ?? ""
        guard ["active", "idle", "parked"].contains(status) else { continue }
        result.append(Session(
            id: sid,
            platform: raw["platform"] as? String ?? "?",
            formFactor: raw["form_factor"] as? String ?? "phone",
            status: status,
            label: raw["label"] as? String ?? "",
            agent: raw["agent"] as? String ?? "",
            createdAt: parseDate(raw["created_at"]),
            heartbeatAt: parseDate(raw["heartbeat_at"]),
            expiresAt: parseDate(raw["expires_at"]),
            osVersion: raw["resolved_os_version"] as? String ?? "",
            deviceName: raw["device_name"] as? String ?? ""
        ))
    }

    // Sort: active first, then idle, then parked. Within each group, most recent heartbeat first.
    let statusOrder: [String: Int] = ["active": 0, "idle": 1, "parked": 2]
    result.sort { a, b in
        let oa = statusOrder[a.status] ?? 9
        let ob = statusOrder[b.status] ?? 9
        if oa != ob { return oa < ob }
        let ha = a.heartbeatAt ?? .distantPast
        let hb = b.heartbeatAt ?? .distantPast
        return ha > hb
    }
    return result
}

private struct Config {
    var windowMode: String = "default"
    var memoryBudgetMB: Int = 16384  // 16 GB default

    var memoryBudgetGB: Int {
        get { memoryBudgetMB / 1024 }
        set { memoryBudgetMB = newValue * 1024 }
    }

    static func load() -> Config {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let file = home.appendingPathComponent(".simemu/config.json")
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return Config()
        }
        var c = Config()
        if let mode = json["window_mode"] as? String { c.windowMode = mode }
        if let budget = json["memory_budget_mb"] as? Int { c.memoryBudgetMB = budget }
        return c
    }

    func save() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let dir = home.appendingPathComponent(".simemu")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("config.json")

        // Read existing config to preserve other keys
        var existing: [String: Any] = [:]
        if let data = try? Data(contentsOf: file),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            existing = json
        }
        existing["window_mode"] = windowMode
        existing["memory_budget_mb"] = memoryBudgetMB
        if let data = try? JSONSerialization.data(withJSONObject: existing, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: file, options: .atomic)
        }
    }
}

// MARK: - App

@main
struct SimEmuBarApp: App {

    init() {
        let myPID = ProcessInfo.processInfo.processIdentifier
        NSWorkspace.shared.runningApplications
            .filter { $0.localizedName == "SimEmuBar" && $0.processIdentifier != myPID }
            .forEach { $0.terminate() }

        DispatchQueue.main.async {
            NSApp.setActivationPolicy(.accessory)
        }
    }

    var body: some Scene {
        MenuBarExtra {
            SimEmuMenu()
        } label: {
            SimEmuMenuBarLabel()
        }
        .menuBarExtraStyle(.menu)
    }
}

// MARK: - Menu Bar Label

/// Shows active count in the menu bar. Reads on each render.
struct SimEmuMenuBarLabel: View {
    var body: some View {
        let sessions = loadSessions()
        let active = sessions.filter { $0.status == "active" }.count
        let idle = sessions.filter { $0.status == "idle" }.count
        let total = active + idle
        let label: String = {
            if total == 0 && !sessions.isEmpty { return "parked" }
            if total == 0 { return "sim" }
            return "\(total)"
        }()
        Label(label, systemImage: "iphone")
    }
}

// MARK: - Main Menu

struct SimEmuMenu: View {
    @State private var sessions: [Session] = []
    @State private var config = Config()

    var body: some View {
        let active = sessions.filter { $0.status == "active" }.count
        let idle = sessions.filter { $0.status == "idle" }.count
        let parked = sessions.filter { $0.status == "parked" }.count

        // Header
        Section {
            headerView(active: active, idle: idle, parked: parked)
        }

        Divider()

        // Sessions
        Section {
            if sessions.isEmpty {
                Text("No sessions")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(sessions, id: \.id) { s in
                    sessionEntry(s)
                }
            }
        }

        Divider()

        // Settings
        Section {
            windowModeMenu
            memoryBudgetMenu
        }

        Divider()

        // Actions
        Section {
            Button("\u{1F504} Refresh") {
                // Menu will re-read on next open; this is a no-op placeholder
            }
            Button("\u{23FB} Quit SimEmuBar") {
                NSApp.terminate(nil)
            }
        }
    }

    // MARK: Header

    @ViewBuilder
    private func headerView(active: Int, idle: Int, parked: Int) -> some View {
        let parts = [
            active > 0 ? "\(active) active" : nil,
            idle > 0 ? "\(idle) idle" : nil,
            parked > 0 ? "\(parked) parked" : nil
        ].compactMap { $0 }.joined(separator: "  \u{00B7}  ")

        Text("Sessions \u{2014} \(parts.isEmpty ? "none" : parts)")
            .font(.system(size: 12, weight: .semibold))
    }

    // MARK: Session Entry

    @ViewBuilder
    private func sessionEntry(_ s: Session) -> some View {
        // Two-line entry using a submenu for the detail line + extra info
        Menu {
            Text("\(s.deviceName.isEmpty ? s.formFactor : s.deviceName)")
            Text(s.osLabel)
            if let created = s.createdAt {
                let formatter = RelativeDateTimeFormatter()
                Text("Created \(formatter.localizedString(for: created, relativeTo: Date()))")
            }
            if s.status == "parked" {
                Text("Boots on next `do` command")
            }
            Divider()
            Text("Session \(s.id)")
                .foregroundStyle(.secondary)
        } label: {
            Text(sessionMenuLabel(s))
        }
    }

    private func sessionMenuLabel(_ s: Session) -> String {
        // Line 1: 🟢 📱 goala · s-794bc6 · iOS 26.3
        // Line 2:    label snippet · idle 2m
        let line1 = s.primaryLine
        let line2 = "     \(s.detailLine)"
        return "\(line1)\n\(line2)"
    }

    // MARK: Window Mode

    private var windowModeMenu: some View {
        Menu {
            ForEach(["hidden", "corner", "display", "default"], id: \.self) { mode in
                Button {
                    var c = config
                    c.windowMode = mode
                    c.save()
                    config = c
                } label: {
                    if mode == config.windowMode {
                        Text("\u{2713} \(mode)")
                    } else {
                        Text("   \(mode)")
                    }
                }
            }
        } label: {
            Text("\u{1F5BC} Window Mode: \(config.windowMode)")
        }
    }

    // MARK: Memory Budget

    private var memoryBudgetMenu: some View {
        Menu {
            ForEach([8, 16, 24, 32], id: \.self) { gb in
                Button {
                    var c = config
                    c.memoryBudgetGB = gb
                    c.save()
                    config = c
                } label: {
                    if gb == config.memoryBudgetGB {
                        Text("\u{2713} \(gb) GB")
                    } else {
                        Text("   \(gb) GB")
                    }
                }
            }
        } label: {
            Text("\u{1F4BE} Memory Budget: \(config.memoryBudgetGB) GB")
        }
    }

    // MARK: Init (read from disk)

    init() {
        _sessions = State(initialValue: loadSessions())
        _config = State(initialValue: Config.load())
    }
}
