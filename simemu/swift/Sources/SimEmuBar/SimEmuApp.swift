import SwiftUI

@main
struct SimEmuBarApp: App {
    @State private var state = SimEmuState()

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
            SimEmuMenu(state: state)
        } label: {
            Label(state.menuBarTitle, systemImage: "iphone")
        }
    }
}

struct SimEmuMenu: View {
    let state: SimEmuState

    var body: some View {
        Section("Sessions — \(state.allocations.count)") {
            if state.allocations.isEmpty {
                Text("No active sessions")
            } else {
                ForEach(state.allocations) { alloc in
                    let icon = alloc.isBooted ? "circle.fill" : "circle"
                    let color: Color = alloc.isBooted ? .green : .gray
                    Label {
                        Text("\(alloc.slug)  \(alloc.memoryText)")
                    } icon: {
                        Image(systemName: icon)
                            .foregroundStyle(color)
                    }
                }
            }
        }

        Divider()

        Section("Memory: \(state.menuBarTitle)") {
            Button("Refresh") { state.refresh() }

            if state.maintenanceActive {
                Label("Maintenance ON", systemImage: "wrench.fill")
            }

            Button("Toggle Maintenance") { state.toggleMaintenance() }
        }

        Divider()

        Section {
            Button("Window Mode: \(windowModeLabel)") {}
                .disabled(true)
            Button("Hide All Windows") { hideAllWindows() }
        }

        Divider()

        Button("Quit SimEmuBar") { NSApp.terminate(nil) }
    }

    private var windowModeLabel: String {
        let path = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".simemu/config.json")
        guard let data = try? Data(contentsOf: path),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let mode = json["window_mode"] as? String
        else { return "default" }
        return mode
    }

    private func hideAllWindows() {
        let home = ProcessInfo.processInfo.environment["HOME"] ?? NSHomeDirectory()
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        proc.arguments = ["-lc", "python3 -c \"from simemu.window import apply_to_all; apply_to_all()\""]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
    }
}
