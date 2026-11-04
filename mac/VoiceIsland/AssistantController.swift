import AppKit
import KeyboardShortcuts
import LiveKit
import SwiftUI

/// Measurements of one reply, sent by the agent on the "voice.metrics" topic.
struct TurnMetrics: Decodable, Equatable {
    let eouMs: Double
    let sttMs: Double
    let llmMs: Double?
    let ttsMs: Double
    let totalMs: Double
}

/// Opens / closes the assistant on the keyboard shortcut, drives the frame around the notch and the dashboard.
@MainActor
final class AssistantController: ObservableObject {
    enum Mode { case voice, command, chat }

    @Published private(set) var session: Session?
    /// Replies spoken aloud (voice mode) or in text only (command mode). Can be changed live.
    @Published private(set) var voiceReplies = true
    @Published private(set) var isActive = false
    @Published var expanded = false
    @Published private(set) var lastMetrics: TurnMetrics?
    /// Last action actually done (or refused) by the agent, shown for a few seconds in the frame.
    @Published private(set) var lastAction: AgentAction?
    private var actionHide: Task<Void, Never>?
    private let micProbe = MicProbe()
    private var micWatch: Task<Void, Never>?
    /// Mic muted: the assistant hears nothing, the conversation stays open.
    @Published private(set) var micMuted = false
    /// Input field shown in the frame (always in chat mode, on demand otherwise).
    @Published var typing = false {
        didSet { panel?.acceptsTyping = typing }
    }
    @Published private(set) var mode: Mode = .voice
    /// Area taken by the frame in the window (SwiftUI coordinates, origin at top left).
    var islandRect: CGRect = .zero

    private var panel: IslandPanel?
    private var dashboard: NSWindow?
    private var metricsListener: MetricsListener?
    private var mouseMonitors: [Any] = []
    let dictation = Dictation()
    private let fnKey = FnKeyMonitor()

    init() {
        AudioManager.shared.add(localAudioRenderer: micProbe)
        KeyboardShortcuts.onKeyUp(for: .toggleAssistant) { [weak self] in
            Task { @MainActor in self?.toggle() }
        }
        KeyboardShortcuts.onKeyUp(for: .openDashboard) { [weak self] in
            Task { @MainActor in self?.openDashboard() }
        }
        KeyboardShortcuts.onKeyUp(for: .commandMode) { [weak self] in
            Task { @MainActor in self?.toggle(mode: .command) }
        }
        KeyboardShortcuts.onKeyUp(for: .toggleMute) { [weak self] in
            Task { @MainActor in self?.toggleMute() }
        }
        KeyboardShortcuts.onKeyUp(for: .chatMode) { [weak self] in
            Task { @MainActor in self?.toggle(mode: .chat) }
        }
        // Dictation: while the shortcut is held, or Fn alone if that is the chosen trigger.
        KeyboardShortcuts.onKeyDown(for: .dictate) { [weak self] in
            guard SettingsStore.dictation().trigger != "fn" else { return }
            Task { @MainActor in self?.dictation.start() }
        }
        KeyboardShortcuts.onKeyUp(for: .dictate) { [weak self] in
            guard SettingsStore.dictation().trigger != "fn" else { return }
            Task { @MainActor in self?.dictation.stop() }
        }
        fnKey.onPress = { [weak self] in
            guard SettingsStore.dictation().trigger == "fn" else { return }
            self?.dictation.start()
        }
        fnKey.onRelease = { [weak self] cancelled in
            guard SettingsStore.dictation().trigger == "fn" else { return }
            cancelled ? self?.dictation.cancel() : self?.dictation.stop()
        }
        fnKey.start()
        // Keyboard-free test: 6 s dictation at launch, result in logs/dictation.log, nothing inserted.
        if CommandLine.arguments.contains("-DictationTest") {
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                self?.dictation.start()
                try? await Task.sleep(nanoseconds: 6_000_000_000)
                self?.dictation.stop()
            }
        }
        // Test for the "silent dictation after a conversation" bug: dictation, 8 s conversation, dictation
        // (nothing inserted).
        if CommandLine.arguments.contains("-DictationAfterConversationTest") {
            Task { @MainActor [weak self] in
                guard let self else { return }
                for step in ["avant", "conversation", "après"] {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    Dictation.log("test : \(step)")
                    if step == "conversation" {
                        self.toggle()
                        try? await Task.sleep(nanoseconds: 8_000_000_000)
                        self.stop()
                        try? await Task.sleep(nanoseconds: 1_500_000_000)
                        continue
                    }
                    self.dictation.start()
                    try? await Task.sleep(nanoseconds: 4_000_000_000)
                    self.dictation.stop()
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                }
            }
        }
        dictation.pauseAssistantMic = { [weak self] paused in
            guard let self, let session = self.session, self.isActive, !self.micMuted else { return }
            _ = try? await session.room.localParticipant.setMicrophone(enabled: !paused)
        }
        dictation.assistantOpen = { [weak self] in self?.isActive ?? false }
    }

    /// ⌥ Space: opens with voice / closes. ⌃ ⌥ Space: opens without voice, or during a conversation
    /// turns the reply voice off / back on without losing the conversation.
    /// ⌃ ⌥ T: opens the chat; during a conversation, shows / hides the input field.
    func toggle(mode: Mode = .voice) {
        switch (isActive, mode) {
        case (false, _): start(mode: mode)
        case (true, .voice): stop()
        case (true, .command): toggleVoiceReplies()
        case (true, .chat): toggleTyping()
        }
    }

    func toggleTyping() {
        guard isActive else { return }
        typing.toggle()
        if typing { panel?.makeKey() }
    }

    /// Typed message, handled like a spoken sentence: same tools, same guardrails.
    func send(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let session else { return }
        Task { await session.send(text: text) }
    }

    /// Yes / No on a risky action proposed by the agent (P0-6). Only the agent decides to run it.
    func confirm(_ action: AgentAction, yes: Bool) {
        guard let id = action.id, let session else { return }
        withAnimation(.easeIn(duration: 0.2)) { lastAction = nil }
        Task {
            guard let agent = session.room.agentParticipant?.identity else { return }
            let body = ["id": id, "answer": yes ? "yes" : "no"]
            guard let data = try? JSONSerialization.data(withJSONObject: body),
                  let payload = String(data: data, encoding: .utf8) else { return }
            _ = try? await session.room.localParticipant.performRpc(
                destinationIdentity: agent, method: "agent.confirm", payload: payload)
        }
    }

    /// Replies spoken aloud or in text only, applied right away by the agent.
    func toggleVoiceReplies() {
        guard isActive, let session else { return }
        voiceReplies.toggle()
        let on = voiceReplies
        Task {
            guard let agent = session.room.agentParticipant?.identity,
                  (try? await session.room.localParticipant.performRpc(
                      destinationIdentity: agent, method: "agent.set_voice", payload: on ? "on" : "off")) != nil
            else {
                // Agent not there yet or too old: revert to the real state.
                if self.session === session { self.voiceReplies = !on }
                return
            }
        }
    }

    /// Mutes or unmutes the mic of the current conversation.
    func toggleMute() {
        guard isActive, let session else { return }
        micMuted.toggle()
        let enabled = !micMuted
        Task { try? await session.room.localParticipant.setMicrophone(enabled: enabled) }
    }

    private func start(mode: Mode) {
        self.mode = mode
        voiceReplies = mode == .voice
        // Chat: mic muted from the start (can be turned back on with the mic button), input field open.
        micMuted = mode == .chat
        let prefix: String? = switch mode {
        case .voice: nil
        case .command: "command"
        case .chat: "chat"
        }
        try? AudioManager.shared.setEngineAvailability(.default)
        dictation.needsVoiceProcessing = true
        let source = LocalTokenSource(roomPrefix: prefix)
        let session = Session(tokenSource: source, options: SessionOptions(preConnectAudio: mode != .chat))
        lastAction = nil
        let listener = MetricsListener { [weak self] metrics in
            Task { @MainActor in self?.lastMetrics = metrics }
        } onAction: { [weak self] action in
            Task { @MainActor in self?.show(action) }
        }
        session.room.add(delegate: listener)
        metricsListener = listener
        // Commands to the Mac (typing text, calendar, windows…) accepted only from the room's agent: another
        // participant (token signed with the public --dev mode keys) can't use them.
        let room = session.room
        let bridges: [(String, @Sendable (String) async -> String)] = [
            (ChromeBridge.method, { await ChromeBridge.handle($0) }),
            (WindowBridge.method, { _ in await WindowBridge.handle() }),
            (KeyBridge.method, { await KeyBridge.handle($0) }),
            (PlaceBridge.method, { await PlaceBridge.handle($0) }),
            (TextBridge.method, { await TextBridge.handle($0) }),
            (ScreenBridge.method, { await ScreenBridge.handle($0) }),
            (LookBridge.method, { await LookBridge.handle($0) }),
            (CalendarBridge.method, { await CalendarBridge.handle($0) }),
        ]
        Task {
            for (method, handle) in bridges {
                try? await room.registerRpcMethod(method) { [weak room] data in
                    guard room?.remoteParticipants[data.callerIdentity]?.kind == .agent else {
                        return #"{"ok":false,"error":"refused: only the assistant may call this"}"#
                    }
                    return await handle(data.payload)
                }
            }
        }
        self.session = session
        isActive = true
        showPanel()
        typing = mode == .chat
        if typing { panel?.makeKey() }
        withAnimation(.spring(response: 0.42, dampingFraction: 0.78)) { expanded = true }
        Task {
            await session.start()
            // Session.start() always turns the mic on: mute it again for the chat.
            if mode == .chat { try? await session.room.localParticipant.setMicrophone(enabled: false) }
        }
        watchMicrophone(session)
    }

    func stop() {
        guard isActive else { return }
        isActive = false
        micWatch?.cancel()
        typing = false
        panel?.resignKey()
        let session = self.session
        Task {
            await session?.end()
            // LiveKit's audio engine is off between two conversations: when on, it keeps the mic and dictation
            // hears nothing (Oct 31). Turned back on by start().
            if !self.isActive { try? AudioManager.shared.setEngineAvailability(.none) }
            // Not done: setPlatformVoiceProcessingAllowed(false) here, so that dictation could skip VPIO (0.2 s
            // instead of 1.1 s after Fn). It passed -DictationAfterConversationTest but after a real conversation
            // every dictation got 0 buffers (Nov 4, 19:42-19:56).
        }
        withAnimation(.spring(response: 0.35, dampingFraction: 0.9)) { expanded = false }
        // Let the closing animation play before removing the frame.
        Task {
            try? await Task.sleep(nanoseconds: 450_000_000)
            if !self.isActive {
                self.panel?.orderOut(nil)
                self.stopTrackingMouse()
            }
        }
    }

    /// Mic on but silent for 4 s: turn it off and on again; still silent: alert in the frame.
    private func watchMicrophone(_ session: Session) {
        micWatch?.cancel()
        micProbe.reset()
        micWatch = Task {
            var started = Date()
            var restarted = false
            while !Task.isCancelled, self.isActive, self.session === session {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                guard session.isConnected, !self.micMuted else { started = Date(); micProbe.reset(); continue }
                let silent = micProbe.secondsSinceSound.map { $0 > 4 } ?? (Date().timeIntervalSince(started) > 6)
                guard silent else { restarted = false; continue }
                if !restarted {
                    restarted = true
                    try? await session.room.localParticipant.setMicrophone(enabled: false)
                    try? await session.room.localParticipant.setMicrophone(enabled: true)
                    started = Date()
                    micProbe.reset()
                } else {
                    self.show(AgentAction(
                        text: String(localized: "The mic isn't picking anything up. Check the selected mic and the permission, or relaunch Voice Island (menu)."),
                        ok: false))
                    started = Date()
                    micProbe.reset()
                    try? await Task.sleep(nanoseconds: 8_000_000_000)
                }
            }
        }
    }

    /// Relaunches the app (resets the whole audio side).
    func relaunch() {
        stop()
        let config = NSWorkspace.OpenConfiguration()
        config.createsNewApplicationInstance = true
        NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: config) { _, _ in
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
    }

    private func show(_ action: AgentAction) {
        withAnimation(.easeOut(duration: 0.2)) { lastAction = action }
        // Immediate feedback, even without voice: small system sound (succeeded / failed / to confirm).
        if SettingsStore.actionSounds() {
            NSSound(named: action.awaitsConfirmation ? "Pop" : action.ok ? "Tink" : "Funk")?.play()
        }
        actionHide?.cancel()
        // An action awaiting confirmation stays shown as long as the agent keeps it (2 min), the others 7 s.
        let seconds: UInt64 = action.awaitsConfirmation ? 120 : 7
        actionHide = Task {
            try? await Task.sleep(nanoseconds: seconds * 1_000_000_000)
            if !Task.isCancelled { withAnimation(.easeIn(duration: 0.3)) { self.lastAction = nil } }
        }
    }

    // MARK: Dashboard

    func openDashboard() {
        if dashboard == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 900, height: 620),
                styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                backing: .buffered, defer: false)
            window.title = "Voice Island"
            window.isReleasedWhenClosed = false
            window.contentViewController = NSHostingController(rootView: DashboardView(controller: self))
            window.center()
            window.setFrameAutosaveName("VoiceIslandDashboard")
            dashboard = window
        }
        NSApp.activate(ignoringOtherApps: true)
        dashboard?.makeKeyAndOrderFront(nil)
    }

    // MARK: Frame

    private func showPanel() {
        if panel == nil {
            panel = IslandPanel(rootView: IslandView(controller: self))
        }
        panel?.place(at: SettingsStore.islandPosition())
        panel?.orderFrontRegardless()
        startTrackingMouse()
    }

    /// The frame captures the mouse only when it hovers over it: elsewhere, clicks pass through.
    private func startTrackingMouse() {
        guard mouseMonitors.isEmpty else { return }
        let update: () -> Void = { [weak self] in self?.updateMousePassthrough() }
        if let global = NSEvent.addGlobalMonitorForEvents(matching: .mouseMoved, handler: { _ in update() }) {
            mouseMonitors.append(global)
        }
        if let local = NSEvent.addLocalMonitorForEvents(matching: .mouseMoved, handler: { event in
            update()
            return event
        }) {
            mouseMonitors.append(local)
        }
    }

    private func stopTrackingMouse() {
        mouseMonitors.forEach(NSEvent.removeMonitor)
        mouseMonitors.removeAll()
        panel?.ignoresMouseEvents = true
    }

    private func updateMousePassthrough() {
        guard let panel else { return }
        let mouse = NSEvent.mouseLocation
        let frame = panel.frame
        // islandRect has its origin at the top left; the screen at the bottom left.
        let rect = CGRect(
            x: frame.minX + islandRect.minX, y: frame.maxY - islandRect.maxY,
            width: islandRect.width, height: islandRect.height)
        panel.ignoresMouseEvents = !rect.contains(mouse)
    }
}

/// Action announced by the agent on the "voice.actions" topic: what was really done, or an alert
/// when the model claimed to act without launching anything.
struct AgentAction: Decodable, Equatable {
    let text: String
    let ok: Bool
    /// Typed status from the agent (P0-5): "verified", "succeeded", "attempted", "failed", later
    /// "proposed" / "approved" (P0-6). Missing with an older agent: `ok` alone is authoritative.
    var status: String? = nil
    /// Proposal to confirm (status "proposed"): sent back as-is with Yes / No.
    var id: String? = nil

    var awaitsConfirmation: Bool { status == "proposed" && id != nil }

    /// Launched with no known result (Claude Code task, MCP tool): neither green nor orange.
    var pending: Bool { ok && ["attempted", "proposed", "approved"].contains(status ?? "") }
}

/// Receives the measurements and actions published by the agent.
final class MetricsListener: RoomDelegate, @unchecked Sendable {
    private let onMetrics: (TurnMetrics) -> Void
    private let onAction: (AgentAction) -> Void
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    init(onMetrics: @escaping (TurnMetrics) -> Void, onAction: @escaping (AgentAction) -> Void) {
        self.onMetrics = onMetrics
        self.onAction = onAction
    }

    func room(_ room: Room, participant: RemoteParticipant?, didReceiveData data: Data, forTopic topic: String,
              encryptionType: EncryptionType)
    {
        if topic == "voice.actions", let action = try? decoder.decode(AgentAction.self, from: data) {
            onAction(action)
        }
        guard topic == "voice.metrics", let metrics = try? decoder.decode(TurnMetrics.self, from: data) else { return }
        onMetrics(metrics)
    }
}

/// Borderless window, above the menu bar, that never takes focus.
final class IslandPanel: NSPanel {
    static let size = NSSize(width: 620, height: 260)

    init(rootView: some View) {
        super.init(
            contentRect: NSRect(origin: .zero, size: Self.size),
            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        ignoresMouseEvents = true
        let host = NSHostingView(rootView: rootView)
        host.frame = NSRect(origin: .zero, size: Self.size)
        contentView = host
    }

    /// Becomes the key window only when the input field is shown (without activating the app:
    /// the window underneath keeps its active appearance, as with Spotlight).
    var acceptsTyping = false
    override var canBecomeKey: Bool { acceptsTyping }
    override var canBecomeMain: Bool { false }

    /// "notch": flush with the top edge of the main screen, centered on the camera (default).
    /// "top_left": top left, below the menu bar (like Atticus).
    func place(at position: String) {
        guard let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) ?? NSScreen.main else { return }
        let frame = screen.frame
        if position == "top_left" {
            let top = screen.visibleFrame.maxY - 6
            setFrameOrigin(NSPoint(x: frame.minX + 12 - (Self.size.width - IslandView.expandedWidth) / 2,
                                   y: top - Self.size.height))
        } else {
            setFrameOrigin(NSPoint(x: frame.midX - Self.size.width / 2, y: frame.maxY - Self.size.height))
        }
    }
}

extension NSScreen {
    /// Height and width of the camera notch (zero on a screen without a notch).
    var notchSize: CGSize {
        guard safeAreaInsets.top > 0, let left = auxiliaryTopLeftArea, let right = auxiliaryTopRightArea else {
            return .zero
        }
        return CGSize(width: frame.width - left.width - right.width, height: safeAreaInsets.top)
    }
}
