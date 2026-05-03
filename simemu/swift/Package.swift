// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SimEmuBar",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(path: "../../../../onlystack/libs/desktop-tooling-core/swift/OnlyMenuBarKit"),
    ],
    targets: [
        .executableTarget(
            name: "SimEmuBar",
            dependencies: ["OnlyMenuBarKit"],
            path: "Sources/SimEmuBar"
        ),
    ]
)
