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
            case .status: return "État"
            case .machine: return "Ton Mac"
            case .test: return "Tester"
            case .dictation: return "Dictée"
            case .command: return "Commandes vocales"
            case .assistant: return "Assistant vocal"
            case .voice: return "Voix et émotions"
            case .turn: return "Tour de parole"
            case .llm: return "Modèle de langage"
            case .tools: return "Outils"
            case .myVoice: return "Ma voix"
            case .appearance: return "Apparence"
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
            ("Dictée (⌥ N par défaut)", [.dictation]),
            ("Commandes vocales, sans voix", [.command]),
            ("Assistant vocal", [.assistant, .voice, .turn]),
            ("Commun aux trois", [.llm, .tools, .myVoice, .appearance]),
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
            ContentUnavailableView("Réglages indisponibles", systemImage: "exclamationmark.triangle",
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
                Text("Modifications non enregistrées").font(.callout.weight(.semibold))
                Text(later.isEmpty ? "Appliqué dès la prochaine réponse"
                     : "À la prochaine conversation : " + later.joined(separator: ", "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Annuler") { store.revert() }
            Button("Enregistrer") { store.save() }
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
            Section("Ce Mac") {
                LabeledContent("Puce", value: info.chip + (info.gpuCores.map { ", GPU \($0) cœurs" } ?? ""))
                LabeledContent("Mémoire", value: "\(info.memoryGB) Go")
                LabeledContent("Mémoire utilisable par le GPU", value: String(format: "%.1f Go", info.gpuBudgetGB))
                LabeledContent("Espace disque libre", value: String(format: "%.0f Go", info.freeDiskGB))
            }
            Section("Ce qui tourne bien ici") {
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
                    LabeledContent("Pack « \(pack.title) »", value: String(format: "≈ %.0f Go à télécharger", pack.downloadGB))
                    Text(pack.summary).font(.caption).foregroundStyle(.secondary)
                } header: {
                    Text("Recommandé pour ce Mac")
                } footer: {
                    Text("Un seul modèle de langage sert aux commandes et à l'assistant : deux modèles chargés à la fois "
                         + "prendraient deux fois plus de mémoire.")
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
                        Text(service.isUp == nil ? "…" : (service.isUp! ? "En marche" : "Arrêté"))
                            .foregroundStyle(.secondary)
                    }
                }
            } header: {
                Text("Services locaux")
            } footer: {
                Text(monitor.allUp ? "Tout tourne : ⌥ Espace pour parler." : "L'assistant a besoin des quatre premiers services.")
            }
            Section {
                HStack {
                    Button {
                        monitor.startAll()
                    } label: {
                        Label("Démarrer tout", systemImage: "play.fill")
                    }
                    .disabled(monitor.busy != nil)
                    Button {
                        monitor.stopAll()
                    } label: {
                        Label("Tout arrêter", systemImage: "stop.fill")
                    }
                    .disabled(monitor.busy != nil)
                    if let busy = monitor.busy {
                        ProgressView().controlSize(.small)
                        Text(busy).foregroundStyle(.secondary)
                    }
                }
                HStack {
                    Button("Ouvrir les journaux") { NSWorkspace.shared.open(Paths.logs) }
                    Button("Interface web") { NSWorkspace.shared.open(URL(string: "http://localhost:3010")!) }
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
                    Label(controller.isActive ? "Arrêter" : "Parler à l'assistant",
                          systemImage: controller.isActive ? "stop.fill" : "mic.fill")
                        .frame(minWidth: 180)
                }
                .controlSize(.large)
                .buttonStyle(.borderedProminent)
                if controller.isActive {
                    Button {
                        controller.toggleMute()
                    } label: {
                        Label(controller.micMuted ? "Rallumer le micro" : "Couper le micro",
                              systemImage: controller.micMuted ? "mic.slash.fill" : "mic.fill")
                    }
                    .controlSize(.large)
                    .tint(controller.micMuted ? .red : nil)
                }
                if controller.isActive {
                    Button {
                        controller.toggleVoiceReplies()
                    } label: {
                        Label(controller.voiceReplies ? "Réponses en texte" : "Réponses à voix haute",
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
                ContentUnavailableView("Aucune conversation", systemImage: "waveform",
                                       description: Text("Lance une conversation ici ou avec ⌥ Espace."))
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
            Text("fin de tour \(Int(metrics.eouMs))")
            Text("LLM \(Int(metrics.llmMs ?? 0))")
            Text("voix \(Int(metrics.ttsMs))")
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
                Picker("Déclencheur", selection: Binding(get: { dictation.wrappedValue.trigger ?? "shortcut" },
                                                         set: { dictation.wrappedValue.trigger = $0 })) {
                    Text("Raccourci ci-dessous").tag("shortcut")
                    Text("Maintenir Fn seule").tag("fn")
                }
                if (dictation.wrappedValue.trigger ?? "shortcut") == "shortcut" {
                    KeyboardShortcuts.Recorder("Dictée (maintenir) :", name: .dictate)
                }
            } footer: {
                Text((dictation.wrappedValue.trigger ?? "shortcut") == "fn"
                     ? "Maintiens Fn, parle, relâche. Fn avec une autre touche (flèches, F1…) ou un appui bref annule. "
                       + "Dans Réglages Système › Clavier, règle « Appuyer sur la touche 🌐 pour » sur « Ne rien "
                       + "faire », et retire Fn des autres apps de dictée."
                     : "Maintiens le raccourci, parle, relâche : le texte s'écrit là où se trouve le curseur, dans "
                       + "n'importe quelle app, sans être envoyé.")
            }
            Section("Reconnaissance") {
                Picker("Moteur", selection: Binding(get: { dictation.wrappedValue.engine ?? "parakeet" },
                                                    set: { dictation.wrappedValue.engine = $0 })) {
                    Text("Parakeet v3 (local, open source, 25 langues)").tag("parakeet")
                    Text("Apple (demande la Dictée de macOS)").tag("apple")
                }
                Picker("Langue", selection: dictation.language) {
                    Text("Langue du Mac").tag("auto")
                    Text("Français").tag("fr-FR")
                    Text("English").tag("en-US")
                    Text("Deutsch (Schweiz)").tag("de-CH")
                }
                Toggle("Retirer aussi les hésitations (LLM, plus lent)", isOn: dictation.cleanup)
                Text("Parakeet devine la langue tout seul ; la langue choisie sert au moteur Apple et au repli Whisper "
                     + "quand Parakeet se trompe d'alphabet.")
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
                KeyboardShortcuts.Recorder("Commandes sans voix :", name: .commandMode)
                KeyboardShortcuts.Recorder("Écrire (chat) :", name: .chatMode)
            } footer: {
                Text("Une fois pour ouvrir, une deuxième fois pour fermer. Pendant une conversation vocale, le même "
                     + "raccourci coupe ou rallume la voix des réponses sans perdre le fil.")
            }
            Section("Transcription") {
                Picker("Moteur", selection: Binding(get: { settings.stt.commandEngine ?? "parakeet" },
                                                    set: { settings.stt.commandEngine = $0 })) {
                    ForEach(Choices.sttEngines, id: \.0) { Text($0.1).tag($0.0) }
                }
                Text("Parakeet comprend le français, l'anglais et l'allemand ; il transcrit la phrase entière après le "
                     + "silence. Moonshine n'entend que l'anglais, mais affiche le texte en direct.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Toggle("Commandes instantanées, sans le modèle", isOn: Binding(
                    get: { settings.tools.instantCommands ?? true }, set: { settings.tools.instantCommands = $0 }))
            } footer: {
                Text("Ouvrir, fermer, placer une fenêtre (« Chrome à gauche »), pause, recherche : exécuté en ~0,1 s, "
                     + "aussi en français. Le reste passe par le modèle de langage.")
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
                KeyboardShortcuts.Recorder("Parler à l'assistant :", name: .toggleAssistant)
                KeyboardShortcuts.Recorder("Couper / rallumer le micro :", name: .toggleMute)
            } footer: {
                Text("Une fois pour ouvrir, une deuxième fois pour fermer.")
            }
            Section("Transcription") {
                Picker("Moteur", selection: Binding(get: { settings.stt.engine ?? "moonshine" },
                                                    set: { settings.stt.engine = $0 })) {
                    ForEach(Choices.sttEngines, id: \.0) { Text($0.1).tag($0.0) }
                }
                if (settings.stt.engine ?? "moonshine") == "moonshine" {
                    Picker("Modèle Moonshine", selection: $settings.stt.model) {
                        ForEach(Choices.sttModels, id: \.0) { Text($0.1).tag($0.0) }
                    }
                    Toggle("Transcription en direct", isOn: $settings.stt.streaming)
                    SliderRow("Silence de fin de phrase", value: $settings.stt.vadWindow, in: 0.1...1, step: 0.05,
                              format: "%.2f s", help: "Plus court = plus rapide, risque de couper.")
                    SliderRow("Intervalle de mise à jour", value: $settings.stt.updateInterval, in: 0.1...1, step: 0.05,
                              format: "%.2f s")
                } else {
                    Text("Parakeet : français, anglais, allemand ; phrase entière transcrite après le silence, fin de "
                         + "tour décidée par le silence.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Section {
                TextEditor(text: $settings.llm.systemPrompt)
                    .font(.body)
                    .frame(minHeight: 160)
            } header: {
                Text("Instructions données au modèle")
            } footer: {
                Text("Appliqué dès la prochaine réponse. Le mode d'emploi des outils est ajouté automatiquement.")
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
                Picker("Modèle", selection: $settings.llm.model) {
                    ForEach(Choices.llmModels, id: \.0) { Text($0.1).tag($0.0) }
                    if !Choices.llmModels.contains(where: { $0.0 == settings.llm.model }) {
                        Text(settings.llm.model).tag(settings.llm.model)
                    }
                }
                SliderRow("Température", value: $settings.llm.temperature, in: 0...2, step: 0.05,
                          help: "0 = toujours la même réponse ; plus haut = plus varié.")
                SliderRow("Top-p", value: $settings.llm.topP, in: 0.05...1, step: 0.05)
                SliderRow("Longueur max", value: $settings.llm.maxTokens, in: 16...512, step: 8, format: "%.0f tokens")
                SliderRow("Mémoire", value: $settings.llm.memoryTurns, in: 1...30, step: 1, format: "%.0f échanges",
                          help: "Questions et réponses précédentes gardées par le modèle.")
            } footer: {
                Text("Un seul modèle pour les commandes vocales et l'assistant : en changer recharge le modèle et vide "
                     + "son cache (la première réponse est plus lente).")
            }
        }
        .formStyle(.grouped)
    }
}

struct VoicePage: View {
    @Binding var settings: VoiceSettings

    var body: some View {
        Form {
            Section("Voix") {
                Picker("Moteur", selection: $settings.tts.engine) {
                    ForEach(Choices.ttsEngines, id: \.0) { Text($0.1).tag($0.0) }
                }
                if settings.tts.engine.hasPrefix("qwen3") {
                    Picker("Voix Qwen", selection: $settings.tts.qwenSpeaker) {
                        ForEach(Choices.qwenSpeakers, id: \.self) { Text($0).tag($0) }
                    }
                }
                if settings.tts.engine == "kokoro" {
                    Picker("Voix Kokoro", selection: $settings.tts.kokoroVoice) {
                        ForEach(Choices.kokoroVoices, id: \.self) { Text(Choices.voiceLabel($0)).tag($0) }
                    }
                    SliderRow("Vitesse", value: $settings.tts.speed, in: 0.5...2, step: 0.05, format: "×%.2f")
                }
            }
            Section {
                Picker("Mode", selection: $settings.emotion.mode) {
                    ForEach(Choices.emotionModes, id: \.0) { Text($0.1).tag($0.0) }
                }
                .pickerStyle(.segmented)
                if settings.emotion.mode == "fixed" {
                    TextField("Style de voix (en anglais)", text: $settings.emotion.fixedStyle)
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
                    Toggle("Sons non verbaux (rire, soupir)", isOn: $settings.emotion.sounds)
                }
            } header: {
                Text("Émotions")
            } footer: {
                Text("Seuls les moteurs Qwen3-TTS jouent une émotion. Automatique : le LLM écrit un ton avant chaque phrase.")
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
                    Text("Détecteur de fin de tour (anglais)")
                    Text("Modèle de LiveKit sous LiveKit Model License, pas open source : à activer soi-même. Désactivé, le silence décide.")
                }
                SliderRow("Seuil du VAD", value: $settings.turn.vadThreshold, in: 0.1...0.9, step: 0.05,
                          help: "Plus haut = ignore les bruits ; trop haut = coupe les voix douces.")
                SliderRow("Silence minimal", value: $settings.turn.minSilence, in: 0.1...1.5, step: 0.05, format: "%.2f s")
                SliderRow("Attente min après la phrase", value: $settings.turn.minEndpointingDelay, in: 0...2,
                          step: 0.05, format: "%.2f s")
                SliderRow("Attente max après la phrase", value: $settings.turn.maxEndpointingDelay, in: 0.5...6,
                          step: 0.1, format: "%.1f s")
            } header: {
                Text("Fin de tour")
            }
            Section("Interruption") {
                Toggle("Me laisser l'interrompre", isOn: $settings.turn.allowInterruptions)
                SliderRow("Durée min pour interrompre", value: $settings.turn.minInterruptionDuration, in: 0.1...2,
                          step: 0.05, format: "%.2f s")
                SliderRow("Mots min pour interrompre", value: $settings.turn.minInterruptionWords, in: 0...6, step: 1,
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
                    Label("Savoir ce qui est ouvert à l'écran", systemImage: "eye")
                }
            } header: {
                Text("Actions sur le Mac")
            } footer: {
                Text("Exemples : « open Chrome », « what apps are open », « search YouTube for lo-fi on my personal account ». Le contexte écran ajoute une ligne par question (app au premier plan, apps ouvertes), sans capture d'écran.")
            }

            Section {
                ForEach(Choices.chromeProfiles(), id: \.dir) { profile in
                    HStack {
                        VStack(alignment: .leading) {
                            Text(profile.email.isEmpty ? profile.name : profile.email)
                            Text("Chrome · \(profile.dir)").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        TextField("étiquette", text: accountLabel(for: profile.dir))
                            .frame(width: 150)
                            .textFieldStyle(.roundedBorder)
                    }
                }
            } header: {
                Text("Comptes Google")
            } footer: {
                Text("Donne une étiquette au compte que tu nommes à l'oral (« personal », « work »). Sans étiquette, « personal » = profil Default.")
            }

            Section {
                TextField("Dossier du projet", text: $settings.tools.claudeCode.projectDir)
                Picker("Droits", selection: $settings.tools.claudeCode.permissionMode) {
                    ForEach(Choices.permissionModes, id: \.0) { Text($0.1).tag($0.0) }
                }
                SliderRow("Durée max", value: $settings.tools.claudeCode.timeoutMinutes, in: 1...60, step: 1,
                          format: "%.0f min")
            } header: {
                Text("Claude Code")
            } footer: {
                Text("L'assistant transmet la tâche à Claude Code en arrière-plan puis annonce le résultat. Claude Code utilise le cloud d'Anthropic. Rapports complets : web/logs/claude_code.log.")
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
                    settings.tools.mcpServers.append(VoiceSettings.MCPServer(name: "nouveau serveur", url: "http://"))
                } label: {
                    Label("Ajouter un serveur MCP", systemImage: "plus")
                }
            } header: {
                Text("Mes outils MCP")
            } footer: {
                Text("Adresse HTTP (http://…/mcp) ou commande locale (ex. npx -y @modelcontextprotocol/server-filesystem ~/Documents). Limite les outils autorisés : un petit modèle se perd avec une longue liste. Pris en compte à la prochaine conversation.")
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
                TextField("Nom", text: $server.name)
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
                    Text("Commande").tag(true)
                }
                .labelsHidden()
                .frame(width: 130)
                Button(role: .destructive, action: onDelete) { Image(systemName: "trash") }
                    .buttonStyle(.borderless)
            }
            if isCommand {
                TextField("Commande et arguments", text: Binding(
                    get: { ([server.command ?? ""] + (server.args ?? [])).joined(separator: " ") },
                    set: { line in
                        let parts = line.split(separator: " ").map(String.init)
                        server.command = parts.first ?? ""
                        server.args = Array(parts.dropFirst())
                    }))
            } else {
                TextField("Adresse", text: Binding(get: { server.url ?? "" }, set: { server.url = $0 }))
            }
            TextField("Outils autorisés (séparés par des virgules, vide = tous)", text: Binding(
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
                    Picker("Position du cadre", selection: Binding(
                        get: { settings.wrappedValue.ui.islandPosition ?? "notch" },
                        set: { settings.wrappedValue.ui.islandPosition = $0 }
                    )) {
                        Text("En haut au centre, sous la caméra").tag("notch")
                        Text("En haut à gauche").tag("top_left")
                    }
                    Toggle("Petit son quand une action est faite", isOn: Binding(
                        get: { settings.wrappedValue.ui.actionSounds ?? true },
                        set: { settings.wrappedValue.ui.actionSounds = $0 }
                    ))
                } footer: {
                    Text("Pris en compte à la prochaine ouverture du cadre.")
                }
            }
            Section {
                KeyboardShortcuts.Recorder("Ouvrir le tableau de bord :", name: .openDashboard)
            } footer: {
                Text("Les raccourcis de chaque produit sont sur sa page : Dictée, Commandes vocales, Assistant vocal.")
            }
        }
        .formStyle(.grouped)
    }
}

/// Slider with title, value and explanation.
struct SliderRow: View {
    let title: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    let step: Double
    let format: String
    let help: String?

    init(_ title: String, value: Binding<Double>, in range: ClosedRange<Double>, step: Double,
         format: String = "%.2f", help: String? = nil)
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
                Text(title)
                Spacer()
                Text(String(format: format, value)).monospacedDigit().foregroundStyle(.secondary)
            }
            Slider(value: $value, in: range, step: step)
            if let help {
                Text(help).font(.caption).foregroundStyle(.secondary)
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
                Text("Aucune tâche pour l'instant.").foregroundStyle(.secondary)
            }
            ForEach(entries.prefix(8)) { entry in
                HStack {
                    VStack(alignment: .leading) {
                        Text(entry.request).lineLimit(2)
                        Text(entry.time).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Ouvrir dans Terminal") { open(entry) }
                }
            }
        } header: {
            Text("Sessions Claude Code")
        } footer: {
            Text("Rouvre la session avec tout son historique : tu peux lire ce que Claude a fait et lui répondre. À la voix : « open the Claude session », ou « tell Claude go ahead » pour lui répondre.")
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
                Text("Rien pour l'instant. Dis « remember that… », ou « when I say focus mode, close Slack and open Notes ».")
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
                row(icon: "bolt.fill", title: "« \(item.entry["phrase"] as? String ?? item.key) »",
                    detail: "\((item.entry["calls"] as? [Any])?.count ?? 0) action(s) · utilisé \(uses) fois") {
                    shortcuts.removeAll { $0.key == item.key }
                    write(Dictionary(uniqueKeysWithValues: shortcuts.map { ($0.key, $0.entry) }), to: Self.learnedFile)
                }
            }
        } header: {
            Text("Mémoire et raccourcis appris")
        } footer: {
            Text("Les raccourcis sont rejoués en ~0,1 s, sans le modèle. Les règles sont rappelées au modèle à chaque question.")
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
                .help("Supprimer")
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
