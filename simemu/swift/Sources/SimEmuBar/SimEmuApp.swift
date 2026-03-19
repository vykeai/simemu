import SwiftUI
import Foundation

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
            Label("sim", systemImage: "iphone")
        }
        .menuBarExtraStyle(.menu)
    }
}

/// Reads state fresh from disk every time the menu opens — no ObservableObject,
/// no @Published, no background polling. Just file reads.
struct SimEmuMenu: View {
    @State private var sessions: [(id: String, platform: String, status: String, label: String)] = []
    @State private var sessionCount = 0
    @State private var windowMode = "default"

    var body: some View {
        Section("Sessions — \(sessionCount)") {
            if sessions.isEmpty {
                Text("No active sessions")
            } else {
                ForEach(sessions, id: \.id) { s in
                    let icon = s.status == "active" ? "circle.fill" : "circle"
                    let color: Color = s.status == "active" ? .green : (s.status == "idle" ? .yellow : .gray)
                    Label {
                        Text("\(s.id)  \(s.platform)  \(s.label)")
                    } icon: {
                        Image(systemName: icon)
                            .foregroundStyle(color)
                    }
                }
            }
        }

        Divider()

        Section {
            Text("Window Mode: \(windowMode)")
                .foregroundStyle(.secondary)
            Button("Hide All Windows") {
                DispatchQueue.global(qos: .utility).async {
                    let proc = Process()
                    proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
                    proc.arguments = ["-lc", "python3 -c \"from simemu.window import apply_to_all; apply_to_all()\""]
                    proc.standardOutput = FileHandle.nullDevice
                    proc.standardError = FileHandle.nullDevice
                    try? proc.run()
                }
            }
        }

        Divider()

        Button("Quit SimEmuBar") { NSApp.terminate(nil) }
    }

    init() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let stateDir = home.appendingPathComponent(".simemu")

        // Read sessions.json
        var loaded: [(id: String, platform: String, status: String, label: String)] = []
        let sessionsFile = stateDir.appendingPathComponent("sessions.json")
        if let data = try? Data(contentsOf: sessionsFile),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let sessions = json["sessions"] as? [String: [String: Any]] {
            for (sid, raw) in sessions.sorted(by: { $0.key < $1.key }) {
                let status = raw["status"] as? String ?? ""
                guard ["active", "idle", "parked"].contains(status) else { continue }
                loaded.append((
                    id: sid,
                    platform: raw["platform"] as? String ?? "?",
                    status: status,
                    label: (raw["label"] as? String ?? "").prefix(25).description
                ))
            }
        }
        _sessions = State(initialValue: loaded)
        _sessionCount = State(initialValue: loaded.count)

        // Read config
        let configFile = stateDir.appendingPathComponent("config.json")
        if let data = try? Data(contentsOf: configFile),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let mode = json["window_mode"] as? String {
            _windowMode = State(initialValue: mode)
        }
    }
}
