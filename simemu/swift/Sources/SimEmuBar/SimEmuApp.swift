import SwiftUI
import AppKit
import Foundation

// ============================================================================
// MARK: - Color Palette (simemu-specific dark theme)
// ============================================================================

enum Sim {
    enum Color {
        static let background   = SwiftUI.Color(hex: "#131620")
        static let surface      = SwiftUI.Color(hex: "#1a1d2e")
        static let surfaceHigh  = SwiftUI.Color(hex: "#242838")

        static let active       = SwiftUI.Color(hex: "#34D399")  // green
        static let idle         = SwiftUI.Color(hex: "#FBBF24")  // amber
        static let parked       = SwiftUI.Color(hex: "#6B7280")  // gray
        static let accent       = SwiftUI.Color(hex: "#60A5FA")  // blue
        static let ios          = SwiftUI.Color(hex: "#818CF8")  // indigo
        static let android      = SwiftUI.Color(hex: "#34D399")  // green

        static let textPrimary  = SwiftUI.Color.white.opacity(0.90)
        static let textSecondary = SwiftUI.Color.white.opacity(0.55)
        static let textMuted    = SwiftUI.Color.white.opacity(0.35)

        static let danger       = SwiftUI.Color(hex: "#F87171")
    }

    enum Gradient {
        static let backgroundRadial = RadialGradient(
            colors: [SwiftUI.Color(hex: "#1a1840").opacity(0.7), Sim.Color.background],
            center: .top, startRadius: 0, endRadius: 400
        )
    }
}

extension SwiftUI.Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: .init(charactersIn: "#"))
        let val = UInt64(hex, radix: 16) ?? 0
        let r = Double((val >> 16) & 0xFF) / 255
        let g = Double((val >> 8)  & 0xFF) / 255
        let b = Double( val        & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}

// ============================================================================
// MARK: - Data Models
// ============================================================================

struct Session {
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

    var project: String {
        if !agent.isEmpty { return agent }
        return label.components(separatedBy: " ").first ?? "?"
    }

    var osLabel: String {
        if !osVersion.isEmpty { return osVersion }
        return platform == "android" ? "Android" : "iOS"
    }

    var statusColor: SwiftUI.Color {
        switch status {
        case "active": return Sim.Color.active
        case "idle":   return Sim.Color.idle
        default:       return Sim.Color.parked
        }
    }

    var borderWidth: CGFloat {
        switch status {
        case "active": return 1.2
        case "idle":   return 0.8
        default:       return 0.5
        }
    }

    var glowRadius: CGFloat {
        switch status {
        case "active": return 8
        case "idle":   return 4
        default:       return 0
        }
    }

    var glowColor: SwiftUI.Color {
        switch status {
        case "active": return Sim.Color.active.opacity(0.15)
        case "idle":   return Sim.Color.idle.opacity(0.08)
        default:       return .clear
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
        if minutes < 60 { return "exp \(minutes)m" }
        let hours = minutes / 60
        return "exp \(hours)h\(minutes % 60)m"
    }

    var truncatedLabel: String {
        if label.count <= 28 { return label }
        return String(label.prefix(25)) + "\u{2026}"
    }

    var statusLine: String {
        var parts: [String] = []
        if status == "parked" {
            parts.append("parked")
            parts.append("boots on do")
        } else {
            let idle = idleString
            if !idle.isEmpty { parts.append(idle) }
            let exp = expiresString
            if !exp.isEmpty { parts.append(exp) }
        }
        return parts.joined(separator: " \u{00B7} ")
    }

    var platformBadgeColor: SwiftUI.Color {
        platform == "android" ? Sim.Color.android : Sim.Color.ios
    }
}

struct SimConfig {
    var windowMode: String = "default"
    var memoryBudgetMB: Int = 16384

    var memoryBudgetGB: Int {
        get { memoryBudgetMB / 1024 }
        set { memoryBudgetMB = newValue * 1024 }
    }

    static func load() -> SimConfig {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let file = home.appendingPathComponent(".simemu/config.json")
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return SimConfig()
        }
        var c = SimConfig()
        if let mode = json["window_mode"] as? String { c.windowMode = mode }
        if let budget = json["memory_budget_mb"] as? Int { c.memoryBudgetMB = budget }
        return c
    }

    func save() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let dir = home.appendingPathComponent(".simemu")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("config.json")

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

// ============================================================================
// MARK: - Data Loading
// ============================================================================

func loadSimSessions() -> [Session] {
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

// ============================================================================
// MARK: - App Entry Point (proven macOS 26 pattern)
// ============================================================================

@main
enum SimEmuBarApp {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)

        // Kill older copies
        let myPID = ProcessInfo.processInfo.processIdentifier
        NSWorkspace.shared.runningApplications
            .filter { $0.localizedName == "SimEmuBar" && $0.processIdentifier != myPID }
            .forEach { $0.terminate() }

        let controller = MenuBarController()
        withExtendedLifetime(controller) {
            app.run()
        }
    }
}

// ============================================================================
// MARK: - Menu Bar Controller (AppKit shell)
// ============================================================================

final class MenuBarController: NSObject {
    private var statusItem: NSStatusItem
    private var popover: NSPopover

    override init() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        popover = NSPopover()
        super.init()

        if let button = statusItem.button {
            button.action = #selector(togglePopover)
            button.target = self
            updateMenuBarLabel()
        }

        popover.contentSize = NSSize(width: 580, height: 620)
        popover.behavior = .transient
        popover.appearance = NSAppearance(named: .darkAqua)

        // Defer SwiftUI view setup to after run loop starts
        DispatchQueue.main.async { [self] in
            let hostingController = NSHostingController(
                rootView: SimEmuPanel()
                    .frame(width: 580, height: 620)
            )
            hostingController.view.layer?.backgroundColor = NSColor.clear.cgColor
            self.popover.contentViewController = hostingController
        }

        // Periodic label update
        Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.updateMenuBarLabel()
        }
    }

    private func updateMenuBarLabel() {
        let sessions = loadSimSessions()
        let booted = sessions.filter { $0.status == "active" || $0.status == "idle" }.count

        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "iphone", accessibilityDescription: "SimEmu")
            button.imagePosition = .imageLeading
            if booted == 0 && !sessions.isEmpty {
                button.title = "pk"
            } else if booted == 0 {
                button.title = ""
            } else {
                button.title = "\(booted)"
            }
        }
    }

    @objc func togglePopover() {
        if let button = statusItem.button {
            if popover.isShown {
                popover.performClose(nil)
            } else {
                // Refresh the view with latest data
                let hostingController = NSHostingController(
                    rootView: SimEmuPanel()
                        .frame(width: 580, height: 620)
                )
                hostingController.view.layer?.backgroundColor = NSColor.clear.cgColor
                popover.contentViewController = hostingController
                popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
                updateMenuBarLabel()
            }
        }
    }
}

// ============================================================================
// MARK: - Main Panel View
// ============================================================================

struct SimEmuPanel: View {
    @State private var sessions: [Session]
    @State private var config: SimConfig
    @State private var showSettings: Bool = false

    init() {
        _sessions = State(initialValue: loadSimSessions())
        _config = State(initialValue: SimConfig.load())
    }

    private var activeSessions: [Session] { sessions.filter { $0.status == "active" } }
    private var idleSessions: [Session] { sessions.filter { $0.status == "idle" } }
    private var parkedSessions: [Session] { sessions.filter { $0.status == "parked" } }
    private var bootedCount: Int { activeSessions.count + idleSessions.count }

    private var estimatedMemoryGB: Double {
        let perSession: Double = 0.9 // rough average per booted sim/emu
        return Double(bootedCount) * perSession
    }

    var body: some View {
        ZStack {
            Sim.Gradient.backgroundRadial.ignoresSafeArea()

            VStack(spacing: 0) {
                headerBar
                Divider().overlay(Sim.Color.accent.opacity(0.15))
                summaryBar
                Divider().overlay(Sim.Color.accent.opacity(0.08))

                if sessions.isEmpty {
                    emptyState
                } else {
                    sessionGrid
                }

                Divider().overlay(Sim.Color.accent.opacity(0.08))

                if showSettings {
                    settingsSection
                    Divider().overlay(Sim.Color.accent.opacity(0.08))
                }

                footerBar
            }
        }
    }

    // MARK: - Header Bar

    private var headerBar: some View {
        HStack(spacing: 8) {
            // App icon + name
            Text("\u{1F9A4}")
                .font(.system(size: 16))
            Text("simemu")
                .font(.system(size: 14, weight: .bold, design: .rounded))
                .foregroundStyle(Sim.Color.textPrimary)

            Spacer()

            // Active count pill
            if activeSessions.count > 0 {
                HStack(spacing: 4) {
                    Circle()
                        .fill(Sim.Color.active)
                        .frame(width: 6, height: 6)
                    Text("\(activeSessions.count) active")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Sim.Color.active)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Sim.Color.active.opacity(0.1))
                .clipShape(Capsule())
            }

            // Settings gear
            Button {
                showSettings.toggle()
            } label: {
                Image(systemName: "gearshape")
                    .font(.system(size: 12))
                    .foregroundStyle(showSettings ? Sim.Color.accent : Sim.Color.textSecondary)
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    // MARK: - Summary Bar

    private var summaryBar: some View {
        HStack(spacing: 6) {
            Text("\(bootedCount) of \(sessions.count) sessions booted")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Sim.Color.textPrimary)

            Text("\u{00B7}")
                .foregroundStyle(Sim.Color.textMuted)

            Text("est. \(String(format: "%.1f", estimatedMemoryGB)) GB")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Sim.Color.textSecondary)

            Spacer()

            // Platform counts
            let iosCount = sessions.filter { $0.platform == "ios" }.count
            let androidCount = sessions.filter { $0.platform == "android" }.count
            if iosCount > 0 {
                platformCountPill(label: "iOS", count: iosCount, color: Sim.Color.ios)
            }
            if androidCount > 0 {
                platformCountPill(label: "Android", count: androidCount, color: Sim.Color.android)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 7)
        .background(Sim.Color.surface.opacity(0.5))
    }

    private func platformCountPill(label: String, count: Int, color: SwiftUI.Color) -> some View {
        HStack(spacing: 3) {
            Text(label)
                .font(.system(size: 9, weight: .bold))
            Text("\(count)")
                .font(.system(size: 9, weight: .bold))
        }
        .foregroundStyle(color)
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(color.opacity(0.1))
        .clipShape(Capsule())
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "iphone.slash")
                .font(.system(size: 28))
                .foregroundStyle(Sim.Color.textMuted)
            Text("No sessions")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Sim.Color.textPrimary)
            Text("Run simemu to allocate a simulator")
                .font(.system(size: 11))
                .foregroundStyle(Sim.Color.textSecondary)
        }
        .padding(40)
    }

    // MARK: - Session Grid (2-column)

    private var sessionGrid: some View {
        ScrollView {
            let columns = [
                GridItem(.flexible(), spacing: 8),
                GridItem(.flexible(), spacing: 8)
            ]
            LazyVGrid(columns: columns, spacing: 8) {
                ForEach(sessions, id: \.id) { session in
                    SessionCard(session: session)
                }
            }
            .padding(10)
        }
    }

    // MARK: - Settings Section

    private var settingsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Settings")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(Sim.Color.textPrimary)

            // Window Mode
            HStack {
                Text("Window Mode")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Sim.Color.textSecondary)
                Spacer()
                settingsPicker(
                    options: ["hidden", "corner", "display", "default"],
                    selected: config.windowMode
                ) { mode in
                    config.windowMode = mode
                    config.save()
                }
            }

            // Memory Budget
            HStack {
                Text("Memory Budget")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Sim.Color.textSecondary)
                Spacer()
                settingsPicker(
                    options: ["8 GB", "16 GB", "24 GB", "32 GB"],
                    selected: "\(config.memoryBudgetGB) GB"
                ) { label in
                    let gb = Int(label.replacingOccurrences(of: " GB", with: "")) ?? 16
                    config.memoryBudgetMB = gb * 1024
                    config.save()
                }
            }

            // Headless indicator
            HStack(spacing: 6) {
                Image(systemName: config.windowMode == "hidden"
                    ? "checkmark.square.fill" : "square")
                    .font(.system(size: 12))
                    .foregroundStyle(config.windowMode == "hidden"
                        ? Sim.Color.active : Sim.Color.textMuted)
                Text("Headless (simulators hidden after boot)")
                    .font(.system(size: 11))
                    .foregroundStyle(Sim.Color.textSecondary)
            }
        }
        .padding(14)
        .background(Sim.Color.surface.opacity(0.3))
    }

    private func settingsPicker(options: [String], selected: String,
                                 onSelect: @escaping (String) -> Void) -> some View {
        Menu {
            ForEach(options, id: \.self) { option in
                Button {
                    onSelect(option)
                } label: {
                    if option == selected {
                        Text("\u{2713} \(option)")
                    } else {
                        Text("   \(option)")
                    }
                }
            }
        } label: {
            HStack(spacing: 4) {
                Text(selected)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Sim.Color.accent)
                Image(systemName: "chevron.down")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(Sim.Color.textMuted)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Sim.Color.surfaceHigh)
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }

    // MARK: - Footer Bar

    private var footerBar: some View {
        HStack(spacing: 10) {
            Button {
                // Refresh data
                sessions = loadSimSessions()
                config = SimConfig.load()
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 10))
                    Text("Refresh")
                        .font(.system(size: 10, weight: .medium))
                }
                .foregroundStyle(Sim.Color.textSecondary)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Sim.Color.surfaceHigh)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)

            Spacer()

            Text("v0.1.0")
                .font(.system(size: 9, weight: .medium))
                .foregroundStyle(Sim.Color.textMuted)

            Spacer()

            Button {
                NSApp.terminate(nil)
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "xmark.circle")
                        .font(.system(size: 10))
                    Text("Quit")
                        .font(.system(size: 10, weight: .medium))
                }
                .foregroundStyle(Sim.Color.danger.opacity(0.7))
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Sim.Color.danger.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }
}

// ============================================================================
// MARK: - Session Card
// ============================================================================

struct SessionCard: View {
    let session: Session

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Row 1: Project name + status dot
            HStack {
                Text(session.project)
                    .font(.system(size: 13, weight: .bold, design: .monospaced))
                    .foregroundStyle(Sim.Color.textPrimary)
                    .lineLimit(1)
                Spacer()
                statusDot
            }

            // Row 2: Platform & form factor badges
            HStack(spacing: 4) {
                platformBadge
                if session.formFactor != "phone" {
                    formFactorBadge
                }
            }

            // Row 3: Device name
            if !session.deviceName.isEmpty {
                Text(session.deviceName)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Sim.Color.textPrimary.opacity(0.8))
                    .lineLimit(2)
            }

            // Row 4: OS version
            Text(session.osLabel)
                .font(.system(size: 10))
                .foregroundStyle(Sim.Color.textSecondary)

            // Row 5: Status line (idle time + expiry)
            let statusText = session.statusLine
            if !statusText.isEmpty {
                Text(statusText)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(session.statusColor.opacity(0.8))
            }

            // Row 6: Label (task description)
            if !session.label.isEmpty {
                HStack(spacing: 3) {
                    Text("\u{1F4CD}")
                        .font(.system(size: 9))
                    Text(session.label)
                        .font(.system(size: 10))
                        .foregroundStyle(Sim.Color.textSecondary)
                        .lineLimit(2)
                }
            }
        }
        .padding(10)
        .background(Sim.Color.surfaceHigh)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(session.statusColor.opacity(
                    session.status == "parked" ? 0.2 : 0.5
                ), lineWidth: session.borderWidth)
        )
        .shadow(color: session.glowColor, radius: session.glowRadius)
    }

    // Status dot
    private var statusDot: some View {
        Circle()
            .fill(session.statusColor)
            .frame(width: 8, height: 8)
            .shadow(color: session.statusColor.opacity(0.5), radius: 3)
    }

    // Platform badge pill
    private var platformBadge: some View {
        Text(session.platform == "android" ? "Android" : "iOS")
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(session.platformBadgeColor)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(session.platformBadgeColor.opacity(0.12))
            .clipShape(Capsule())
    }

    // Form factor badge pill
    private var formFactorBadge: some View {
        Text(session.formFactor)
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(Sim.Color.accent)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Sim.Color.accent.opacity(0.1))
            .clipShape(Capsule())
    }
}
