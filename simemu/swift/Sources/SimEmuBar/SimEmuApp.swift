import AppKit
import SwiftUI

@main
enum SimEmuBarApp {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let controller = MenuBarController()
        withExtendedLifetime(controller) {
            app.run()
        }
    }
}

final class MenuBarController {
    private var statusItem: NSStatusItem
    private var popover: NSPopover
    private var state: SimEmuState?
    private var refreshTimer: Timer?
    private var globalMonitor: Any?
    private var localMonitor: Any?

    init() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        popover = NSPopover()

        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "iphone", accessibilityDescription: "SimEmu")
            button.title = " idle"
            button.imagePosition = .imageLeading
            button.action = #selector(togglePopover)
            button.target = self
        }

        // Defer state + popover setup — SimEmuState init can crash on macOS 26
        // if run before the run loop is active (LureFact/Observable issue)
        DispatchQueue.main.async { [self] in
            let s = SimEmuState()
            self.state = s
            self.popover.contentSize = NSSize(width: 320, height: 420)
            self.popover.behavior = .transient
            self.popover.contentViewController = NSHostingController(
                rootView: MainView(state: s)
                    .frame(width: 320)
            )
        }

        localMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.keyCode == 53 {
                self?.closePopover()
                return nil
            }
            return event
        }

        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.keyCode == 53
                && event.modifierFlags.contains(.command)
                && event.modifierFlags.contains(.shift)
            {
                DispatchQueue.main.async {
                    self?.closePopover()
                    NSApplication.shared.terminate(nil)
                }
            }
        }

        refreshTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.updateTitle()
        }
        updateTitle()
    }

    deinit {
        if let m = globalMonitor { NSEvent.removeMonitor(m) }
        if let m = localMonitor { NSEvent.removeMonitor(m) }
    }

    @objc private func togglePopover() {
        state?.refresh()
        if let button = statusItem.button {
            if popover.isShown {
                popover.performClose(nil)
            } else {
                popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
                popover.contentViewController?.view.window?.makeKey()
            }
        }
    }

    private func closePopover() {
        if popover.isShown {
            popover.performClose(nil)
        }
    }

    private func updateTitle() {
        guard let button = statusItem.button else { return }
        let mb = state?.totalMemoryMB ?? 0
        if mb < 100 {
            button.title = " idle"
            button.contentTintColor = nil
        } else if mb >= 16384 {
            button.title = " " + formatMB(mb)
            button.contentTintColor = .systemRed
        } else if mb >= 8192 {
            button.title = " " + formatMB(mb)
            button.contentTintColor = .systemOrange
        } else if mb >= 4096 {
            button.title = " " + formatMB(mb)
            button.contentTintColor = .systemYellow
        } else {
            button.title = " " + formatMB(mb)
            button.contentTintColor = nil
        }
    }

    private func formatMB(_ mb: Double) -> String {
        if mb >= 1024 { return String(format: "%.1f GB", mb / 1024) }
        return String(format: "%.0f MB", mb)
    }
}
