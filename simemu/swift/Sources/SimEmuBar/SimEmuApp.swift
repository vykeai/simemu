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
    let isVisible: Bool  // from sessions.json "visible" field

    // T-005: Smart project name -- never show raw pid-XXXXX
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

    var statusLabel: String {
        switch status {
        case "active": return "ACTIVE"
        case "idle":   return "IDLE"
        default:       return "PARKED"
        }
    }

    var borderWidth: CGFloat { status == "active" ? 1.2 : status == "idle" ? 0.8 : 0.5 }
    var glowRadius: CGFloat { status == "active" ? 6 : status == "idle" ? 3 : 0 }
    var glowColor: SwiftUI.Color {
        status == "active" ? Sim.Color.active.opacity(0.12) :
        status == "idle" ? Sim.Color.idle.opacity(0.06) : .clear
    }
    var platformColor: SwiftUI.Color { platform == "android" ? Sim.Color.android : Sim.Color.ios }

    var deviceIcon: String {
        if platform == "android" { return "" } // handled with emoji
        switch formFactor.lowercased() {
        case "tablet", "ipad":   return "ipad"
        case "watch":            return "applewatch"
        case "tv":               return "appletv"
        case "vision":           return "visionpro"
        default:                 return "iphone"
        }
    }

    var isAndroid: Bool { platform == "android" }

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

    // Sort priority within a platform group: phone=0, tablet=1, watch=2, tv=3, vision=4, other=5
    var formFactorOrder: Int {
        switch formFactor.lowercased() {
        case "phone":                    return 0
        case "tablet", "ipad":           return 1
        case "watch":                    return 2
        case "tv":                       return 3
        case "vision":                   return 4
        default:                         return 5
        }
    }

    var isHeadless: Bool { status != "parked" && !isVisible }
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
            simId: raw["sim_id"] as? String ?? "",
            isVisible: raw["visible"] as? Bool ?? false
        ))
    }
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

final class MenuBarController: NSObject, NSPopoverDelegate {
    private var statusItem: NSStatusItem
    private var popover: NSPopover
    private var outsideClickMonitor: Any?
    private var localClickMonitor: Any?
    private var dirWatchSource: DispatchSourceFileSystemObject?
    private var debounceWork: DispatchWorkItem?
    private var fallbackTimer: Timer?

    private static let panelWidth: CGFloat = 640

    override init() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        popover = NSPopover()
        super.init()

        if let button = statusItem.button {
            button.action = #selector(togglePopover)
            button.target = self
            updateLabel()
        }

        let sessions = loadSessions()
        popover.contentSize = NSSize(
            width: Self.panelWidth,
            height: SimEmuPanel.preferredHeight(for: sessions.count)
        )
        popover.behavior = .transient
        popover.appearance = NSAppearance(named: .darkAqua)
        popover.delegate = self

        DispatchQueue.main.async { [self] in
            let hc = self.makeHostingController(sessionCount: sessions.count)
            hc.view.layer?.backgroundColor = NSColor.clear.cgColor
            self.popover.contentViewController = hc
        }

        startFileWatch()

        // Fallback: poll every 30s in case the file watch misses events
        fallbackTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            self?.onSessionsChanged()
        }
    }

    // MARK: - File Watch (DispatchSource on ~/.simemu/ directory)
    // Watch the directory, not the file — atomic writes (tmp.replace) change the inode.

    private func startFileWatch() {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".simemu")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let fd = open(dir.path, O_EVTONLY)
        guard fd >= 0 else { return }

        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd,
            eventMask: [.write, .rename, .delete],
            queue: .main
        )
        source.setEventHandler { [weak self] in
            self?.debouncedRefresh()
        }
        source.setCancelHandler {
            close(fd)
        }
        source.resume()
        dirWatchSource = source
    }

    private func debouncedRefresh() {
        debounceWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            self?.onSessionsChanged()
        }
        debounceWork = work
        // Debounce 200ms — atomic writes generate multiple events
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2, execute: work)
    }

    private func onSessionsChanged() {
        updateLabel()
        // If popover is open, update its content live
        if popover.isShown {
            let sessions = loadSessions()
            let hc = makeHostingController(sessionCount: sessions.count)
            hc.view.layer?.backgroundColor = NSColor.clear.cgColor
            popover.contentViewController = hc
            popover.contentSize = NSSize(
                width: Self.panelWidth,
                height: SimEmuPanel.preferredHeight(for: sessions.count)
            )
        }
    }

    // T-007: Better menu bar label
    private func updateLabel() {
        let sessions = loadSessions()
        let active = sessions.filter { $0.status == "active" }.count
        let booted = sessions.filter { $0.status != "parked" }.count

        if let button = statusItem.button {
            func templateIcon(_ name: String) -> NSImage? {
                let img = NSImage(systemSymbolName: name, accessibilityDescription: "SimEmu")
                img?.isTemplate = true
                return img
            }

            button.image = templateIcon("iphone")
            button.imagePosition = .imageLeading
            button.contentTintColor = nil  // reset — let system handle color

            if booted == 0 && !sessions.isEmpty {
                button.image = templateIcon("moon.zzz")
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
                closePopover()
            } else {
                let sessions = loadSessions()
                popover.contentSize = NSSize(
                    width: Self.panelWidth,
                    height: SimEmuPanel.preferredHeight(for: sessions.count)
                )
                let hc = makeHostingController(sessionCount: sessions.count)
                hc.view.layer?.backgroundColor = NSColor.clear.cgColor
                popover.contentViewController = hc
                popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
                installOutsideClickMonitor()
                updateLabel()
            }
        }
    }

    private func closePopover() {
        popover.performClose(nil)
        removeOutsideClickMonitor()
    }

    private func makeHostingController(sessionCount: Int) -> NSHostingController<AnyView> {
        let preferredHeight = SimEmuPanel.preferredHeight(for: sessionCount)
        let root = SimEmuPanel(sessionCount: sessionCount) { [weak self] nextHeight in
            DispatchQueue.main.async {
                self?.updatePopoverHeight(nextHeight)
            }
        }
        .frame(width: Self.panelWidth, height: preferredHeight)
        .background(Sim.Color.background)
        let controller = NSHostingController(rootView: AnyView(root))
        controller.view.wantsLayer = true
        controller.view.layer?.backgroundColor = NSColor(Sim.Color.background).cgColor
        controller.view.frame = NSRect(x: 0, y: 0, width: Self.panelWidth, height: preferredHeight)
        return controller
    }

    private func updatePopoverHeight(_ preferredHeight: CGFloat) {
        let nextSize = NSSize(width: Self.panelWidth, height: preferredHeight)
        guard popover.contentSize != nextSize else { return }
        popover.contentSize = nextSize
        popover.contentViewController?.view.frame = NSRect(
            x: 0,
            y: 0,
            width: Self.panelWidth,
            height: preferredHeight
        )
    }

    private func installOutsideClickMonitor() {
        removeOutsideClickMonitor()
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, self.popover.isShown else { return }
                self.closePopover()
            }
        }
        localClickMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] event in
            guard let self, self.popover.isShown else { return event }
            let popoverWindow = self.popover.contentViewController?.view.window
            let statusWindow = self.statusItem.button?.window
            if event.window !== popoverWindow && event.window !== statusWindow {
                self.closePopover()
            }
            return event
        }
    }

    private func removeOutsideClickMonitor() {
        if let outsideClickMonitor {
            NSEvent.removeMonitor(outsideClickMonitor)
            self.outsideClickMonitor = nil
        }
        if let localClickMonitor {
            NSEvent.removeMonitor(localClickMonitor)
            self.localClickMonitor = nil
        }
    }

    func popoverDidClose(_ notification: Notification) {
        removeOutsideClickMonitor()
    }
}

// ============================================================================
// MARK: - Panel
// ============================================================================

struct SimEmuPanel: View {
    @State private var sessions: [SimSession]
    @State private var config: SimConfig
    @State private var showSettings = false
    @State private var panelHeight: CGFloat
    private let initialSessionCount: Int
    private let onPreferredHeightChange: ((CGFloat) -> Void)?
    private static let minPanelHeight: CGFloat = 360
    private static let maxPanelHeight: CGFloat = 920

    init(sessionCount: Int? = nil, onPreferredHeightChange: ((CGFloat) -> Void)? = nil) {
        let count = sessionCount ?? loadSessions().count
        initialSessionCount = count
        _sessions = State(initialValue: loadSessions())
        _config = State(initialValue: SimConfig.load())
        _panelHeight = State(initialValue: Self.preferredHeight(for: count))
        self.onPreferredHeightChange = onPreferredHeightChange
    }

    static func preferredHeight(for sessionCount: Int) -> CGFloat {
        let base: CGFloat = 260
        let perRow: CGFloat = 150
        let rows = max(1, Int(ceil(Double(sessionCount) / 2.0)))
        return min(max(base + CGFloat(rows) * perRow, minPanelHeight), maxPanelHeight)
    }

    private var active: [SimSession] { sessions.filter { $0.status == "active" } }
    private var idle: [SimSession] { sessions.filter { $0.status == "idle" } }
    private var parked: [SimSession] { sessions.filter { $0.status == "parked" } }
    private var booted: Int { active.count + idle.count }
    private var visibleCount: Int { sessions.filter { $0.status != "parked" && $0.isVisible }.count }
    private var headlessCount: Int { sessions.filter { $0.isHeadless }.count }
    private var iosPhoneCount: Int {
        sessions.filter { $0.platform != "android" && $0.status != "parked" && $0.formFactor == "phone" }.count
    }
    private var iosTabletCount: Int {
        sessions.filter { $0.platform != "android" && $0.status != "parked" && $0.formFactor == "tablet" }.count
    }
    private var androidCount: Int {
        sessions.filter { $0.platform == "android" && $0.status != "parked" }.count
    }

    private var iosSessions: [SimSession] {
        sessions.filter { $0.platform != "android" }
            .sorted { a, b in
                let oa = statusOrder(a.status)
                let ob = statusOrder(b.status)
                if oa != ob { return oa < ob }
                if a.formFactorOrder != b.formFactorOrder { return a.formFactorOrder < b.formFactorOrder }
                return (a.heartbeatAt ?? .distantPast) > (b.heartbeatAt ?? .distantPast)
            }
    }
    private var androidSessions: [SimSession] {
        sessions.filter { $0.platform == "android" }
            .sorted { a, b in
                let oa = statusOrder(a.status)
                let ob = statusOrder(b.status)
                if oa != ob { return oa < ob }
                if a.formFactorOrder != b.formFactorOrder { return a.formFactorOrder < b.formFactorOrder }
                return (a.heartbeatAt ?? .distantPast) > (b.heartbeatAt ?? .distantPast)
            }
    }

    private func statusOrder(_ s: String) -> Int {
        switch s { case "active": return 0; case "idle": return 1; default: return 2 }
    }

    private var allParked: Bool {
        !sessions.isEmpty && sessions.allSatisfy { $0.status == "parked" }
    }

    private var dynamicPreferredHeight: CGFloat {
        let headerBlock: CGFloat = 118
        let footerBlock: CGFloat = 58
        let sectionHeaderHeight: CGFloat = 34
        let parkedHeaderHeight: CGFloat = parked.isEmpty || allParked ? 0 : 42
        let settingsHeight: CGFloat = showSettings ? 112 : 0
        let iosActiveRows = iosSessions.filter { $0.status != "parked" }.chunkedCount(size: 2)
        let androidActiveRows = androidSessions.filter { $0.status != "parked" }.chunkedCount(size: 2)
        let parkedRows = parked.chunkedCount(size: 2)
        let tileRowHeight: CGFloat = 152
        let verticalPadding: CGFloat = 32
        var total = headerBlock + footerBlock + settingsHeight + verticalPadding
        if sessions.isEmpty || allParked {
            total += 220
        } else {
            if iosActiveRows > 0 {
                total += sectionHeaderHeight + CGFloat(iosActiveRows) * tileRowHeight
            }
            if androidActiveRows > 0 {
                total += sectionHeaderHeight + CGFloat(androidActiveRows) * tileRowHeight
            }
            if parkedRows > 0 {
                total += parkedHeaderHeight + CGFloat(parkedRows) * tileRowHeight
            }
        }
        return min(max(total, Self.minPanelHeight), Self.maxPanelHeight)
    }

    var body: some View {
        ZStack {
            Sim.Gradient.bg.ignoresSafeArea()

            VStack(spacing: 0) {
                // -- Header (pinned) --
                header
                Divider().overlay(Sim.Color.accent.opacity(0.12))
                summaryBar
                Divider().overlay(Sim.Color.accent.opacity(0.06))

                // -- Scrollable content --
                ScrollView(.vertical, showsIndicators: true) {
                    VStack(spacing: 0) {
                        if sessions.isEmpty {
                            emptyState
                        } else if allParked {
                            allParkedState
                        } else {
                            groupedGrid
                        }

                        // Show parked section after active/idle when not all parked
                        if !allParked && !parked.isEmpty {
                            parkedSection
                        }

                        if showSettings {
                            Divider().overlay(Sim.Color.accent.opacity(0.06)).padding(.vertical, 4)
                            settings
                        }
                    }
                }

                // -- Footer (pinned) --
                Divider().overlay(Sim.Color.accent.opacity(0.06))
                footer
            }
        }
        .frame(height: panelHeight)
        .onAppear { syncPanelHeight() }
        .onChange(of: sessions.count) { _, _ in syncPanelHeight() }
        .onChange(of: showSettings) { _, _ in syncPanelHeight() }
    }

    private func syncPanelHeight() {
        let next = dynamicPreferredHeight
        guard abs(next - panelHeight) > 1 else { return }
        panelHeight = next
        onPreferredHeightChange?(next)
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 8) {
            Text("\u{1F9A4}")
                .font(.system(size: 18))
            Text("simemu")
                .font(.system(size: 16, weight: .bold, design: .rounded))
                .foregroundStyle(Sim.Color.textPrimary)

            Spacer()

            // Live inventory summary
            HStack(spacing: 4) {
                Image(systemName: "square.stack.3d.up")
                    .font(.system(size: 11))
                Text("\(booted) live")
                    .font(.system(size: 12, weight: .semibold))
            }
            .foregroundStyle(Sim.Color.accent)

            if visibleCount > 0 || headlessCount > 0 {
                HStack(spacing: 3) {
                    Image(systemName: visibleCount > 0 ? "eye" : "eye.slash")
                        .font(.system(size: 10))
                    Text("\(visibleCount)/\(headlessCount)")
                        .font(.system(size: 11, weight: .medium))
                }
                .foregroundStyle(Sim.Color.accent.opacity(0.8))
            }

            // Settings gear -- larger hit area
            Button { showSettings.toggle() } label: {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 14))
                    .foregroundStyle(showSettings ? Sim.Color.accent : Sim.Color.textSecondary)
                    .frame(width: 28, height: 28)
                    .background(showSettings ? Sim.Color.accent.opacity(0.1) : .clear)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    // MARK: Summary Bar

    private var summaryBar: some View {
        HStack(spacing: 0) {
            let parts: [String] = {
                var p: [String] = []
                p.append("\(booted) booted")
                if !parked.isEmpty { p.append("\(parked.count) parked") }
                if iosPhoneCount > 0 { p.append("\(iosPhoneCount) iPhone") }
                if iosTabletCount > 0 { p.append("\(iosTabletCount) iPad") }
                if androidCount > 0 { p.append("\(androidCount) Android") }
                if visibleCount > 0 { p.append("\(visibleCount) visible") }
                if headlessCount > 0 { p.append("\(headlessCount) headless") }
                return p
            }()

            ForEach(Array(parts.enumerated()), id: \.offset) { i, part in
                if i > 0 {
                    Text(" \u{00B7} ")
                        .font(.system(size: 12))
                        .foregroundStyle(Sim.Color.textMuted)
                }
                Text(part)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(i == 0 ? Sim.Color.textPrimary : Sim.Color.textSecondary)
            }

            Spacer()

            if active.count > 0 {
                statusPill("ACTIVE", color: Sim.Color.active, count: active.count)
            }
            if idle.count > 0 {
                statusPill("IDLE", color: Sim.Color.idle, count: idle.count)
                    .padding(.leading, 4)
            }
            if parked.count > 0 {
                statusPill("PARKED", color: Sim.Color.parked, count: parked.count)
                    .padding(.leading, 4)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 7)
        .background(Sim.Color.surface.opacity(0.5))
    }

    // MARK: Grouped Grid

    private var groupedGrid: some View {
        let cols = [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)]
        let iosActive = iosSessions.filter { $0.status != "parked" }
        let androidActive = androidSessions.filter { $0.status != "parked" }

        return VStack(spacing: 4) {
            // iOS Section
            if !iosActive.isEmpty {
                sectionHeader(
                    icon: "iphone",
                    title: "iOS",
                    count: iosActive.count,
                    color: Sim.Color.ios,
                    isSystemImage: true
                )
                LazyVGrid(columns: cols, spacing: 8) {
                    ForEach(iosActive) { s in
                        SessionTile(session: s)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.bottom, 8)
            }

            // Android Section
            if !androidActive.isEmpty {
                sectionHeader(
                    icon: "\u{1F916}",
                    title: "Android",
                    count: androidActive.count,
                    color: Sim.Color.android,
                    isSystemImage: false
                )
                LazyVGrid(columns: cols, spacing: 8) {
                    ForEach(androidActive) { s in
                        SessionTile(session: s)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.bottom, 8)
            }
        }
        .padding(.top, 6)
    }

    // MARK: Parked Section (collapsed/compact)

    private var parkedSection: some View {
        let cols = [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)]
        let iosParked = iosSessions.filter { $0.status == "parked" }
        let androidParked = androidSessions.filter { $0.status == "parked" }
        let allParkedSessions = iosParked + androidParked

        return VStack(spacing: 4) {
            Divider().overlay(Sim.Color.accent.opacity(0.06)).padding(.horizontal, 10)

            HStack(spacing: 6) {
                Image(systemName: "moon.zzz")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Sim.Color.parked)
                Text("Parked")
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .foregroundStyle(Sim.Color.textSecondary)
                Text("\(allParkedSessions.count)")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Sim.Color.textMuted)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(Sim.Color.surfaceHigh)
                    .clipShape(Capsule())
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 6)

            LazyVGrid(columns: cols, spacing: 8) {
                ForEach(allParkedSessions) { s in
                    SessionTile(session: s)
                }
            }
            .padding(.horizontal, 10)
            .padding(.bottom, 8)
        }
    }

    // MARK: Section Header

    private func sectionHeader(icon: String, title: String, count: Int, color: SwiftUI.Color, isSystemImage: Bool) -> some View {
        HStack(spacing: 6) {
            if isSystemImage {
                Image(systemName: icon)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(color)
            } else {
                Text(icon)
                    .font(.system(size: 12))
            }
            Text(title)
                .font(.system(size: 13, weight: .bold, design: .rounded))
                .foregroundStyle(color)
            Text("\(count)")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(Sim.Color.textMuted)
                .padding(.horizontal, 6)
                .padding(.vertical, 1)
                .background(Sim.Color.surfaceHigh)
                .clipShape(Capsule())
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
    }

    // MARK: Empty State

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "iphone.slash")
                .font(.system(size: 32))
                .foregroundStyle(Sim.Color.textMuted)
            Text("No sessions")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Sim.Color.textPrimary)
            Text("Start one with:")
                .font(.system(size: 12))
                .foregroundStyle(Sim.Color.textSecondary)
            Text("simemu claim ios")
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .foregroundStyle(Sim.Color.accent)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(Sim.Color.surfaceHigh)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(40)
    }

    // MARK: All Parked State

    private var allParkedState: some View {
        VStack(spacing: 12) {
            Image(systemName: "moon.zzz.fill")
                .font(.system(size: 32))
                .foregroundStyle(Sim.Color.parked)
            Text("All sessions parked")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Sim.Color.textPrimary)
            Text("They'll wake automatically on next command")
                .font(.system(size: 12))
                .foregroundStyle(Sim.Color.textSecondary)
            Text("simemu do <session> boot")
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .foregroundStyle(Sim.Color.accent)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(Sim.Color.surfaceHigh)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(40)
    }

    // MARK: Settings

    private var settings: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("SETTINGS")
                .font(.system(size: 10, weight: .bold))
                .kerning(1)
                .foregroundStyle(Sim.Color.textMuted)

            HStack {
                Text("Window Mode")
                    .font(.system(size: 12)).foregroundStyle(Sim.Color.textSecondary)
                Spacer()
                picker(["hidden", "corner", "display", "default"], selected: config.windowMode) { m in
                    config.windowMode = m; config.save()
                }
            }
            HStack {
                Text("Memory Budget")
                    .font(.system(size: 12)).foregroundStyle(Sim.Color.textSecondary)
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
                HStack(spacing: 4) {
                    Image(systemName: "arrow.clockwise").font(.system(size: 11))
                    Text("Refresh").font(.system(size: 11, weight: .medium))
                }
                .foregroundStyle(Sim.Color.textSecondary)
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(Sim.Color.surfaceHigh)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)

            // T-008: Hide All
            Button { hideAllSimulators() } label: {
                HStack(spacing: 4) {
                    Image(systemName: "eye.slash").font(.system(size: 11))
                    Text("Hide All").font(.system(size: 11, weight: .medium))
                }
                .foregroundStyle(Sim.Color.accent.opacity(0.8))
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(Sim.Color.accent.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)

            // Show All
            Button { showAllSimulators() } label: {
                HStack(spacing: 4) {
                    Image(systemName: "eye").font(.system(size: 11))
                    Text("Show All").font(.system(size: 11, weight: .medium))
                }
                .foregroundStyle(Sim.Color.accent.opacity(0.8))
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(Sim.Color.accent.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)

            Spacer()

            Text("v0.3.0")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(Sim.Color.textMuted)

            // Quit
            Button { NSApp.terminate(nil) } label: {
                HStack(spacing: 4) {
                    Image(systemName: "xmark.circle").font(.system(size: 11))
                    Text("Quit").font(.system(size: 11, weight: .medium))
                }
                .foregroundStyle(Sim.Color.danger.opacity(0.7))
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(Sim.Color.danger.opacity(0.06))
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
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

    private func showAllSimulators() {
        DispatchQueue.global(qos: .userInitiated).async {
            let script = """
            tell application "System Events"
                if exists process "Simulator" then
                    tell process "Simulator"
                        set miniaturized of every window to false
                    end tell
                end if
            end tell
            tell application "Simulator" to activate
            """
            var err: NSDictionary?
            NSAppleScript(source: script)?.executeAndReturnError(&err)
        }
    }

    private func statusPill(_ text: String, color: SwiftUI.Color, count: Int) -> some View {
        Text("\(count) \(text)")
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(color.opacity(0.12))
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
                Text(selected).font(.system(size: 11, weight: .medium)).foregroundStyle(Sim.Color.accent)
                Image(systemName: "chevron.down").font(.system(size: 8, weight: .bold)).foregroundStyle(Sim.Color.textMuted)
            }
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Sim.Color.surfaceHigh)
            .clipShape(RoundedRectangle(cornerRadius: 5))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }
}

private extension Array {
    func chunkedCount(size: Int) -> Int {
        guard size > 0 else { return 0 }
        guard !isEmpty else { return 0 }
        return Int(ceil(Double(count) / Double(size)))
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
        VStack(alignment: .leading, spacing: 5) {
            // Row 1: Device icon + project name + status badge
            HStack(spacing: 6) {
                // Device icon
                if session.isAndroid {
                    Text("\u{1F916}")
                        .font(.system(size: 14))
                } else {
                    Image(systemName: session.deviceIcon)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Sim.Color.ios)
                }

                Text(session.project)
                    .font(.system(size: 15, weight: .bold, design: .rounded))
                    .foregroundStyle(Sim.Color.textPrimary)
                    .lineLimit(1)

                Spacer()

                // Visible/invisible icon
                if session.status != "parked" {
                    Image(systemName: session.isVisible ? "eye" : "eye.slash")
                        .font(.system(size: 10))
                        .foregroundStyle(session.isVisible ? Sim.Color.active.opacity(0.6) : Sim.Color.textMuted)
                }

                // Status pill/badge
                statusBadge
            }

            // Row 2: Device name
            if !session.deviceName.isEmpty {
                Text(session.deviceName)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Sim.Color.textPrimary.opacity(0.8))
                    .lineLimit(1)
            }

            // Row 3: OS version + idle/expiry info
            HStack(spacing: 0) {
                Text(session.osLabel)
                    .font(.system(size: 12))
                    .foregroundStyle(Sim.Color.textSecondary)

                if session.status != "parked" {
                    Text(session.isVisible ? "  ·  visible" : "  ·  headless")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(session.isVisible ? Sim.Color.active.opacity(0.75) : Sim.Color.textMuted)
                }

                if session.status == "parked" {
                    Text("  \u{00B7}  boots on do")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Sim.Color.parked)
                } else {
                    let idleStr = session.idleText
                    let expStr = session.expiresText
                    if !idleStr.isEmpty {
                        Text("  \u{00B7}  idle \(idleStr)")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(session.statusColor.opacity(0.8))
                    }
                    if !expStr.isEmpty {
                        Text("  \u{00B7}  expires \(expStr)")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(session.statusColor.opacity(0.6))
                    }
                }
            }

            // Row 4: Label with pin
            if !session.label.isEmpty {
                HStack(spacing: 4) {
                    Text("\u{1F4CC}")
                        .font(.system(size: 9))
                    Text(session.label)
                        .font(.system(size: 11))
                        .foregroundStyle(Sim.Color.textSecondary.opacity(0.75))
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            // Row 5: Session ID -- bottom right, very small and muted
            HStack {
                Spacer()
                Text(session.id)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(Sim.Color.textPrimary.opacity(0.30))
            }
        }
        .padding(12)
        .background(Sim.Color.surfaceHigh)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(session.statusColor.opacity(session.status == "parked" ? 0.15 : 0.4),
                              lineWidth: session.borderWidth)
        )
        .shadow(color: session.glowColor, radius: session.glowRadius)
    }

    // MARK: Status Badge

    private var statusBadge: some View {
        Text(session.statusLabel)
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(session.statusColor)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(session.statusColor.opacity(0.15))
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
