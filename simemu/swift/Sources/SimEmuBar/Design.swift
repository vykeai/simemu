import SwiftUI

enum Design {
    // Background
    static let bgDark = Color(hex: 0x120B07)
    static let bgLight = Color(hex: 0x2A1208)

    // Surface
    static let cardBg = Color(hex: 0x1E1008)
    static let cardBorder = Color.white.opacity(0.2)

    // Text
    static let textPrimary = Color(hex: 0xFFF3E8)
    static let textMuted = Color(hex: 0xA07860)

    // Accent
    static let orange = Color(hex: 0xFF6B2B)

    // State dots
    static let dotGreen = Color(hex: 0x4ADE80)
    static let dotAmber = Color(hex: 0xFBBF24)
    static let dotRed = Color(hex: 0xEF4444)
    static let dotGray = Color(hex: 0x6B7280)

    // Card styling
    static let cardRadius: CGFloat = 12
    static let cardPadding: CGFloat = 10

    // Section label
    static func sectionLabel(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .bold))
            .kerning(1.0)
            .foregroundStyle(textMuted)
            .textCase(.uppercase)
    }

    static var backgroundGradient: some ShapeStyle {
        RadialGradient(
            colors: [bgLight, bgDark],
            center: .top,
            startRadius: 0,
            endRadius: 400
        )
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}
