.PHONY: build release clean install app

BUILD_DIR := .build
RELEASE_BIN := $(BUILD_DIR)/release/SimEmuBar
APP_BUNDLE := $(BUILD_DIR)/SimEmuBar.app
INSTALL_DIR := /Applications

build:
	swift build

release:
	swift build -c release -Xswiftc -Onone

app: release
	rm -rf $(APP_BUNDLE)
	mkdir -p $(APP_BUNDLE)/Contents/MacOS
	mkdir -p $(APP_BUNDLE)/Contents/Resources
	cp $(RELEASE_BIN) $(APP_BUNDLE)/Contents/MacOS/SimEmuBar
	/usr/libexec/PlistBuddy -c "Add :CFBundleName string SimEmuBar" $(APP_BUNDLE)/Contents/Info.plist
	/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.simemu.bar" $(APP_BUNDLE)/Contents/Info.plist
	/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 1.0" $(APP_BUNDLE)/Contents/Info.plist
	/usr/libexec/PlistBuddy -c "Add :CFBundleExecutable string SimEmuBar" $(APP_BUNDLE)/Contents/Info.plist
	/usr/libexec/PlistBuddy -c "Add :CFBundlePackageType string APPL" $(APP_BUNDLE)/Contents/Info.plist
	/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" $(APP_BUNDLE)/Contents/Info.plist
	@echo "Built $(APP_BUNDLE)"

install: app
	rm -rf $(INSTALL_DIR)/SimEmuBar.app
	cp -R $(APP_BUNDLE) $(INSTALL_DIR)/SimEmuBar.app
	@echo "Installed to $(INSTALL_DIR)/SimEmuBar.app"

clean:
	swift package clean
	rm -rf $(APP_BUNDLE)
