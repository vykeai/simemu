// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SimEmuBar",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "SimEmuBar",
            path: "Sources/SimEmuBar"
        ),
    ]
)
