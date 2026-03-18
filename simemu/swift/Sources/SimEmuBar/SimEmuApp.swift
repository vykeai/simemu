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
            VStack(alignment: .leading, spacing: 8) {
                Text("SimEmu")
                    .font(.headline)
                Text("Sessions: \(state.allocations.count)")
                Text("Memory: \(state.menuBarTitle)")
                Divider()
                Button("Refresh") { state.refresh() }
                Button("Quit") { NSApp.terminate(nil) }
            }
            .padding()
            .frame(width: 240)
            .onAppear { state.startPolling() }
        } label: {
            Label {
                Text(state.menuBarTitle)
                    .monospacedDigit()
            } icon: {
                Image(systemName: "iphone")
            }
        }
        .menuBarExtraStyle(.window)
    }
}
