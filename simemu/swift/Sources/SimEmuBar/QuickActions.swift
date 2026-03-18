import SwiftUI

struct QuickActions: View {
    @ObservedObject var state: SimEmuState

    var body: some View {
        VStack(spacing: 2) {
            actionButton(
                icon: state.maintenanceActive ? "lock.fill" : "lock.open.fill",
                label: "Maintenance: \(state.maintenanceActive ? "ON" : "OFF")",
                tint: state.maintenanceActive ? Design.dotAmber : Design.textMuted
            ) {
                state.toggleMaintenance()
            }

            actionButton(
                icon: "xmark.circle.fill",
                label: "Kill All Emulators",
                tint: Design.dotRed
            ) {
                state.killAll()
            }

            actionButton(
                icon: "arrow.clockwise",
                label: "Refresh",
                tint: Design.textMuted
            ) {
                state.refresh()
            }

            Divider().overlay(Design.cardBorder)

            actionButton(
                icon: "power",
                label: "Quit SimEmuBar  (⇧⌘⎋)",
                tint: Design.textMuted.opacity(0.6)
            ) {
                NSApplication.shared.terminate(nil)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private func actionButton(
        icon: String,
        label: String,
        tint: Color,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 12))
                    .foregroundStyle(tint)
                    .frame(width: 16)

                Text(label)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Design.textPrimary)

                Spacer()
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.white.opacity(0.001))
        )
        .onHover { hovering in
            // SwiftUI handles hover automatically with buttonStyle
        }
    }
}
