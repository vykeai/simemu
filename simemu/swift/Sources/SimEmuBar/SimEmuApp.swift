import SwiftUI
import AppKit
import Foundation

// ============================================================================
// MARK: - Color Palette
// ============================================================================

enum Sim {
    enum Color {
        static let background    = SwiftUI.Color(hex: "#131620")
        static let surface       = SwiftUI.Color(hex: "#1a1d2e")
        static let surfaceHigh   = SwiftUI.Color(hex: "#242838")
        static let active        = SwiftUI.Color(hex: "#34D399")
        static let idle          = SwiftUI.Color(hex: "#FBBF24")
        static let parked        = SwiftUI.Color(hex: "#6B7280")
        static let accent        = SwiftUI.Color(hex: "#60A5FA")
        static let ios           = SwiftUI.Color(hex: "#818CF8")
        static let android       = SwiftUI.Color(hex: "#34D399")
        static let textPrimary   = SwiftUI.Color.white.opacity(0.90)
        static let textSecondary = SwiftUI.Color.white.opacity(0.55)
        static let textMuted     = SwiftUI.Color.white.opacity(0.35)
        static let danger        = SwiftUI.Color(hex: "#F87171")
    }
    enum Gradient {
        static let bg = RadialGradient(
            colors: [SwiftUI.Color(hex: "#1a1840").opacity(0.7), Sim.Color.background],
            center: .top, startRadius: 0, endRadius: 400
        )
    }
}

extension SwiftUI.Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: .init(charactersIn: "#"))
        let val = UInt64(hex, radix: 16) ?? 0
        self.init(
            red:   Double((val >> 16) & 0xFF) / 255,
            green: Double((val >> 8)  & 0xFF) / 255,
            blue:  Double( val        & 0xFF) / 255
        )
    }
}

// ============================================================================
// MARK: - Data Model
// ============================================================================

struct SimSession: Identifiable {
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
    let simId: String

    // T-005: Smart project name — never show raw pid-XXXXX
    var project: String {
        let a = agent
        if a.isEmpty || a.hasPrefix("pid-") {
            // Extract from label first word
            let first = label.components(separatedBy: " ").first ?? ""
            if !first.isEmpty && !first.hasPrefix("T-") && !first.hasPrefix("t0") {
                return first
            }
            // Try label for known project names
            for name in ["goala", "sitches", "fitkind", "vivii", "univiirse", "up2much"] {
                if label.lowercased().contains(name) { return name }
            }
            return a.isEmpty ? "?" : String(a.prefix(8))
        }
        return a
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

    var borderWidth: CGFloat { status == "active" ? 1.2 : status == "idle" ? 0.8 : 0.5 }
    var glowRadius: CGFloat { status == "active" ? 6 : status == "idle" ? 3 : 0 }
    var glowColor: SwiftUI.Color {
        status == "active" ? Sim.Color.active.opacity(0.12) :
        status == "idle" ? Sim.Color.idle.opacity(0.06) : .clear
    }
    var platformColor: SwiftUI.Color { platform == "android" ? Sim.Color.android : Sim.Color.ios }

    var idleText: String {
        guard let hb = heartbeatAt else { return "" }
        let s = Int(Date().timeIntervalSince(hb))
        if s < 60 { return "\(s)s" }
        let m = s / 60
        if m < 60 { return "\(m)m" }
        return "\(m/60)h\(m%60)m"
    }

    var expiresText: String {
        guard let exp = expiresAt else { return "" }
        let s = Int(exp.timeIntervalSince(Date()))
        guard s > 0 else { return "expired" }
        let m = s / 60
        if m < 60 { return "\(m)m" }
        return "\(m/60)h\(m%60)m"
    }

    // T-003: headless — caller passes this in, don't load config per-session
    var isHeadless: Bool = false
}

struct SimConfig {
    var windowMode: String = "default"
    var memoryBudgetMB: Int = 16384
    var memoryBudgetGB: Int {
        get { memoryBudgetMB / 1024 }
        set { memoryBudgetMB = newValue * 1024 }
    }

    static func load() -> SimConfig {
        let f = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".simemu/config.json")
        guard let d = try? Data(contentsOf: f),
              let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return SimConfig() }
        var c = SimConfig()
        if let m = j["window_mode"] as? String { c.windowMode = m }
        if let b = j["memory_budget_mb"] as? Int { c.memoryBudgetMB = b }
        return c
    }

    func save() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let dir = home.appendingPathComponent(".simemu")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let f = dir.appendingPathComponent("config.json")
        var existing: [String: Any] = [:]
        if let d = try? Data(contentsOf: f),
           let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] { existing = j }
        existing["window_mode"] = windowMode
        existing["memory_budget_mb"] = memoryBudgetMB
        if let d = try? JSONSerialization.data(withJSONObject: existing, options: [.prettyPrinted, .sortedKeys]) {
            try? d.write(to: f, options: .atomic)
        }
    }
}

// ============================================================================
// MARK: - Data Loading
// ============================================================================

func loadSessions() -> [SimSession] {
    let f = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".simemu/sessions.json")
    guard let d = try? Data(contentsOf: f),
          let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
          let sessions = j["sessions"] as? [String: [String: Any]] else { return [] }

    let iso = ISO8601DateFormatter()
    iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let isoB = ISO8601DateFormatter()
    isoB.formatOptions = [.withInternetDateTime]
    func pd(_ v: Any?) -> Date? {
        guard let s = v as? String else { return nil }
        return iso.date(from: s) ?? isoB.date(from: s)
    }

    var result: [SimSession] = []
    for (sid, raw) in sessions {
        let st = raw["status"] as? String ?? ""
        guard ["active", "idle", "parked"].contains(st) else { continue }
        result.append(SimSession(
            id: sid,
            platform: raw["platform"] as? String ?? "?",
            formFactor: raw["form_factor"] as? String ?? "phone",
            status: st,
            label: raw["label"] as? String ?? "",
            agent: raw["agent"] as? String ?? "",
            createdAt: pd(raw["created_at"]),
            heartbeatAt: pd(raw["heartbeat_at"]),
            expiresAt: pd(raw["expires_at"]),
            osVersion: raw["resolved_os_version"] as? String ?? "",
            deviceName: raw["device_name"] as? String ?? "",
            simId: raw["sim_id"] as? String ?? ""
        ))
    }
    let headless = SimConfig.load().windowMode == "hidden"
    for i in result.indices { result[i].isHeadless = headless }

    let order: [String: Int] = ["active": 0, "idle": 1, "parked": 2]
    result.sort { a, b in
        let oa = order[a.status] ?? 9
        let ob = order[b.status] ?? 9
        if oa != ob { return oa < ob }
        return (a.heartbeatAt ?? .distantPast) > (b.heartbeatAt ?? .distantPast)
    }
    return result
}

// ============================================================================
// MARK: - App Entry Point
// ============================================================================

@main
enum SimEmuBarApp {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let myPID = ProcessInfo.processInfo.processIdentifier
        NSWorkspace.shared.runningApplications
            .filter { $0.localizedName == "SimEmuBar" && $0.processIdentifier != myPID }
            .forEach { $0.terminate() }
        let c = MenuBarController()
        withExtendedLifetime(c) { app.run() }
    }
}

// ============================================================================
// MARK: - Menu Bar Controller
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
            updateLabel()
        }

        popover.contentSize = NSSize(width: 620, height: 600)
        popover.behavior = .transient
        popover.appearance = NSAppearance(named: .darkAqua)

        DispatchQueue.main.async { [self] in
            let hc = NSHostingController(rootView: SimEmuPanel().frame(width: 620))
            hc.view.layer?.backgroundColor = NSColor.clear.cgColor
            self.popover.contentViewController = hc
        }

        Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.updateLabel()
        }
    }

    // T-007: Better menu bar label
    private func updateLabel() {
        let sessions = loadSessions()
        let active = sessions.filter { $0.status == "active" }.count
        let booted = sessions.filter { $0.status != "parked" }.count

        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "iphone", accessibilityDescription: "SimEmu")
            button.imagePosition = .imageLeading
            if booted == 0 && !sessions.isEmpty {
                // T-007: show moon icon instead of cryptic "pk"
                button.image = NSImage(systemSymbolName: "moon.zzz", accessibilityDescription: "Parked")
                button.title = ""
            } else if booted == 0 {
                button.title = ""
            } else {
                button.title = "\(booted)"
                if active > 0 {
                    button.contentTintColor = NSColor(Sim.Color.active)
                } else {
                    button.contentTintColor = NSColor(Sim.Color.idle)
                }
            }
        }
    }

    @objc func togglePopover() {
        if let button = statusItem.button {
            if popover.isShown {
                popover.performClose(nil)
            } else {
                let hc = NSHostingController(rootView: SimEmuPanel().frame(width: 620))
                hc.view.layer?.backgroundColor = NSColor.clear.cgColor
                popover.contentViewController = hc
                popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
                updateLabel()
            }
        }
    }
}

// ============================================================================
// MARK: - Panel
// ============================================================================

struct SimEmuPanel: View {
    @State private var sessions: [SimSession]
    @State private var config: SimConfig
    @State private var showSettings = false

    init() {
        _sessions = State(initialValue: loadSessions())
        _config = State(initialValue: SimConfig.load())
    }

    private var active: [SimSession] { sessions.filter { $0.status == "active" } }
    private var idle: [SimSession] { sessions.filter { $0.status == "idle" } }
    private var parked: [SimSession] { sessions.filter { $0.status == "parked" } }
    private var booted: Int { active.count + idle.count }
    private var memGB: Double { Double(booted) * 0.9 }

    var body: some View {
        ZStack {
            Sim.Gradient.bg.ignoresSafeArea()

            VStack(spacing: 0) {
                // ── Header (pinned) ──
                header
                Divider().overlay(Sim.Color.accent.opacity(0.12))
                summary
                Divider().overlay(Sim.Color.accent.opacity(0.06))

                // ── Scrollable content ──
                ScrollView(.vertical, showsIndicators: true) {
                    VStack(spacing: 0) {
                        if sessions.isEmpty {
                            emptyState
                        } else {
                            grid
                        }

                        if showSettings {
                            Divider().overlay(Sim.Color.accent.opacity(0.06)).padding(.vertical, 4)
                            settings
                        }
                    }
                }

                // ── Footer (pinned) ──
                Divider().overlay(Sim.Color.accent.opacity(0.06))
                footer
            }
        }
        .frame(height: 600)
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 8) {
            Text("\u{1F9A4}")
                .font(.system(size: 16))
            Text("simemu")
                .font(.system(size: 14, weight: .bold, design: .rounded))
                .foregroundStyle(Sim.Color.textPrimary)

            Spacer()

            if active.count > 0 {
                pill("\(active.count) active", color: Sim.Color.active)
            }
            if idle.count > 0 {
                pill("\(idle.count) idle", color: Sim.Color.idle)
            }
            if parked.count > 0 {
                pill("\(parked.count) parked", color: Sim.Color.parked)
            }

            Button { showSettings.toggle() } label: {
                Image(systemName: "gearshape")
                    .font(.system(size: 12))
                    .foregroundStyle(showSettings ? Sim.Color.accent : Sim.Color.textSecondary)
                    .frame(width: 24, height: 24)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    // MARK: Summary

    private var summary: some View {
        HStack(spacing: 6) {
            Text("\(booted) of \(sessions.count) booted")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(Sim.Color.textPrimary)
            Text("\u{00B7}").foregroundStyle(Sim.Color.textMuted)
            Text("~\(String(format: "%.1f", memGB)) GB")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(Sim.Color.textSecondary)
            Text("\u{00B7}").foregroundStyle(Sim.Color.textMuted)
            // T-003: headless indicator
            HStack(spacing: 3) {
                Image(systemName: config.windowMode == "hidden" ? "eye.slash" : "eye")
                    .font(.system(size: 9))
                Text(config.windowMode == "hidden" ? "headless" : "visible")
                    .font(.system(size: 10, weight: .medium))
            }
            .foregroundStyle(config.windowMode == "hidden" ? Sim.Color.accent : Sim.Color.textSecondary)

            Spacer()

            let ic = sessions.filter { $0.platform == "ios" }.count
            let ac = sessions.filter { $0.platform == "android" }.count
            if ic > 0 { pill("iOS \(ic)", color: Sim.Color.ios) }
            if ac > 0 { pill("Android \(ac)", color: Sim.Color.android) }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(Sim.Color.surface.opacity(0.5))
    }

    // MARK: Grid

    private var grid: some View {
        let cols = [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)]
        return LazyVGrid(columns: cols, spacing: 8) {
            ForEach(sessions) { s in
                SessionTile(session: s)
            }
        }
        .padding(10)
    }

    // MARK: Empty

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "iphone.slash")
                .font(.system(size: 24))
                .foregroundStyle(Sim.Color.textMuted)
            Text("No sessions")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(Sim.Color.textPrimary)
            Text("simemu claim ios")
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(Sim.Color.textSecondary)
        }
        .padding(30)
    }

    // MARK: Settings

    private var settings: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("SETTINGS")
                .font(.system(size: 9, weight: .bold))
                .kerning(1)
                .foregroundStyle(Sim.Color.textMuted)

            HStack {
                Text("Window Mode")
                    .font(.system(size: 11)).foregroundStyle(Sim.Color.textSecondary)
                Spacer()
                picker(["hidden", "corner", "display", "default"], selected: config.windowMode) { m in
                    config.windowMode = m; config.save()
                }
            }
            HStack {
                Text("Memory Budget")
                    .font(.system(size: 11)).foregroundStyle(Sim.Color.textSecondary)
                Spacer()
                picker(["8 GB", "16 GB", "24 GB", "32 GB"], selected: "\(config.memoryBudgetGB) GB") { l in
                    config.memoryBudgetMB = (Int(l.replacingOccurrences(of: " GB", with: "")) ?? 16) * 1024
                    config.save()
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    // MARK: Footer

    private var footer: some View {
        HStack(spacing: 8) {
            // Refresh
            Button { sessions = loadSessions(); config = SimConfig.load() } label: {
                HStack(spacing: 3) {
                    Image(systemName: "arrow.clockwise").font(.system(size: 9))
                    Text("Refresh").font(.system(size: 10, weight: .medium))
                }
                .foregroundStyle(Sim.Color.textSecondary)
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(Sim.Color.surfaceHigh)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)

            // T-008: Hide All
            Button { hideAllSimulators() } label: {
                HStack(spacing: 3) {
                    Image(systemName: "eye.slash").font(.system(size: 9))
                    Text("Hide All").font(.system(size: 10, weight: .medium))
                }
                .foregroundStyle(Sim.Color.accent.opacity(0.7))
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(Sim.Color.accent.opacity(0.06))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)

            Spacer()

            Text("v0.2.0").font(.system(size: 9)).foregroundStyle(Sim.Color.textMuted)

            Spacer()

            // Quit
            Button { NSApp.terminate(nil) } label: {
                HStack(spacing: 3) {
                    Image(systemName: "xmark.circle").font(.system(size: 9))
                    Text("Quit").font(.system(size: 10, weight: .medium))
                }
                .foregroundStyle(Sim.Color.danger.opacity(0.7))
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(Sim.Color.danger.opacity(0.06))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
    }

    // MARK: Helpers

    private func hideAllSimulators() {
        DispatchQueue.global(qos: .userInitiated).async {
            // Hide iOS Simulator windows
            let script = """
            tell application "System Events"
                if exists process "Simulator" then
                    tell process "Simulator"
                        set miniaturized of every window to true
                    end tell
                end if
            end tell
            """
            var err: NSDictionary?
            NSAppleScript(source: script)?.executeAndReturnError(&err)
        }
    }

    private func pill(_ text: String, color: SwiftUI.Color) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.1))
            .clipShape(Capsule())
    }

    private func picker(_ options: [String], selected: String, onSelect: @escaping (String) -> Void) -> some View {
        Menu {
            ForEach(options, id: \.self) { o in
                Button { onSelect(o) } label: {
                    Text(o == selected ? "\u{2713} \(o)" : "   \(o)")
                }
            }
        } label: {
            HStack(spacing: 3) {
                Text(selected).font(.system(size: 10, weight: .medium)).foregroundStyle(Sim.Color.accent)
                Image(systemName: "chevron.down").font(.system(size: 7, weight: .bold)).foregroundStyle(Sim.Color.textMuted)
            }
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(Sim.Color.surfaceHigh)
            .clipShape(RoundedRectangle(cornerRadius: 5))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }
}

// ============================================================================
// MARK: - Session Tile
// ============================================================================

struct SessionTile: View {
    let session: SimSession

    var body: some View {
        // T-004: Click to focus the simulator window
        Button {
            focusSimulator()
        } label: {
            tileContent
        }
        .buttonStyle(.plain)
        // T-009: Right-click context menu
        .contextMenu {
            if session.platform == "ios" && session.status != "parked" {
                Button("Focus Window") { focusSimulator() }
                Button("Hide Window") { hideSimulator() }
                Divider()
            }
            Button("Copy Session ID") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(session.id, forType: .string)
            }
            Button("Copy simemu do command") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString("simemu do \(session.id) ", forType: .string)
            }
            Divider()
            Button("Release Session") {
                // Copy the release command to clipboard for the user
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString("simemu do \(session.id) done", forType: .string)
            }
        }
    }

    private var tileContent: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Row 1: Project + status dot + headless icon
            HStack(spacing: 4) {
                Text(session.project)
                    .font(.system(size: 14, weight: .bold, design: .rounded))
                    .foregroundStyle(Sim.Color.textPrimary)
                    .lineLimit(1)
                Spacer()
                if session.isHeadless && session.status != "parked" {
                    Image(systemName: "eye.slash")
                        .font(.system(size: 9))
                        .foregroundStyle(Sim.Color.textMuted)
                }
                Circle()
                    .fill(session.statusColor)
                    .frame(width: 8, height: 8)
                    .shadow(color: session.statusColor.opacity(0.5), radius: 3)
            }

            // Row 2: Platform + form factor badges + session ID
            HStack(spacing: 4) {
                badge(session.platform == "android" ? "Android" : "iOS", color: session.platformColor)
                badge(session.formFactor, color: Sim.Color.accent)
                Spacer()
                Text(session.id)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(Sim.Color.textMuted)
            }

            // Row 3: Device name
            if !session.deviceName.isEmpty {
                Text(session.deviceName)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Sim.Color.textPrimary.opacity(0.8))
                    .lineLimit(2)
            }

            // Row 4: OS + status
            HStack(spacing: 0) {
                Text(session.osLabel)
                    .font(.system(size: 11))
                    .foregroundStyle(Sim.Color.textSecondary)

                if session.status == "parked" {
                    Text("  \u{00B7}  parked \u{00B7} boots on do")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(Sim.Color.parked)
                } else {
                    let idle = session.idleText
                    let exp = session.expiresText
                    if !idle.isEmpty {
                        Text("  \u{00B7}  idle \(idle)")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(session.statusColor.opacity(0.8))
                    }
                    if !exp.isEmpty {
                        Text("  \u{00B7}  exp \(exp)")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(session.statusColor.opacity(0.6))
                    }
                }
            }

            // Row 5: Label
            if !session.label.isEmpty {
                Text(session.label)
                    .font(.system(size: 11))
                    .foregroundStyle(Sim.Color.textSecondary.opacity(0.8))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(11)
        .background(Sim.Color.surfaceHigh)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(session.statusColor.opacity(session.status == "parked" ? 0.15 : 0.4),
                              lineWidth: session.borderWidth)
        )
        .shadow(color: session.glowColor, radius: session.glowRadius)
    }

    private func badge(_ text: String, color: SwiftUI.Color) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.12))
            .clipShape(Capsule())
    }

    private func focusSimulator() {
        guard session.status != "parked" else { return }
        guard session.platform == "ios" else { return }
        let name = session.deviceName
        guard !name.isEmpty else { return }

        DispatchQueue.global(qos: .userInitiated).async {
            // First unminiaturize, then raise
            let script = """
            tell application "Simulator" to activate
            delay 0.3
            tell application "System Events"
                tell process "Simulator"
                    try
                        set w to first window whose name contains "\(name)"
                        set miniaturized of w to false
                        perform action "AXRaise" of w
                    end try
                end tell
            end tell
            """
            var err: NSDictionary?
            NSAppleScript(source: script)?.executeAndReturnError(&err)
        }
    }

    private func hideSimulator() {
        let name = session.deviceName
        guard !name.isEmpty else { return }

        DispatchQueue.global(qos: .utility).async {
            let script = """
            tell application "System Events"
                tell process "Simulator"
                    try
                        set miniaturized of (first window whose name contains "\(name)") to true
                    end try
                end tell
            end tell
            """
            var err: NSDictionary?
            NSAppleScript(source: script)?.executeAndReturnError(&err)
        }
    }

}
