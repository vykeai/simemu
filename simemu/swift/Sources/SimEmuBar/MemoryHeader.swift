import SwiftUI

struct MemoryHeader: View {
    @ObservedObject var state: SimEmuState

    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Design.sectionLabel("SIMEMU")
                Circle()
                    .fill(state.daemonRunning ? Design.dotGreen : Design.dotRed)
                    .frame(width: 6, height: 6)
                    .help(state.daemonRunning ? "API server running" : "API server offline")
                Spacer()
                Text(formattedMemory)
                    .font(.system(size: 16, weight: .bold, design: .monospaced))
                    .foregroundStyle(memoryColor)
            }

            // Memory bar
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(Design.cardBg)
                        .frame(height: 6)

                    RoundedRectangle(cornerRadius: 3)
                        .fill(memoryColor)
                        .frame(width: barWidth(in: geo.size.width), height: 6)
                }
            }
            .frame(height: 6)

            HStack {
                Circle()
                    .fill(Design.dotGreen)
                    .frame(width: 6, height: 6)
                Text("\(state.bootedCount) booted")
                    .font(.system(size: 11))
                    .foregroundStyle(Design.textMuted)

                Text("·")
                    .foregroundStyle(Design.textMuted)

                Text("\(state.allocations.count) allocated")
                    .font(.system(size: 11))
                    .foregroundStyle(Design.textMuted)

                Spacer()
            }
        }
        .padding(12)
    }

    private var formattedMemory: String {
        if state.totalMemoryMB < 100 { return "idle" }
        if state.totalMemoryMB >= 1024 {
            return String(format: "%.1f GB", state.totalMemoryMB / 1024)
        }
        return String(format: "%.0f MB", state.totalMemoryMB)
    }

    private var memoryColor: Color {
        if state.totalMemoryMB < 4096 { return Design.dotGreen }
        if state.totalMemoryMB < 8192 { return Design.dotAmber }
        if state.totalMemoryMB < 16384 { return Design.dotRed }
        return Design.dotRed
    }

    private func barWidth(in totalWidth: CGFloat) -> CGFloat {
        // Scale: 0 → 32GB
        let fraction = min(state.totalMemoryMB / 32768, 1.0)
        return max(0, totalWidth * fraction)
    }
}
