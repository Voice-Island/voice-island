import KeyboardShortcuts
import LiveKit
import SwiftUI

/// Dashboard: stack status, assistant test and all the settings.
struct DashboardView: View {
    /// Three separate products (dictation, voice commands, voice assistant), then what they share.
    enum Page: String, CaseIterable, Identifiable {
        case status, machine, test, dictation, command, assistant, voice, turn, llm, tools, myVoice, appearance
        var id: String { rawValue }

        var title: String {
            switch self {
            case .status: return String(localized: "Status")
            case .machine: return String(localized: "Your Mac")
            case .test: return String(localized: "Test")
            case .dictation: return String(localized: "Dictation")
            case .command: return String(localized: "Voice commands")
            case .assistant: return String(localized: "Voice assistant")
            case .voice: return String(localized: "Voice and emotions")
            case .turn: return String(localized: "Turn-taking")
            case .llm: return String(localized: "Language model")
            case .tools: return String(localized: "Tools")
            case .myVoice: return String(localized: "My voice")
            case .appearance: return String(localized: "Appearance")
            }
        }

        var icon: String {
            switch self {
            case .status: return "gauge.with.dots.needle.67percent"
            case .machine: return "laptopcomputer"
            case .test: return "waveform"
            case .dictation: return "text.cursor"
            case .command: return "bolt.horizontal.circle"
            case .assistant: return "bubble.left.and.bubble.right"
            case .voice: return "person.wave.2"
            case .turn: return "arrow.left.arrow.right"
            case .llm: return "cpu"
            case .tools: return "wrench.and.screwdriver"
            case .myVoice: return "person.badge.key"
            case .appearance: return "paintbrush"
            }
        }

        static let groups: [(title: String?, pages: [Page])] = [
            (nil, [.status, .machine, .test]),
            (String(localized: "Dictation (⌥ N by default)"), [.dictation]),
            (String(localized: "Voice commands (silent)"), [.command]),
            (String(localized: "Voice assistant"), [.assistant, .voice, .turn]),
            (String(localized: "Shared by all three"), [.llm, .tools, .myVoice, .appearance]),
        ]
    }

    @ObservedObject var controller: AssistantController
    @StateObject private var store = SettingsStore()
    @StateObject private var monitor = ServiceMonitor()
    @State private var page: Page? = .status

    var body: some View {
        NavigationSplitView {
            List(selection: $page) {
                ForEach(Page.groups.indices, id: \.self) { index in
                    Section {
                        ForEach(Page.groups[index].pages) { page in
                            Label(page.title, systemImage: page.icon).tag(page)
                        }
                    } header: {
                        if let title = Page.groups[index].title { Text(title) }
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 190, ideal: 200)
        } detail: {
            VStack(spacing: 0) {
                detail
                if store.isDirty { saveBar }
            }
            .navigationTitle(page?.title ?? "")
        }
        .frame(minWidth: 820, minHeight: 560)
        .onAppear {
            store.load()
            monitor.startPolling()
        }
        .onDisappear { monitor.stopPolling() }
    }

    @ViewBuilder private var detail: some View {
        if let error = store.error, store.draft == nil, page != .status {
            ContentUnavailableView("Settings unavailable", systemImage: "exclamationmark.triangle",
                                   description: Text(error))
        } else {
            switch page ?? .status {
            case .status: StatusPage(monitor: monitor)
            case .machine: MachinePage()
            case .test: TestPage(controller: controller)
            case .appearance: AppearancePage(settings: Binding($store.draft))
            default:
                if let binding = Binding($store.draft) {
                    switch page ?? .status {
                    case .dictation: DictationPage(settings: binding)
                    case .command: CommandPage(settings: binding)
                    case .assistant: AssistantPage(settings: binding)
                    case .voice: VoicePage(settings: binding)
                    case .turn: TurnPage(settings: binding)
                    case .llm: LLMPage(settings: binding)
                    case .tools: ToolsPage(settings: binding)
                    case .myVoice: MyVoicePage(settings: binding, controller: controller)
                    default: EmptyView()
                    }
                }
            }
        }
    }

    private var saveBar: some View {
        let later = store.draft.flatMap { draft in store.saved.map { draft.nextConversationChanges(since: $0) } } ?? []
        return HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Unsaved changes").font(.callout.weight(.semibold))
                (later.isEmpty ? Text("Applies from the next reply")
                     : Text("From the next conversation: \(later.joined(separator: ", "))"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Cancel") { store.revert() }
            Button("Save") { store.save() }
                .keyboardShortcut("s", modifiers: .command)
                .buttonStyle(.borderedProminent)
        }
        .padding(12)
        .background(.bar)
    }
}

// MARK: - Pages

/// What this Mac can run, product by product (MachineCheck.swift).
struct MachinePage: View {
    private let info = MachineInfo.current()

    var body: some View {
        Form {
            Section("This Mac") {
                LabeledContent("Chip", value: info.gpuCores.map { String(localized: "\(info.chip), \($0)-core GPU") }
                    ?? info.chip)
                LabeledContent("Memory", value: String(localized: "\(info.memoryGB) GB"))
                LabeledContent("Memory available to the GPU",
                               value: String(localized: "\(info.gpuBudgetGB, format: .number.precision(.fractionLength(1))) GB"))
                LabeledContent("Free disk space",
                               value: String(localized: "\(info.freeDiskGB, format: .number.precision(.fractionLength(0))) GB"))
            }
            Section("What runs well here") {
                ForEach(info.verdicts) { verdict in
                    HStack(alignment: .firstTextBaseline) {
                        Image(systemName: verdict.level == .good ? "checkmark.circle.fill"
                              : verdict.level == .slow ? "exclamationmark.triangle.fill" : "xmark.circle.fill")
                            .foregroundStyle(verdict.level == .good ? .green : verdict.level == .slow ? .orange : .red)
                        VStack(alignment: .leading) {
                            Text(verdict.product)
                            Text(verdict.note).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if let pack = info.recommendedPack {
                Section {
                    LabeledContent("\(pack.title) pack", value: String(
                        localized: "≈ \(pack.downloadGB, format: .number.precision(.fractionLength(0))) GB to download"))
                    Text(pack.summary).font(.caption).foregroundStyle(.secondary)
                } header: {
                    Text("Recommended for this Mac")
                } footer: {
                    Text("One language model serves both commands and the assistant: two models loaded at once would take twice the memory.")
                }
            }
        }
        .formStyle(.grouped)
    }
}

struct StatusPage: View {
    @ObservedObject var monitor: ServiceMonitor

    var body: some View {
        Form {
            Section {
                ForEach(monitor.services) { service in
                    HStack {
                        Circle()
                            .fill(service.isUp == nil ? .gray : (service.isUp! ? .green : .red))
                            .frame(width: 9, height: 9)
                        VStack(alignment: .leading) {
                            Text(service.name)
                            Text(service.detail).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        (service.isUp == nil ? Text(verbatim: "…") : (service.isUp! ? Text("Running") : Text("Stopped")))
                            .foregroundStyle(.secondary)
                    }
                }
            } header: {
                Text("Local services")
            } footer: {
                monitor.allUp ? Text("Everything is running: press ⌥ Space to talk.")
                    : Text("The assistant needs the first four services.")
            }
            Section {
                HStack {
                    Button {
                        monitor.startAll()
                    } label: {
                        Label("Start all", systemImage: "play.fill")
                    }
                    .disabled(monitor.busy != nil)
                    Button {
                        monitor.stopAll()
                    } label: {
                        Label("Stop all", systemImage: "stop.fill")
                    }
                    .disabled(monitor.busy != nil)
                    if let busy = monitor.busy {
                        ProgressView().controlSize(.small)
                        Text(busy).foregroundStyle(.secondary)
                    }
                }
                HStack {
                    Button("Open logs") { NSWorkspace.shared.open(Paths.logs) }
                    Button("Web interface") { NSWorkspace.shared.open(URL(string: "http://localhost:3010")!) }
                }
            }
        }
        .formStyle(.grouped)
    }
}

struct TestPage: View {
    @ObservedObject var controller: AssistantController

    var body: some View {
        VStack(spacing: 16) {
            HStack(spacing: 14) {
                Button {
                    controller.toggle()
                } label: {
                    Label(controller.isActive ? String(localized: "Stop") : String(localized: "Talk to the assistant"),
                          systemImage: controller.isActive ? "stop.fill" : "mic.fill")
                        .frame(minWidth: 180)
                }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)
                if controller.isActive {
                    Button {
                        controller.toggleMute()
                    } label: {
                        Label(controller.micMuted ? String(localized: "Unmute microphone")
                              : String(localized: "Mute microphone"),
                              systemImage: controller.micMuted ? "mic.slash.fill" : "mic.fill")
                    }
                    .controlSize(.large)
                    .tint(controller.micMuted ? .red : nil)
                }
                if controller.isActive {
                    Button {
                        controller.toggleVoiceReplies()
                    } label: {
                        Label(controller.voiceReplies ? String(localized: "Text replies")
                              : String(localized: "Spoken replies"),
                              systemImage: controller.voiceReplies ? "speaker.slash.fill" : "speaker.wave.2.fill")
                    }
                    .controlSize(.large)
                }
                if let metrics = controller.lastMetrics {
                    MetricsLine(metrics: metrics)
                }
                Spacer()
            }
            if let session = controller.session {
                SessionTranscript(session: session)
            } else {
                ContentUnavailableView("No conversation", systemImage: "waveform",
                                       description: Text("Start a conversation here or with ⌥ Space."))
            }
        }
        .padding(20)
    }
}

struct MetricsLine: View {
    let metrics: TurnMetrics

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "timer").foregroundStyle(.secondary)
            Text("end of turn \(Int(metrics.eouMs))")
            Text("LLM \(Int(metrics.llmMs ?? 0))")
            Text("voice \(Int(metrics.ttsMs))")
            Text("total \(Int(metrics.totalMs)) ms").bold()
        }
        .font(.callout.monospacedDigit())
    }
}

struct SessionTranscript: View {
    @ObservedObject var session: Session

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(session.messages) { message in
                        bubble(message).id(message.id)
                    }
                }
                .padding(4)
            }
            .defaultScrollAnchor(.bottom)
            .onChange(of: session.messages) { _, messages in
                if let last = messages.last { proxy.scrollTo(last.id, anchor: .bottom) }
            }
        }
    }

    @ViewBuilder private func bubble(_ message: ReceivedMessage) -> some View {
        switch message.content {
        case .agentTranscript(let text):
            Text(text)
                .padding(10)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
                .frame(maxWidth: .infinity, alignment: .leading)
        case .userTranscript(let text), .userInput(let text):
            Text(text)
                .padding(10)
                .foregroundStyle(.white)
                .background(Color.accentColor, in: RoundedRectangle(cornerRadius: 12))
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
    }
}

/// Dictation: hold the shortcut, speak, release; the text is typed at the cursor.
struct DictationPage: View {
    @Binding var settings: VoiceSettings

    private var dictation: Binding<VoiceSettings.DictationSettings> {
        Binding(get: { settings.dictation ?? .init(engine: "parakeet", language: "auto", cleanup: false) },
                set: { settings.dictation = $0 })
    }

    var body: some View {
        Form {
            Section {
                Picker("Trigger", selection: Binding(get: { dictation.wrappedValue.trigger ?? "shortcut" },
                                                     set: { dictation.wrappedValue.trigger = $0 })) {
                    Text("Shortcut below").tag("shortcut")
                    Text("Hold Fn alone").tag("fn")
                }
                if (dictation.wrappedValue.trigger ?? "shortcut") == "shortcut" {
                    KeyboardShortcuts.Recorder("Dictation (hold):", name: .dictate)
                }
            } footer: {
                (dictation.wrappedValue.trigger ?? "shortcut") == "fn"
                    ? Text("Hold Fn, speak, release. Fn with another key (arrows, F1…) or a short press cancels. In System Settings › Keyboard, set “Press 🌐 key to” to “Do Nothing”, and remove Fn from other dictation apps.")
                    : Text("Hold the shortcut, speak, release: the text is typed wherever the cursor is, in any app, without being sent.")
            }
            Section("Recognition") {
                Picker("Engine", selection: Binding(get: { dictation.wrappedValue.engine ?? "parakeet" },
                                                    set: { dictation.wrappedValue.engine = $0 })) {
                    Text("Parakeet v3 (local, open source, 25 languages)").tag("parakeet")
                    Text("Apple (requires macOS Dictation)").tag("apple")
                }
                Picker("Language", selection: dictation.language) {
                    Text("Mac language").tag("auto")
                    Text(verbatim: "Français").tag("fr-FR")
                    Text(verbatim: "English").tag("en-US")
                    Text(verbatim: "Deutsch (Schweiz)").tag("de-CH")
                }
                Toggle("Also remove filler words (LLM, slower)", isOn: dictation.cleanup)
                Text("Parakeet detects the language on its own; the chosen language is used by the Apple engine and by the Whisper fallback when Parakeet gets the alphabet wrong.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            VoiceIDSection(settings: $settings, product: .dictation)
        }
        .formStyle(.grouped)
    }
}

/// Voice commands: the assistant acts and answers in text, without voice (and the typed chat, which shares this mode).
struct CommandPage: View {
    @Binding var settings: VoiceSettings

    var body: some View {
        Form {
            Section {
                KeyboardShortcuts.Recorder("Silent commands:", name: .commandMode)
                KeyboardShortcuts.Recorder("Type (chat):", name: .chatMode)
            } footer: {
                Text("Press once to open, again to close. During a voice conversation, the same shortcut turns spoken replies off or on without losing the thread.")
            }
            Section("Transcription") {
                Picker("Engine", selection: Binding(get: { settings.stt.commandEngine ?? "parakeet" },
                                                    set: { settings.stt.commandEngine = $0 })) {
                    ForEach(Choices.sttEngines, id: \.0) { Text($0.1).tag($0.0) }
                }
                Text("Parakeet understands French, English and German; it transcribes the whole sentence after the silence. Moonshine only hears English, but shows the text live.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Toggle("Instant commands, without the model", isOn: Binding(
                    get: { settings.tools.instantCommands ?? true }, set: { settings.tools.instantCommands = $0 }))
            } footer: {
                Text("Open, close, place a window (“Chrome on the left”), pause, search: done in ~0.1 s, in English or French. Everything else goes through the language model.")
            }
            VoiceIDSection(settings: $settings, product: .command)
        }
        .formStyle(.grouped)
    }
}

/// Voice assistant: spoken conversation (voice, emotions and turn-taking have their own page).
struct AssistantPage: View {
    @Binding var settings: VoiceSettings

    var body: some View {
        Form {
            Section {
                KeyboardShortcuts.Recorder("Talk to the assistant:", name: .toggleAssistant)
                KeyboardShortcuts.Recorder("Mute / unmute microphone:", name: .toggleMute)
            } footer: {
                Text("Press once to open, again to close.")
            }
            Section("Transcription") {
                Picker("Engine", selection: Binding(get: { settings.stt.engine ?? "moonshine" },
                                                    set: { settings.stt.engine = $0 })) {
                    ForEach(Choices.sttEngines, id: \.0) { Text($0.1).tag($0.0) }
                }
                if (settings.stt.engine ?? "moonshine") == "moonshine" {
                    Picker("Moonshine model", selection: $settings.stt.model) {
                        ForEach(Choices.sttModels, id: \.0) { Text($0.1).tag($0.0) }
                    }
                    Toggle("Live transcription", isOn: $settings.stt.streaming)
                    SliderRow("End-of-sentence silence", value: $settings.stt.vadWindow, in: 0.1...1, step: 0.05,
                              format: "%.2f s", help: "Shorter = faster, but it may cut you off.")
                    SliderRow("Update interval", value: $settings.stt.updateInterval, in: 0.1...1, step: 0.05,
                              format: "%.2f s")
                } else {
                    Text("Parakeet: French, English, German; the whole sentence is transcribed after the silence, and the silence decides the end of turn.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Section {
                TextEditor(text: $settings.llm.systemPrompt)
                    .font(.body)
                    .frame(minHeight: 160)
            } header: {
                Text("Instructions for the model")
            } footer: {
                Text("Applies from the next reply. Instructions for the tools are added automatically.")
            }
            VoiceIDSection(settings: $settings, product: .assistant)
        }
        .formStyle(.grouped)
    }
}

/// Language model, shared by commands and the assistant (and dictation for "retirer les hésitations").
struct LLMPage: View {
    @Binding var settings: VoiceSettings

    var body: some View {
        Form {
            Section {
                Picker("Model", selection: $settings.llm.model) {
                    ForEach(Choices.llmModels, id: \.0) { Text($0.1).tag($0.0) }
                    if !Choices.llmModels.contains(where: { $0.0 == settings.llm.model }) {
                        Text(settings.llm.model).tag(settings.llm.model)
                    }
                }
                SliderRow("Temperature", value: $settings.llm.temperature, in: 0...2, step: 0.05,
                          help: "0 = always the same reply; higher = more varied.")
                SliderRow("Top-p", value: $settings.llm.topP, in: 0.05...1, step: 0.05)
                SliderRow("Max length", value: $settings.llm.maxTokens, in: 16...512, step: 8, format: "%.0f tokens")
                SliderRow("Memory", value: $settings.llm.memoryTurns, in: 1...30, step: 1,
                          format: String(localized: "%.0f exchanges"),
                          help: "Previous questions and answers the model keeps.")
            } footer: {
                Text("One model for voice commands and the assistant: changing it reloads the model and clears its cache (the first reply is slower).")
            }
        }
        .formStyle(.grouped)
    }
}

struct VoicePage: View {
    @Binding var settings: VoiceSettings

    var body: some View {
        Form {
            Section("Voice") {
                Picker("Engine", selection: $settings.tts.engine) {
                    ForEach(Choices.ttsEngines, id: \.0) { Text($0.1).tag($0.0) }
                }
                if settings.tts.engine.hasPrefix("qwen3") {
                    Picker("Qwen voice", selection: $settings.tts.qwenSpeaker) {
                        ForEach(Choices.qwenSpeakers, id: \.self) { Text($0).tag($0) }
                    }
                }
                if settings.tts.engine == "kokoro" {
                    Picker("Kokoro voice", selection: $settings.tts.kokoroVoice) {
                        ForEach(Choices.kokoroVoices, id: \.self) { Text(Choices.voiceLabel($0)).tag($0) }
                    }
                    SliderRow("Speed", value: $settings.tts.speed, in: 0.5...2, step: 0.05, format: "×%.2f")
                }
            }
            Section {
                Picker("Mode", selection: $settings.emotion.mode) {
                    ForEach(Choices.emotionModes, id: \.0) { Text($0.1).tag($0.0) }
                }
                .pickerStyle(.segmented)
                if settings.emotion.mode == "fixed" {
                    TextField("Voice style (in English)", text: $settings.emotion.fixedStyle)
                    HStack {
                        ForEach(Choices.fixedStyles, id: \.self) { style in
                            Button(style) { settings.emotion.fixedStyle = style }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                        }
                    }
                }
                if settings.emotion.mode == "auto" {
                    TextEditor(text: $settings.emotion.instructions)
                        .font(.callout)
                        .frame(minHeight: 140)
                    Toggle("Non-verbal sounds (laugh, sigh)", isOn: $settings.emotion.sounds)
                }
            } header: {
                Text("Emotions")
            } footer: {
                Text("Only the Qwen3-TTS engines can express emotion. Automatic: the LLM writes a tone before each sentence.")
            }
        }
        .formStyle(.grouped)
    }
}

struct TurnPage: View {
    @Binding var settings: VoiceSettings

    var body: some View {
        Form {
            Section {
                Toggle(isOn: $settings.turn.turnDetector) {
                    Text("End-of-turn detector (English)")
                    Text("LiveKit model under the LiveKit Model License, not open source: turn it on yourself. When off, silence decides.")
                }
                SliderRow("VAD threshold", value: $settings.turn.vadThreshold, in: 0.1...0.9, step: 0.05,
                          help: "Higher = ignores noise; too high = cuts off soft voices.")
                SliderRow("Minimum silence", value: $settings.turn.minSilence, in: 0.1...1.5, step: 0.05, format: "%.2f s")
                SliderRow("Min wait after the sentence", value: $settings.turn.minEndpointingDelay, in: 0...2,
                          step: 0.05, format: "%.2f s")
                SliderRow("Max wait after the sentence", value: $settings.turn.maxEndpointingDelay, in: 0.5...6,
                          step: 0.1, format: "%.1f s")
            } header: {
                Text("End of turn")
            }
            Section("Interruption") {
                Toggle("Let me interrupt it", isOn: $settings.turn.allowInterruptions)
                SliderRow("Min duration to interrupt", value: $settings.turn.minInterruptionDuration, in: 0.1...2,
                          step: 0.05, format: "%.2f s")
                SliderRow("Min words to interrupt", value: $settings.turn.minInterruptionWords, in: 0...6, step: 1,
                          format: "%.0f")
            }
        }
        .formStyle(.grouped)
    }
}

struct ToolsPage: View {
    @Binding var settings: VoiceSettings

    var body: some View {
        Form {
            Section {
                ForEach(Choices.tools, id: \.0) { tool in
                    Toggle(isOn: Binding(
                        get: { settings.tools.enabled.contains(tool.0) },
                        set: { on in
                            settings.tools.enabled.removeAll { $0 == tool.0 }
                            if on { settings.tools.enabled.append(tool.0) }
                        }
                    )) {
                        Label(tool.1, systemImage: tool.2)
                    }
                }
                Toggle(isOn: $settings.tools.screenContext) {
                    Label("Know what's open on screen", systemImage: "eye")
                }
            } header: {
                Text("Actions on the Mac")
            } footer: {
                Text("Examples: “open Chrome”, “what apps are open”, “search YouTube for lo-fi on my personal account”. Screen context adds one line per question (frontmost app, open apps), without a screenshot.")
            }

            Section {
                ForEach(Choices.chromeProfiles(), id: \.dir) { profile in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(profile.email.isEmpty ? profile.name : profile.email)
                            Text("Chrome · \(profile.dir)").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        TextField("label", text: accountLabel(for: profile.dir))
                            .frame(width: 150)
                            .textFieldStyle(.roundedBorder)
                    }
                }
            } header: {
                Text("Google accounts")
            } footer: {
                Text("Give a label to each account you name out loud (“personal”, “work”). Without a label, “personal” = the Default profile.")
            }

            Section {
                TextField("Project folder", text: $settings.tools.claudeCode.projectDir)
                Picker("Permissions", selection: $settings.tools.claudeCode.permissionMode) {
                    ForEach(Choices.permissionModes, id: \.0) { Text($0.1).tag($0.0) }
                }
                SliderRow("Max duration", value: $settings.tools.claudeCode.timeoutMinutes, in: 1...60, step: 1,
                          format: "%.0f min")
            } header: {
                Text("Claude Code")
            } footer: {
                Text("The assistant hands the task to Claude Code in the background, then announces the result. Claude Code uses Anthropic's cloud. Full reports: web/logs/claude_code.log.")
            }

            ClaudeSessionsSection()
            LearnedSection()

            Section {
                ForEach($settings.tools.mcpServers) { $server in
                    MCPServerRow(server: $server) {
                        settings.tools.mcpServers.removeAll { $0.id == server.id }
                    }
                }
                Button {
                    settings.tools.mcpServers.append(VoiceSettings.MCPServer(name: String(localized: "new server"),
                                                                             url: "http://"))
                } label: {
                    Label("Add an MCP server", systemImage: "plus")
                }
            } header: {
                Text("My MCP tools")
            } footer: {
                Text("HTTP address (http://…/mcp) or local command (e.g. npx -y @modelcontextprotocol/server-filesystem ~/Documents). Limit the allowed tools: a small model gets lost in a long list. Applies from the next conversation.")
            }
        }
        .formStyle(.grouped)
    }

    /// Chrome profile label, read from and written to "accounts" (label -> folder).
    private func accountLabel(for dir: String) -> Binding<String> {
        Binding(
            get: { settings.tools.accounts.first { $0.value == dir }?.key ?? "" },
            set: { label in
                settings.tools.accounts = settings.tools.accounts.filter { $0.value != dir }
                let clean = label.trimmingCharacters(in: .whitespaces).lowercased()
                if !clean.isEmpty { settings.tools.accounts[clean] = dir }
            })
    }
}

struct MCPServerRow: View {
    @Binding var server: VoiceSettings.MCPServer
    let onDelete: () -> Void

    private var isCommand: Bool { server.command != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Toggle("", isOn: Binding(get: { server.enabled ?? true }, set: { server.enabled = $0 }))
                    .labelsHidden()
                TextField("Name", text: $server.name)
                Picker("", selection: Binding(
                    get: { isCommand },
                    set: { command in
                        if command {
                            server.url = nil
                            server.command = server.command ?? ""
                        } else {
                            server.command = nil
                            server.args = nil
                            server.url = server.url ?? "http://"
                        }
                    }
                )) {
                    Text("HTTP").tag(false)
                    Text("Command").tag(true)
                }
                .labelsHidden()
                .frame(width: 130)
                Button(role: .destructive, action: onDelete) { Image(systemName: "trash") }
                    .buttonStyle(.borderless)
            }
            if isCommand {
                TextField("Command and arguments", text: Binding(
                    get: { ([server.command ?? ""] + (server.args ?? [])).joined(separator: " ") },
                    set: { line in
                        let parts = line.split(separator: " ").map(String.init)
                        server.command = parts.first ?? ""
                        server.args = Array(parts.dropFirst())
                    }))
            } else {
                TextField("Address", text: Binding(get: { server.url ?? "" }, set: { server.url = $0 }))
            }
            TextField("Allowed tools (comma-separated, empty = all)", text: Binding(
                get: { (server.allowedTools ?? []).joined(separator: ", ") },
                set: { line in
                    let tools = line.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
                        .filter { !$0.isEmpty }
                    server.allowedTools = tools.isEmpty ? nil : tools
                }))
                .font(.callout)
        }
        .padding(.vertical, 4)
    }
}

/// Frame appearance and dashboard shortcut (common to the three products).
struct AppearancePage: View {
    var settings: Binding<VoiceSettings>?

    var body: some View {
        Form {
            if let settings {
                Section {
                    Picker("Island position", selection: Binding(
                        get: { settings.wrappedValue.ui.islandPosition ?? "notch" },
                        set: { settings.wrappedValue.ui.islandPosition = $0 }
                    )) {
                        Text("Top center, below the camera").tag("notch")
                        Text("Top left").tag("top_left")
                    }
                    Toggle("Short sound when an action is done", isOn: Binding(
                        get: { settings.wrappedValue.ui.actionSounds ?? true },
                        set: { settings.wrappedValue.ui.actionSounds = $0 }
                    ))
                } footer: {
                    Text("Applies the next time the island opens.")
                }
            }
            Section {
                KeyboardShortcuts.Recorder("Open the dashboard:", name: .openDashboard)
            } footer: {
                Text("Each product's shortcuts are on its own page: Dictation, Voice commands, Voice assistant.")
            }
        }
        .formStyle(.grouped)
    }
}

/// Slider with title, value and explanation. Literal title and help are localized like Text("…");
/// already-localized Strings (String(localized:)) go through the String overload.
struct SliderRow: View {
    let title: Text
    @Binding var value: Double
    let range: ClosedRange<Double>
    let step: Double
    let format: String
    let help: Text?

    init(_ title: LocalizedStringKey, value: Binding<Double>, in range: ClosedRange<Double>, step: Double,
         format: String = "%.2f", help: LocalizedStringKey? = nil)
    {
        self.init(title: Text(title), value: value, range: range, step: step, format: format,
                  help: help.map { Text($0) })
    }

    @_disfavoredOverload
    init(_ title: String, value: Binding<Double>, in range: ClosedRange<Double>, step: Double,
         format: String = "%.2f", help: String? = nil)
    {
        self.init(title: Text(verbatim: title), value: value, range: range, step: step, format: format,
                  help: help.map { Text(verbatim: $0) })
    }

    private init(title: Text, value: Binding<Double>, range: ClosedRange<Double>, step: Double, format: String,
                 help: Text?)
    {
        self.title = title
        self._value = value
        self.range = range
        self.step = step
        self.format = format
        self.help = help
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                title
                Spacer()
                Text(String(format: format, value)).monospacedDigit().foregroundStyle(.secondary)
            }
            Slider(value: $value, in: range, step: step)
            if let help {
                help.font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

/// Tasks handed to Claude Code by the assistant: each one reopens in Terminal (claude --resume).
struct ClaudeSessionsSection: View {
    private struct Entry: Decodable, Identifiable {
        let time: String
        let request: String
        let sessionId: String
        let projectDir: String
        var id: String { sessionId + time }
    }

    @State private var entries: [Entry] = []

    var body: some View {
        Section {
            if entries.isEmpty {
                Text("No tasks yet.").foregroundStyle(.secondary)
            }
            ForEach(entries.prefix(8)) { entry in
                HStack {
                    VStack(alignment: .leading) {
                        Text(entry.request).lineLimit(2)
                        Text(entry.time).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Open in Terminal") { open(entry) }
                }
            }
        } header: {
            Text("Claude Code sessions")
        } footer: {
            Text("Reopens the session with its full history: you can read what Claude did and reply to it. By voice: “open the Claude session”, or “tell Claude go ahead” to reply.")
        }
        .onAppear(perform: load)
    }

    private func load() {
        let url = Paths.logs.appendingPathComponent("claude_code_sessions.jsonl")
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        entries = text.split(separator: "\n").reversed().compactMap { try? decoder.decode(Entry.self, from: Data($0.utf8)) }
    }

    /// A .command file opened by Terminal: no macOS permission is needed.
    private func open(_ entry: Entry) {
        let script = Paths.logs.appendingPathComponent("open_claude_session.command")
        let claude = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/claude").path
        let quote = { (s: String) in "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }
        let text = "#!/bin/zsh\ncd \(quote(entry.projectDir)) && exec \(quote(claude)) --resume \(quote(entry.sessionId))\n"
        try? text.write(to: script, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)
        NSWorkspace.shared.open(script)
    }
}

/// What the assistant has learned: "remember…" rules (web/memory.json) and shortcuts replayed without the LLM
/// (web/learned.json). Read when the page opens; deleting rewrites the file.
struct LearnedSection: View {
    @State private var rules: [[String: Any]] = []
    @State private var shortcuts: [(key: String, entry: [String: Any])] = []

    private static let memoryFile = Paths.web.appendingPathComponent("memory.json")
    private static let learnedFile = Paths.web.appendingPathComponent("learned.json")

    var body: some View {
        Section {
            if rules.isEmpty && shortcuts.isEmpty {
                Text("Nothing yet. Say “remember that…”, or “when I say focus mode, close Slack and open Notes”.")
                    .foregroundStyle(.secondary)
            }
            ForEach(Array(rules.enumerated()), id: \.offset) { index, rule in
                row(icon: "brain", title: rule["text"] as? String ?? "", detail: rule["created"] as? String ?? "") {
                    rules.remove(at: index)
                    write(rules, to: Self.memoryFile)
                }
            }
            ForEach(shortcuts, id: \.key) { item in
                let uses = item.entry["uses"] as? Int ?? 0
                let phrase = item.entry["phrase"] as? String ?? item.key
                let actions = (item.entry["calls"] as? [Any])?.count ?? 0
                row(icon: "bolt.fill", title: String(localized: "“\(phrase)”"),
                    detail: String(localized: "\(actions) action(s) · used \(uses) times")) {
                    shortcuts.removeAll { $0.key == item.key }
                    write(Dictionary(uniqueKeysWithValues: shortcuts.map { ($0.key, $0.entry) }), to: Self.learnedFile)
                }
            }
        } header: {
            Text("Memory and learned shortcuts")
        } footer: {
            Text("Shortcuts are replayed in ~0.1 s, without the model. Rules are given to the model with every question.")
        }
        .onAppear(perform: load)
    }

    private func row(icon: String, title: String, detail: String, remove: @escaping () -> Void) -> some View {
        HStack {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).lineLimit(2)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button(role: .destructive, action: remove) { Image(systemName: "trash") }
                .buttonStyle(.borderless)
                .help("Delete")
        }
    }

    private func load() {
        rules = (try? JSONSerialization.jsonObject(with: Data(contentsOf: Self.memoryFile))) as? [[String: Any]] ?? []
        let learned = (try? JSONSerialization.jsonObject(with: Data(contentsOf: Self.learnedFile))) as? [String: Any] ?? [:]
        shortcuts = learned.compactMap { key, value in (value as? [String: Any]).map { (key, $0) } }
            .sorted { ($0.entry["phrase"] as? String ?? "") < ($1.entry["phrase"] as? String ?? "") }
    }

    private func write(_ object: Any, to url: URL) {
        if let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: url, options: .atomic)
        }
    }
}
