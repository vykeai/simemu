import SwiftUI

struct AllocationCard: View {
    let alloc: AllocationInfo

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(alloc.statusColor)
                .frame(width: 8, height: 8)

            VStack(alignment: .leading, spacing: 2) {
                Text(alloc.slug)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Design.textPrimary)

                HStack(spacing: 0) {
                    Image(systemName: alloc.platformIcon)
                        .font(.system(size: 9))
                        .foregroundStyle(Design.textMuted)

                    Text("  \(alloc.deviceName)")
                        .font(.system(size: 11))
                        .foregroundStyle(Design.textMuted)
                }
            }

            Spacer()

            Text(alloc.memoryText)
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(alloc.isBooted ? Design.textPrimary : Design.textMuted)
        }
        .padding(.horizontal, Design.cardPadding)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: Design.cardRadius)
                .fill(Design.cardBg)
                .overlay(
                    RoundedRectangle(cornerRadius: Design.cardRadius)
                        .strokeBorder(Design.cardBorder, lineWidth: 0.5)
                )
        )
    }
}
