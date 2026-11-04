import AppKit
import EventKit
import ScreenCaptureKit
import Vision

/// Drives Chrome for the agent (RPC "mac.chrome"). macOS grants this right to a whole application:
/// going through Voice Island avoids giving it to Terminal, and so to everything running in it.
/// Possible actions, and only these: read the active tab, change its address, close it, count
/// the windows and tabs.
@MainActor
enum ChromeBridge {
    static let method = "mac.chrome"
    private static let bundleID = "com.google.Chrome"

    private struct Request: Decodable {
        let action: String
        let url: String?
    }

    static func handle(_ payload: String) -> String {
        guard let data = payload.data(using: .utf8), let request = try? JSONDecoder().decode(Request.self, from: data)
        else { return reply(["error": "bad request"]) }
        // Chrome closed: don't launch it just to read a tab.
        guard !NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).isEmpty else {
            return reply(["running": false])
        }
        switch request.action {
        case "active_tab":
            let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier == bundleID
            let script = """
                tell application id "\(bundleID)"
                    if (count of windows) is 0 then return ""
                    set t to active tab of front window
                    return (URL of t) & linefeed & (title of t)
                end tell
                """
            switch run(script) {
            case .success(let text):
                let parts = text.split(separator: "\n", maxSplits: 1).map(String.init)
                return reply(["running": true, "front": front, "url": parts.first ?? "",
                              "title": parts.count > 1 ? parts[1] : ""])
            case .failure(let message):
                return reply(["running": true, "error": message])
            }
        case "set_url":
            guard let url = request.url, let parsed = URL(string: url), ["http", "https"].contains(parsed.scheme)
            else { return reply(["error": "bad url"]) }
            let quoted = url.replacingOccurrences(of: "\\", with: "%5C").replacingOccurrences(of: "\"", with: "%22")
            let script = """
                tell application id "\(bundleID)"
                    if (count of windows) is 0 then return "no window"
                    set URL of active tab of front window to "\(quoted)"
                    activate
                    return "ok"
                end tell
                """
            switch run(script) {
            case .success(let text): return reply(["ok": text == "ok"])
            case .failure(let message): return reply(["ok": false, "error": message])
            }
        case "close_tab":
            let script = """
                tell application id "\(bundleID)"
                    if (count of windows) is 0 then return "no window"
                    set t to active tab of front window
                    set closed to title of t
                    close t
                    return closed
                end tell
                """
            switch run(script) {
            case .success(let title): return reply(["ok": title != "no window", "title": title])
            case .failure(let message): return reply(["ok": false, "error": message])
            }
        case "count":
            let script = """
                tell application id "\(bundleID)"
                    set n to count of windows
                    if n is 0 then return "0" & linefeed & "0"
                    return (n as text) & linefeed & ((count of tabs of front window) as text)
                end tell
                """
            switch run(script) {
            case .success(let text):
                let parts = text.split(separator: "\n").compactMap { Int($0) }
                return reply(["running": true, "windows": parts.first ?? 0, "front_window_tabs": parts.last ?? 0])
            case .failure(let message):
                return reply(["running": true, "error": message])
            }
        default:
            return reply(["error": "unknown action"])
        }
    }

    private enum Outcome { case success(String), failure(String) }

    private static func run(_ source: String) -> Outcome {
        var error: NSDictionary?
        let result = NSAppleScript(source: source)?.executeAndReturnError(&error)
        if let error {
            // -1743: the user denied Voice Island the right to control Chrome.
            let code = error[NSAppleScript.errorNumber] as? Int ?? 0
            return .failure(code == -1743 ? "not allowed to control Chrome" : "\(error[NSAppleScript.errorMessage] ?? code)")
        }
        return .success(result?.stringValue ?? "")
    }

    static func reply(_ fields: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: fields) else { return "{}" }
        return String(decoding: data, as: UTF8.self)
    }
}

/// Open windows per application (RPC "mac.windows"). Only the owning app's name and the size:
/// no title or content, so no "Screen Recording" permission is needed.
@MainActor
enum WindowBridge {
    static let method = "mac.windows"

    static func handle() -> String {
        let list = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []
        var counts: [String: Int] = [:]
        for window in list {
            guard (window[kCGWindowLayer as String] as? Int) == 0,
                  (window[kCGWindowAlpha as String] as? Double ?? 1) > 0,
                  let owner = window[kCGWindowOwnerName as String] as? String,
                  let bounds = window[kCGWindowBounds as String] as? [String: Double],
                  (bounds["Width"] ?? 0) >= 160, (bounds["Height"] ?? 0) >= 100
            else { continue }
            counts[owner, default: 0] += 1
        }
        let front = NSWorkspace.shared.frontmostApplication?.localizedName ?? ""
        return ChromeBridge.reply(["front": front, "windows": counts])
    }
}

/// Keys sent for the agent (RPC "mac.keys"), closed list: play / pause, next / previous track,
/// YouTube full screen, new window (⌘ N) of an application. Asks for the macOS "Accessibility" permission
/// the first time, for Voice Island only.
@MainActor
enum KeyBridge {
    static let method = "mac.keys"

    private struct Request: Decodable {
        let action: String
        let bundleId: String?
    }

    static func handle(_ payload: String) async -> String {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let data = payload.data(using: .utf8), let request = try? decoder.decode(Request.self, from: data)
        else { return ChromeBridge.reply(["error": "bad request"]) }
        let prompt = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        guard AXIsProcessTrustedWithOptions(prompt) else {
            return ChromeBridge.reply(["ok": false, "error": "needs the Accessibility permission for Voice Island"])
        }
        switch request.action {
        case "play_pause": mediaKey(16)
        case "next_track": mediaKey(17)
        case "previous_track": mediaKey(18)
        case "youtube_fullscreen":
            guard await bringToFront("com.google.Chrome") else { return ChromeBridge.reply(["ok": false, "error": "Chrome is not open"]) }
            key(3, flags: [])  // "f": YouTube player full screen
        case "new_window":
            guard let bundle = request.bundleId, await bringToFront(bundle) else {
                return ChromeBridge.reply(["ok": false, "error": "app not running"])
            }
            key(45, flags: .maskCommand)  // ⌘ N
        default:
            return ChromeBridge.reply(["error": "unknown action"])
        }
        return ChromeBridge.reply(["ok": true])
    }

    /// Brings the app to the front (a menu bar app can't simply "activate" another app).
    private static func bringToFront(_ bundleID: String) async -> Bool {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) else { return false }
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        guard (try? await NSWorkspace.shared.openApplication(at: url, configuration: config)) != nil else { return false }
        try? await Task.sleep(nanoseconds: 350_000_000)
        return true
    }

    private static func key(_ code: CGKeyCode, flags: CGEventFlags) {
        let source = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: down)
            event?.flags = flags
            event?.post(tap: .cghidEventTap)
        }
    }

    /// Media key (16 play / pause, 17 next, 18 previous): controls Music, Spotify, YouTube…
    private static func mediaKey(_ type: Int) {
        for down in [true, false] {
            let flags = NSEvent.ModifierFlags(rawValue: down ? 0xa00 : 0xb00)
            let data1 = (type << 16) | ((down ? 0xa : 0xb) << 8)
            NSEvent.otherEvent(with: .systemDefined, location: .zero, modifierFlags: flags, timestamp: 0,
                               windowNumber: 0, context: nil, subtype: 8, data1: data1, data2: -1)?
                .cgEvent?.post(tap: .cghidEventTap)
        }
    }
}

/// Places an app's window (RPC "mac.place"): left or right half, maximized, centered; on the current
/// screen, the laptop's or the external display. Accessibility API, already allowed for keys: a few
/// milliseconds, where Atticus goes through System Events (~0.5 s).
@MainActor
enum PlaceBridge {
    static let method = "mac.place"

    private struct Request: Decodable {
        let bundleId: String
        let place: String
        let screen: String?
    }

    static func handle(_ payload: String) async -> String {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let data = payload.data(using: .utf8), let request = try? decoder.decode(Request.self, from: data)
        else { return ChromeBridge.reply(["error": "bad request"]) }
        guard AXIsProcessTrusted() else {
            return ChromeBridge.reply(["ok": false, "error": "needs the Accessibility permission for Voice Island"])
        }
        guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: request.bundleId).first else {
            return ChromeBridge.reply(["ok": false, "error": "not running"])
        }
        let element = AXUIElementCreateApplication(app.processIdentifier)
        // An app that was just launched takes a moment to create its window.
        var window: AXUIElement?
        for _ in 0..<20 {
            window = mainWindow(element)
            if window != nil { break }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let window else { return ChromeBridge.reply(["ok": false, "error": "no window"]) }

        if bool(window, "AXFullScreen") {  // a macOS full-screen window doesn't move: exit full screen first
            AXUIElementSetAttributeValue(window, "AXFullScreen" as CFString, kCFBooleanFalse)
            try? await Task.sleep(nanoseconds: 1_100_000_000)
        }
        if bool(window, kAXMinimizedAttribute) {
            AXUIElementSetAttributeValue(window, kAXMinimizedAttribute as CFString, kCFBooleanFalse)
        }
        let current = frame(window)
        guard let screen = target(request.screen ?? "current", from: current) else {
            return ChromeBridge.reply(["ok": false, "error": "no such screen"])
        }
        let v = screen.visibleFrame
        let goal: CGRect
        switch request.place {
        case "left": goal = CGRect(x: v.minX, y: v.minY, width: v.width / 2, height: v.height)
        case "right": goal = CGRect(x: v.midX, y: v.minY, width: v.width / 2, height: v.height)
        case "full": goal = v
        case "center":
            let size = CGSize(width: v.width * 0.6, height: v.height * 0.75)
            goal = CGRect(x: v.midX - size.width / 2, y: v.midY - size.height / 2, width: size.width, height: size.height)
        default: return ChromeBridge.reply(["error": "place must be left, right, full or center"])
        }
        // Position, size, then position again: some apps adjust their size when changing screens.
        let ax = axRect(goal)
        set(window, position: ax.origin)
        set(window, size: ax.size)
        set(window, position: ax.origin)
        AXUIElementPerformAction(window, kAXRaiseAction as CFString)
        app.activate()
        let done = frame(window)
        let close = abs(done.minX - ax.minX) < 40 && abs(done.minY - ax.minY) < 40
        return ChromeBridge.reply(["ok": close, "error": close ? "" : "the app refused that size or position"])
    }

    private static func mainWindow(_ app: AXUIElement) -> AXUIElement? {
        for attribute in [kAXFocusedWindowAttribute, kAXMainWindowAttribute] {
            var value: CFTypeRef?
            if AXUIElementCopyAttributeValue(app, attribute as CFString, &value) == .success, let value,
                CFGetTypeID(value) == AXUIElementGetTypeID()
            {
                return (value as! AXUIElement)
            }
        }
        var list: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &list) == .success,
            let windows = list as? [AXUIElement]
        else { return nil }
        return windows.first { string($0, kAXSubroleAttribute) == "AXStandardWindow" } ?? windows.first
    }

    /// Target screen: the window's, the laptop's (built-in) or an external display.
    private static func target(_ name: String, from window: CGRect) -> NSScreen? {
        let builtIn: (NSScreen) -> Bool = { screen in
            let id = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0
            return CGDisplayIsBuiltin(id) != 0
        }
        switch name {
        case "laptop": return NSScreen.screens.first(where: builtIn)
        case "monitor": return NSScreen.screens.first { !builtIn($0) }
        default:
            let center = cocoaPoint(CGPoint(x: window.midX, y: window.midY))
            return NSScreen.screens.first { $0.frame.contains(center) } ?? NSScreen.main
        }
    }

    // Accessibility counts in points from the top of the main screen, AppKit from the bottom.
    private static var primaryHeight: CGFloat { NSScreen.screens.first?.frame.maxY ?? 0 }

    private static func axRect(_ r: CGRect) -> CGRect {
        CGRect(x: r.minX, y: primaryHeight - r.maxY, width: r.width, height: r.height)
    }

    private static func cocoaPoint(_ p: CGPoint) -> CGPoint { CGPoint(x: p.x, y: primaryHeight - p.y) }

    private static func frame(_ window: AXUIElement) -> CGRect {
        var position = CGPoint.zero, size = CGSize.zero
        var value: CFTypeRef?
        if AXUIElementCopyAttributeValue(window, kAXPositionAttribute as CFString, &value) == .success, let value {
            AXValueGetValue(value as! AXValue, .cgPoint, &position)
        }
        if AXUIElementCopyAttributeValue(window, kAXSizeAttribute as CFString, &value) == .success, let value {
            AXValueGetValue(value as! AXValue, .cgSize, &size)
        }
        return CGRect(origin: position, size: size)
    }

    private static func set(_ window: AXUIElement, position: CGPoint) {
        var p = position
        if let value = AXValueCreate(.cgPoint, &p) {
            AXUIElementSetAttributeValue(window, kAXPositionAttribute as CFString, value)
        }
    }

    private static func set(_ window: AXUIElement, size: CGSize) {
        var s = size
        if let value = AXValueCreate(.cgSize, &s) {
            AXUIElementSetAttributeValue(window, kAXSizeAttribute as CFString, value)
        }
    }

    private static func bool(_ element: AXUIElement, _ attribute: String) -> Bool {
        var value: CFTypeRef?
        return AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success
            && (value as? Bool) == true
    }

    private static func string(_ element: AXUIElement, _ attribute: String) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success else { return nil }
        return value as? String
    }
}

/// Text of the frontmost app (RPC "mac.text"): read the selection ("summarize this", "reply to this"),
/// type at the cursor (a reply, a translation). Never sends: the user proofreads and sends it themselves.
@MainActor
enum TextBridge {
    static let method = "mac.text"

    private struct Request: Decodable {
        let action: String
        let text: String?
    }

    static func handle(_ payload: String) async -> String {
        guard let data = payload.data(using: .utf8), let request = try? JSONDecoder().decode(Request.self, from: data)
        else { return ChromeBridge.reply(["error": "bad request"]) }
        guard AXIsProcessTrusted() else {
            return ChromeBridge.reply(["ok": false, "error": "needs the Accessibility permission for Voice Island"])
        }
        let front = frontApp()
        switch request.action {
        case "selection":
            var text = front.flatMap(selectedText) ?? ""
            if text.isEmpty, NSApp.keyWindow == nil { text = await copiedSelection() }   // Chrome, Slack, VS Code…
            return ChromeBridge.reply(["ok": true, "app": front?.localizedName ?? "", "text": String(text.prefix(6000))])
        case "insert":
            guard let text = request.text, !text.isEmpty else { return ChromeBridge.reply(["error": "no text"]) }
            if NSApp.keyWindow != nil {   // the chat field has the keyboard: hand control back to the previous app
                front?.activate()
                try? await Task.sleep(nanoseconds: 250_000_000)
            }
            TextInsert.paste(text)
            return ChromeBridge.reply(["ok": true, "app": front?.localizedName ?? ""])
        default:
            return ChromeBridge.reply(["error": "unknown action"])
        }
    }

    private static func frontApp() -> NSRunningApplication? {
        let mine = Bundle.main.bundleIdentifier
        return NSWorkspace.shared.frontmostApplication.flatMap { $0.bundleIdentifier == mine ? nil : $0 }
            ?? NSWorkspace.shared.runningApplications.first { $0.isActive && $0.bundleIdentifier != mine }
    }

    private static func selectedText(_ app: NSRunningApplication) -> String? {
        let element = AXUIElementCreateApplication(app.processIdentifier)
        var focused: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
            let focused, CFGetTypeID(focused) == AXUIElementGetTypeID()
        else { return nil }
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(focused as! AXUIElement, kAXSelectedTextAttribute as CFString, &value)
            == .success
        else { return nil }
        return (value as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// ⌘ C then reads the clipboard (restored as before afterwards), for apps without text Accessibility.
    private static func copiedSelection() async -> String {
        let board = NSPasteboard.general
        let before = board.changeCount
        let saved = board.string(forType: .string)
        let source = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: 8, keyDown: down)  // C
            event?.flags = .maskCommand
            event?.post(tap: .cghidEventTap)
        }
        try? await Task.sleep(nanoseconds: 250_000_000)
        guard board.changeCount != before else { return "" }
        let text = board.string(forType: .string) ?? ""
        board.clearContents()
        if let saved { board.setString(saved, forType: .string) }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// What is written in the frontmost window (RPC "mac.screen"), for "what's on my screen", "what's
/// wrong with this code", "reply to this email". Accessibility first (no extra permission); if it
/// gives too little, a capture of the window read by Apple's OCR (Vision), on the Mac, if "Screen
/// Recording" is allowed. The image is neither kept nor sent: only the text goes to the agent.
@MainActor
enum ScreenBridge {
    static let method = "mac.screen"
    private static let limit = 6000

    static func handle(_ payload: String) async -> String {
        guard AXIsProcessTrusted() else {
            return ChromeBridge.reply(["ok": false, "error": "needs the Accessibility permission for Voice Island"])
        }
        let mine = Bundle.main.bundleIdentifier
        guard let app = NSWorkspace.shared.frontmostApplication.flatMap({ $0.bundleIdentifier == mine ? nil : $0 })
        else { return ChromeBridge.reply(["ok": false, "error": "no app in front"]) }
        var text = accessibilityText(app)
        var source = "accessibility"
        if text.count < 200 {
            if CGPreflightScreenCaptureAccess(), let ocr = await ocrText(app), ocr.count > text.count {
                text = ocr
                source = "ocr"
            } else if !CGPreflightScreenCaptureAccess() {
                CGRequestScreenCaptureAccess()
            }
        }
        return ChromeBridge.reply([
            "ok": true, "app": app.localizedName ?? "", "source": source, "text": String(text.prefix(limit)),
        ])
    }

    // MARK: Accessibility

    private static func accessibilityText(_ app: NSRunningApplication) -> String {
        let element = AXUIElementCreateApplication(app.processIdentifier)
        // Chrome and Electron apps only expose their web content on request.
        AXUIElementSetAttributeValue(element, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
        AXUIElementSetAttributeValue(element, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        var window: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXFocusedWindowAttribute as CFString, &window) == .success,
            let window, CFGetTypeID(window) == AXUIElementGetTypeID()
        else { return "" }
        var lines: [String] = []
        var seen = Set<String>()
        var budget = limit
        collect(window as! AXUIElement, depth: 0, lines: &lines, seen: &seen, budget: &budget)
        // Left on, this mode slows down and animates Chrome's window moves.
        AXUIElementSetAttributeValue(element, "AXEnhancedUserInterface" as CFString, kCFBooleanFalse)
        return lines.joined(separator: "\n")
    }

    private static func collect(_ element: AXUIElement, depth: Int, lines: inout [String], seen: inout Set<String>,
                                budget: inout Int)
    {
        guard depth < 40, budget > 0 else { return }
        for attribute in [kAXValueAttribute, kAXTitleAttribute, kAXDescriptionAttribute] {
            var value: CFTypeRef?
            if AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success,
                let text = (value as? String)?.trimmingCharacters(in: .whitespacesAndNewlines),
                text.count > 1, !seen.contains(text)
            {
                seen.insert(text)
                lines.append(text)
                budget -= text.count
                break
            }
        }
        var children: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &children) == .success,
            let list = children as? [AXUIElement]
        else { return }
        for child in list.prefix(400) {
            collect(child, depth: depth + 1, lines: &lines, seen: &seen, budget: &budget)
            if budget <= 0 { return }
        }
    }

    // MARK: OCR

    /// Capture of an app's main window (without the cursor or other windows). `maxSide`: longest side
    /// in pixels; nil = Retina resolution (x2), for OCR.
    static func windowImage(_ app: NSRunningApplication, maxSide: CGFloat? = nil) async -> CGImage? {
        guard let content = try? await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true),
            let window = content.windows.first(where: {
                $0.owningApplication?.processID == app.processIdentifier && $0.windowLayer == 0 && $0.frame.width > 200
            })
        else { return nil }
        let longest = max(window.frame.width, window.frame.height)
        let scale = maxSide.map { min(2, $0 / longest) } ?? 2
        let config = SCStreamConfiguration()
        config.width = Int(window.frame.width * scale)
        config.height = Int(window.frame.height * scale)
        config.showsCursor = false
        return try? await SCScreenshotManager.captureImage(
            contentFilter: SCContentFilter(desktopIndependentWindow: window), configuration: config)
    }

    private static func ocrText(_ app: NSRunningApplication) async -> String? {
        guard let image = await windowImage(app) else { return nil }
        return await withCheckedContinuation { continuation in
            let request = VNRecognizeTextRequest { request, _ in
                let lines = (request.results as? [VNRecognizedTextObservation])?
                    .compactMap { $0.topCandidates(1).first?.string } ?? []
                continuation.resume(returning: lines.joined(separator: "\n"))
            }
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = true
            DispatchQueue.global(qos: .userInitiated).async {
                try? VNImageRequestHandler(cgImage: image).perform([request])
            }
        }
    }
}

/// Look at the screen (RPC "mac.look"), for "what am I looking at", a chart, a photo, "where is the Save
/// button". On demand only: capture of the frontmost window, sent by the app itself to the local vision
/// server (mlx_vlm.server on this Mac, refused elsewhere), which answers the question. The image stays in memory:
/// never written to disk nor sent to the agent, only the text answer goes back.
@MainActor
enum LookBridge {
    static let method = "mac.look"

    private struct Request: Decodable {
        let question: String
        let url: String
        let model: String
        let max_side: Int?
        let max_tokens: Int?
        let system: String?
    }

    private static let session = URLSession(configuration: .ephemeral)

    static func handle(_ payload: String) async -> String {
        guard let request = try? JSONDecoder().decode(Request.self, from: Data(payload.utf8)) else {
            return ChromeBridge.reply(["ok": false, "error": "bad request"])
        }
        guard let base = URL(string: request.url), ["127.0.0.1", "localhost"].contains(base.host ?? "") else {
            return ChromeBridge.reply(["ok": false, "error": "the vision server must run on this Mac"])
        }
        guard CGPreflightScreenCaptureAccess() else {
            CGRequestScreenCaptureAccess()
            return ChromeBridge.reply(["ok": false, "error": "needs the Screen Recording permission for Voice Island"])
        }
        let mine = Bundle.main.bundleIdentifier
        guard let app = NSWorkspace.shared.frontmostApplication.flatMap({ $0.bundleIdentifier == mine ? nil : $0 })
        else { return ChromeBridge.reply(["ok": false, "error": "no app in front"]) }
        guard let image = await ScreenBridge.windowImage(app, maxSide: CGFloat(request.max_side ?? 1280)),
            let jpeg = NSBitmapImageRep(cgImage: image).representation(using: .jpeg, properties: [.compressionFactor: 0.8])
        else { return ChromeBridge.reply(["ok": false, "error": "could not capture the \(app.localizedName ?? "") window"]) }

        var messages: [[String: Any]] = []
        if let system = request.system { messages.append(["role": "system", "content": system]) }
        messages.append(["role": "user", "content": [
            ["type": "text", "text": request.question],
            ["type": "image_url", "image_url": ["url": "data:image/jpeg;base64," + jpeg.base64EncodedString()]],
        ]])
        let body: [String: Any] = [
            "model": request.model, "messages": messages, "max_tokens": request.max_tokens ?? 160, "temperature": 0,
            "chat_template_kwargs": ["enable_thinking": false],
        ]
        var http = URLRequest(url: base.appendingPathComponent("chat/completions"), timeoutInterval: 55)
        http.httpMethod = "POST"
        http.setValue("application/json", forHTTPHeaderField: "Content-Type")
        http.httpBody = try? JSONSerialization.data(withJSONObject: body)
        guard let (data, response) = try? await session.data(for: http) else {
            return ChromeBridge.reply(["ok": false, "error": "the vision server is not running"])
        }
        guard (response as? HTTPURLResponse)?.statusCode == 200,
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let message = (json["choices"] as? [[String: Any]])?.first?["message"] as? [String: Any],
            let answer = message["content"] as? String
        else { return ChromeBridge.reply(["ok": false, "error": "the vision server gave no answer"]) }
        return ChromeBridge.reply(["ok": true, "app": app.localizedName ?? "", "answer": answer])
    }
}

/// Calendar and reminders (RPC "mac.calendar"), with EventKit, on the Mac. Adding is immediate; moving or
/// deleting an event goes through a confirmation card (Approve / Cancel).
@MainActor
enum CalendarBridge {
    static let method = "mac.calendar"
    private static let store = EKEventStore()

    private struct Request: Decodable {
        let action: String
        let title: String?
        let start: String?      // "yyyy-MM-dd HH:mm", local time
        let minutes: Int?
        let newStart: String?
        let days: Int?
    }

    private static let format: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f
    }()

    static func handle(_ payload: String) async -> String {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let data = payload.data(using: .utf8), let r = try? decoder.decode(Request.self, from: data)
        else { return ChromeBridge.reply(["error": "bad request"]) }
        if r.action == "add_reminder" {
            guard (try? await store.requestFullAccessToReminders()) == true else {
                return ChromeBridge.reply(["ok": false, "error": "Reminders access is not allowed for Voice Island"])
            }
            let reminder = EKReminder(eventStore: store)
            reminder.title = r.title ?? "Reminder"
            reminder.calendar = store.defaultCalendarForNewReminders()
            if let due = r.start.flatMap(format.date) {
                reminder.dueDateComponents = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: due)
                reminder.addAlarm(EKAlarm(absoluteDate: due))
            }
            do { try store.save(reminder, commit: true) } catch { return fail(error) }
            return ChromeBridge.reply(["ok": true])
        }
        guard (try? await store.requestFullAccessToEvents()) == true else {
            return ChromeBridge.reply(["ok": false, "error": "Calendar access is not allowed for Voice Island"])
        }
        switch r.action {
        case "list":
            let from = r.start.flatMap(format.date) ?? Calendar.current.startOfDay(for: Date())
            let to = Calendar.current.date(byAdding: .day, value: max(1, r.days ?? 1), to: from) ?? from
            let events = store.events(matching: store.predicateForEvents(withStart: from, end: to, calendars: nil))
                .sorted { $0.startDate < $1.startDate }.prefix(30)
                .map { ["title": $0.title ?? "", "start": format.string(from: $0.startDate),
                        "end": format.string(from: $0.endDate), "all_day": $0.isAllDay] as [String: Any] }
            return ChromeBridge.reply(["ok": true, "events": Array(events)])
        case "add":
            guard let start = r.start.flatMap(format.date) else { return ChromeBridge.reply(["error": "bad start"]) }
            let event = EKEvent(eventStore: store)
            event.title = r.title ?? "Event"
            event.startDate = start
            event.endDate = start.addingTimeInterval(Double(r.minutes ?? 60) * 60)
            event.calendar = store.defaultCalendarForNewEvents
            do { try store.save(event, span: .thisEvent) } catch { return fail(error) }
            return ChromeBridge.reply(["ok": true])
        case "move", "delete":
            guard let event = find(r.title ?? "") else {
                return ChromeBridge.reply(["ok": false, "error": "no upcoming event called \(r.title ?? "")"])
            }
            let old = format.string(from: event.startDate)
            if r.action == "move" {
                guard let start = r.newStart.flatMap(format.date) else { return ChromeBridge.reply(["error": "bad time"]) }
                let length = event.endDate.timeIntervalSince(event.startDate)
                guard confirm("Move “\(event.title ?? "")”?", "From \(old)\nto \(format.string(from: start))") else {
                    return ChromeBridge.reply(["ok": false, "error": "the user cancelled"])
                }
                event.startDate = start
                event.endDate = start.addingTimeInterval(length)
                do { try store.save(event, span: .thisEvent) } catch { return fail(error) }
            } else {
                guard confirm("Delete “\(event.title ?? "")”?", "On \(old)") else {
                    return ChromeBridge.reply(["ok": false, "error": "the user cancelled"])
                }
                do { try store.remove(event, span: .thisEvent) } catch { return fail(error) }
            }
            return ChromeBridge.reply(["ok": true, "title": event.title ?? "", "was": old])
        default:
            return ChromeBridge.reply(["error": "unknown action"])
        }
    }

    /// The next event (within 30 days) whose title contains these words.
    private static func find(_ title: String) -> EKEvent? {
        let words = title.lowercased().split(separator: " ").map(String.init).filter { $0.count > 2 }
        let now = Date()
        let later = Calendar.current.date(byAdding: .day, value: 30, to: now) ?? now
        return store.events(matching: store.predicateForEvents(withStart: now, end: later, calendars: nil))
            .sorted { $0.startDate < $1.startDate }
            .first { event in words.allSatisfy { (event.title ?? "").lowercased().contains($0) } }
    }

    /// Confirmation card: nothing is moved or deleted without a click on Approve.
    private static func confirm(_ title: String, _ detail: String) -> Bool {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "Approve")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        return alert.runModal() == .alertFirstButtonReturn
    }

    private static func fail(_ error: Error) -> String {
        ChromeBridge.reply(["ok": false, "error": error.localizedDescription])
    }
}
