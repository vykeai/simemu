import SwiftUI

struct LureFact: Equatable {
    let emoji: String
    let category: String
    let text: String

    static let facts: [LureFact] = [
        LureFact(emoji: "🐨", category: "ANIMALS", text: "Koalas have fingerprints nearly identical to human ones — even forensic experts can struggle to tell them apart."),
        LureFact(emoji: "🐙", category: "ANIMALS", text: "Octopuses have three hearts and blue blood. Two hearts pump blood to the gills, one pumps it to the body."),
        LureFact(emoji: "🦈", category: "ANIMALS", text: "Sharks are older than trees. They've been around for about 400 million years."),
        LureFact(emoji: "🍯", category: "FOOD", text: "Honey never spoils. Archaeologists have found 3,000-year-old honey in Egyptian tombs that was still edible."),
        LureFact(emoji: "🌍", category: "SPACE", text: "A day on Venus is longer than a year on Venus. It takes 243 Earth days to rotate but only 225 to orbit the Sun."),
        LureFact(emoji: "⚡", category: "SCIENCE", text: "A single bolt of lightning contains enough energy to toast 100,000 slices of bread."),
        LureFact(emoji: "🧬", category: "BIOLOGY", text: "Humans share about 60% of their DNA with bananas."),
        LureFact(emoji: "🏔️", category: "GEOGRAPHY", text: "The shortest war in history lasted 38 minutes — between Britain and Zanzibar in 1896."),
        LureFact(emoji: "🎵", category: "MUSIC", text: "The song \"Happy Birthday\" was copyrighted until 2016. It earned about $2 million per year in royalties."),
        LureFact(emoji: "🐝", category: "ANIMALS", text: "Bees can recognize human faces. They use the same part of their brain that we use for face recognition."),
        LureFact(emoji: "🌊", category: "OCEAN", text: "More than 80% of the ocean is unexplored and unmapped. We know more about Mars than our own sea floor."),
        LureFact(emoji: "🧊", category: "SCIENCE", text: "Hot water freezes faster than cold water. This is called the Mpemba effect and no one fully understands why."),
    ]

    static func random() -> LureFact {
        facts.randomElement()!
    }
}

struct LureCard: View {
    let fact: LureFact
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Text(fact.emoji)
                        .font(.system(size: 14))
                    Design.sectionLabel(fact.category)
                    Spacer()
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 9))
                        .foregroundStyle(Design.textMuted.opacity(0.5))
                }

                Text(fact.text)
                    .font(.system(size: 11))
                    .foregroundStyle(Design.textMuted)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(Design.cardPadding)
            .background(
                RoundedRectangle(cornerRadius: Design.cardRadius)
                    .fill(Design.cardBg)
            )
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .buttonStyle(.plain)
    }
}
