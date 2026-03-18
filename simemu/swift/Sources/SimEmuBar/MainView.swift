import SwiftUI

struct MainView: View {
    @ObservedObject var state: SimEmuState

    var body: some View {
        VStack(spacing: 0) {
            // Dismiss bar — always visible, big and obvious
            HStack {
                Spacer()
                Text("ESC")
                    .font(.system(size: 14, weight: .black, design: .monospaced))
                    .foregroundStyle(Design.textPrimary.opacity(0.5))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 3)
                    .background(
                        RoundedRectangle(cornerRadius: 5)
                            .strokeBorder(Design.textPrimary.opacity(0.25), lineWidth: 1.5)
                    )
                Text("to close")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Design.textMuted.opacity(0.5))
                Text("·")
                    .foregroundStyle(Design.textMuted.opacity(0.3))
                Text("⇧⌘⎋")
                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                    .foregroundStyle(Design.textMuted.opacity(0.5))
                Text("to quit")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Design.textMuted.opacity(0.5))
                Spacer()
            }
            .padding(.vertical, 6)
            .background(Design.bgDark.opacity(0.8))

            MemoryHeader(state: state)

            Divider().overlay(Design.cardBorder)

            if state.allocations.isEmpty {
                emptyState
            } else {
                allocationsList
            }

            Divider().overlay(Design.cardBorder)

            LureCard(fact: state.lureFact) {
                state.cycleLureFact()
            }

            Divider().overlay(Design.cardBorder)

            QuickActions(state: state)
        }
        .background(Design.backgroundGradient)
    }

    private var allocationsList: some View {
        ScrollView {
            VStack(spacing: 6) {
                ForEach(state.allocations) { alloc in
                    AllocationCard(alloc: alloc)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .frame(maxHeight: 240)
    }

    private var emptyState: some View {
        VStack(spacing: 4) {
            Text("No allocations")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Design.textMuted)
            Text("Run simemu acquire to get started")
                .font(.system(size: 11))
                .foregroundStyle(Design.textMuted.opacity(0.7))
        }
        .padding(.vertical, 20)
    }
}

struct MenuBarLabel: View {
    @ObservedObject var state: SimEmuState

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "iphone")
            if state.totalMemoryMB < 100 {
                Text("idle")
            } else {
                Text(formatMemory(state.totalMemoryMB))
                    .foregroundStyle(state.totalMemoryMB >= 16384 ? .red : .primary)
            }
        }
    }

    private func formatMemory(_ mb: Double) -> String {
        if mb >= 1024 {
            return String(format: "%.1f GB", mb / 1024)
        }
        return String(format: "%.0f MB", mb)
    }
}
