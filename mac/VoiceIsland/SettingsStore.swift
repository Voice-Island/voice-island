import Foundation

/// Locations of the local stack (see web/dev.sh).
enum Paths {
    /// The repo's web/ folder, written into the app at build time (Info.plist, VoiceIslandWebDir).
    static let web: URL = {
        if let dir = Bundle.main.object(forInfoDictionaryKey: "VoiceIslandWebDir") as? String, !dir.isEmpty,
           !dir.contains("$(") {
            return URL(fileURLWithPath: dir).standardizedFileURL
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("s2s-voice-assistant/web")
    }()
    static let settings = web.appendingPathComponent("settings.json")
    static let defaults = web.appendingPathComponent("settings.defaults.json")
    static let devScript = web.appendingPathComponent("dev.sh")
    static let logs = web.appendingPathComponent("logs")
    static let localModels = web.appendingPathComponent("models")
}

/// Same settings as web/agent/settings.py (snake_case keys in the file).
struct VoiceSettings: Codable, Equatable {
    struct LLM: Codable, Equatable {
        var model: String
        var systemPrompt: String
        var temperature: Double
        var topP: Double
        var maxTokens: Double
        var memoryTurns: Double
    }

    struct STT: Codable, Equatable {
        /// moonshine | parakeet: voice conversation (engine) and voiceless commands / chat (command_engine).
        var engine: String?
        var commandEngine: String?
        var model: String
        var streaming: Bool
        var vadWindow: Double
        var updateInterval: Double
    }

    struct Turn: Codable, Equatable {
        var vadThreshold: Double
        var minSilence: Double
        var turnDetector: Bool
        var minEndpointingDelay: Double
        var maxEndpointingDelay: Double
        var allowInterruptions: Bool
        var minInterruptionDuration: Double
        var minInterruptionWords: Double
    }

    struct TTS: Codable, Equatable {
        var engine: String
        var kokoroVoice: String
        var qwenSpeaker: String
        var speed: Double
    }

    struct Emotion: Codable, Equatable {
        var mode: String
        var fixedStyle: String
        var instructions: String
        var sounds: Bool
    }

    struct Tools: Codable, Equatable {
        var enabled: [String]
        var screenContext: Bool
        /// Label ("personal", "work") -> Chrome profile folder.
        var accounts: [String: String]
        var claudeCode: ClaudeCode
        var mcpServers: [MCPServer]
        /// Open, close, place, pause, search: run without the LLM (fast_path.py).
        var instantCommands: Bool?
    }

    struct ClaudeCode: Codable, Equatable {
        var projectDir: String
        var permissionMode: String
        var timeoutMinutes: Double
    }

    /// MCP server: HTTP address, or a command run locally (stdio).
    struct MCPServer: Codable, Equatable, Identifiable {
        var id = UUID()
        var name: String = ""
        var url: String?
        var command: String?
        var args: [String]?
        var env: [String: String]?
        var allowedTools: [String]?
        /// When to use it, added to the system prompt (without tool names: see TOOLS_HINT).
        var hint: String?
        var enabled: Bool? = true

        enum CodingKeys: String, CodingKey {
            case name, url, command, args, env, allowedTools, hint, enabled
        }
    }

    struct UI: Codable, Equatable {
        var visualizer: String
        var theme: String
        var showMetrics: Bool
        /// "notch" (below the camera) or "top_left".
        var islandPosition: String?
        var actionSounds: Bool?
    }

    var llm: LLM
    var stt: STT
    var turn: Turn
    var tts: TTS
    var emotion: Emotion
    var tools: Tools
    var ui: UI
    var dictation: DictationSettings?
    var voiceId: VoiceID?

    /// "N'écouter que ma voix" (listen only to my voice), product by product (web/agent/voice_id.py).
    struct VoiceID: Codable, Equatable {
        var dictation: Bool
        var command: Bool
        var assistant: Bool
        var threshold: Double
        var shortGrace: Double

        static let standard = VoiceID(dictation: false, command: false, assistant: false, threshold: 0.39, shortGrace: 20)
    }

    struct DictationSettings: Codable, Equatable {
        var engine: String?
        var language: String
        var cleanup: Bool
        /// shortcut (recorded shortcut, ⌥ N by default) | fn (hold Fn alone).
        var trigger: String?
    }

    /// Settings the agent reads only at the start of a conversation.
    func nextConversationChanges(since saved: VoiceSettings) -> [String] {
        var changes: [String] = []
        if stt != saved.stt { changes.append("transcription") }
        if turn != saved.turn { changes.append("tour de parole") }
        if tools != saved.tools { changes.append("outils") }
        if (emotion.mode == "auto") != (saved.emotion.mode == "auto") || emotion.instructions != saved.emotion.instructions
            || emotion.sounds != saved.emotion.sounds
        {
            changes.append("émotions automatiques")
        }
        return changes
    }
}

/// Reads and writes web/settings.json, merged with the default values published by the agent.
@MainActor
final class SettingsStore: ObservableObject {
    @Published private(set) var saved: VoiceSettings?
    @Published var draft: VoiceSettings?
    @Published private(set) var error: String?

    var isDirty: Bool { draft != nil && draft != saved }

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    /// Frame position, read when it is shown (without going through the dashboard).
    static func islandPosition() -> String { ui("island_position") as? String ?? "notch" }

    static func actionSounds() -> Bool { ui("action_sounds") as? Bool ?? true }

    /// Dictation language ("auto": the Mac's first language) and LLM cleanup.
    static func dictation() -> (engine: String, language: String, cleanup: Bool, trigger: String) {
        let merged = merge((try? readJSON(Paths.defaults)) ?? [:], (try? readJSON(Paths.settings)) ?? [:])
        let d = merged["dictation"] as? [String: Any] ?? [:]
        var language = d["language"] as? String ?? "auto"
        if language == "auto" { language = Locale.preferredLanguages.first ?? "en-US" }
        return (d["engine"] as? String ?? "parakeet", language, d["cleanup"] as? Bool ?? false,
                d["trigger"] as? String ?? "shortcut")
    }

    /// Chosen language model, as mlx_lm.server expects it ("local:…" -> web/models folder).
    static func llmModelPath() -> String {
        let merged = merge((try? readJSON(Paths.defaults)) ?? [:], (try? readJSON(Paths.settings)) ?? [:])
        let model = (merged["llm"] as? [String: Any])?["model"] as? String ?? ""
        guard model.hasPrefix("local:") else { return model }
        return Paths.web.appendingPathComponent("models/" + model.dropFirst("local:".count)).path
    }

    private static func ui(_ key: String) -> Any? {
        let merged = merge((try? readJSON(Paths.defaults)) ?? [:], (try? readJSON(Paths.settings)) ?? [:])
        return (merged["ui"] as? [String: Any])?[key]
    }

    func load() {
        do {
            let merged = Self.merge(try Self.readJSON(Paths.defaults), try Self.readJSON(Paths.settings))
            let data = try JSONSerialization.data(withJSONObject: merged)
            let settings = try decoder.decode(VoiceSettings.self, from: data)
            saved = settings
            draft = settings
            error = nil
        } catch {
            self.error = "Réglages illisibles (\(error.localizedDescription)). Lance une fois web/dev.sh."
        }
    }

    func revert() { draft = saved }

    func resetToDefaults() {
        guard let data = try? JSONSerialization.data(withJSONObject: (try? Self.readJSON(Paths.defaults)) ?? [:]),
            let defaults = try? decoder.decode(VoiceSettings.self, from: data)
        else { return }
        draft = defaults
    }

    func save() {
        guard let draft else { return }
        do {
            let edited = try JSONSerialization.jsonObject(with: try encoder.encode(draft)) as? [String: Any] ?? [:]
            // Keys unknown to the app (added on the agent side) are kept.
            var merged = Self.merge(try Self.readJSON(Paths.settings), edited)
            // Account labels are replaced, not merged: a deleted label disappears.
            if var tools = merged["tools"] as? [String: Any], let edits = edited["tools"] as? [String: Any] {
                tools["accounts"] = edits["accounts"]
                merged["tools"] = tools
            }
            let data = try JSONSerialization.data(withJSONObject: merged, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: Paths.settings, options: .atomic)
            saved = draft
            error = nil
        } catch {
            self.error = "Enregistrement impossible : \(error.localizedDescription)"
        }
    }

    private static func readJSON(_ url: URL) throws -> [String: Any] {
        guard let data = try? Data(contentsOf: url) else { return [:] }
        return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    }

    private static func merge(_ base: [String: Any], _ override: [String: Any]) -> [String: Any] {
        var out = base
        for (key, value) in override {
            if let child = value as? [String: Any], let current = out[key] as? [String: Any] {
                out[key] = merge(current, child)
            } else {
                out[key] = value
            }
        }
        return out
    }
}

/// Choice lists offered in the dashboard.
enum Choices {
    static var llmModels: [(String, String)] {
        var models: [(String, String)] = []
        for (folder, label) in [
            ("Qwen2.5-3B-Instruct-4bit-tools", "Qwen2.5 3B · outils (licence non commerciale)"),
            ("Qwen2.5-1.5B-Instruct-4bit-tools", "Qwen2.5 1.5B · outils, plus rapide"),
        ] {
            let path = Paths.localModels.appendingPathComponent(folder)
            if FileManager.default.fileExists(atPath: path.path) { models.append(("local:\(folder)", label)) }
        }
        return [("mlx-community/Qwen3-4B-Instruct-2507-4bit", "Qwen3 4B · outils, le plus fiable")] + models + [
            // On trial (Oct 29): smaller, native tool calls in mlx_lm (web/test/tool_eval.py).
            ("openbmb/MiniCPM5-2B-MLX", "MiniCPM5 2B · outils, à l'essai"),
            ("mlx-community/Qwen3.5-2B-MLX-8bit", "Qwen3.5 2B · outils, français, à l'essai"),
            ("mlx-community/Qwen3.5-4B-OptiQ-4bit", "Qwen3.5 4B · outils, français, à l'essai"),
            ("LiquidAI/LFM2.5-1.2B-Instruct-MLX-4bit", "LFM2.5 1.2B · le plus rapide, à l'essai"),
            ("mlx-community/Qwen2.5-0.5B-Instruct-4bit", "Qwen2.5 0.5B · très rapide, sans outils"),
            ("mlx-community/Qwen2.5-7B-Instruct-4bit", "Qwen2.5 7B · meilleur, plus lent"),
            ("mlx-community/Llama-3.2-3B-Instruct-4bit", "Llama 3.2 3B · sans outils"),
            ("mlx-community/gemma-3-4b-it-qat-4bit", "Gemma 3 4B · sans outils"),
        ]
    }

    static let sttEngines = [("moonshine", "Moonshine · anglais, texte en direct, le plus rapide"),
                             ("parakeet", "Parakeet v3 (NVIDIA) · français et anglais")]
    static let sttModels = [("tiny", "Tiny · le plus rapide"), ("small", "Small · plus précis"),
                            ("medium", "Medium · le plus précis, lourd")]
    static let ttsEngines = [("kokoro", "Kokoro · le plus rapide, sans émotion"),
                             ("qwen3-0.6b", "Qwen3-TTS 0.6B · émotions"),
                             ("qwen3-1.7b", "Qwen3-TTS 1.7B · émotions, plus lent"),
                             ("chatterbox-turbo", "Chatterbox Turbo")]
    static let qwenSpeakers = ["Ryan", "Aiden", "Vivian", "Serena", "Dylan", "Eric", "Uncle_Fu"]
    static let kokoroVoices = ["af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore", "af_nicole",
                               "af_nova", "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric",
                               "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa", "bf_alice",
                               "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis"]
    static let emotionModes = [("off", "Désactivées"), ("fixed", "Style fixe"), ("auto", "Automatiques")]
    static let fixedStyles = ["gently warm and calm", "really bright and upbeat", "soft, almost a whisper",
                              "calm and professional", "a little playful, friendly"]
    static let permissionModes = [("plan", "Lecture seule (analyse, propose)"),
                                  ("acceptEdits", "Peut modifier les fichiers du projet")]

    static let tools = [("open_app", "Ouvrir une application", "app.badge"),
                        ("quit_app", "Quitter une application", "xmark.app"),
                        ("place_window", "Placer une fenêtre (gauche, droite, plein écran)", "rectangle.split.2x1"),
                        ("read_selection", "Lire le texte sélectionné (« summarize this »)", "text.viewfinder"),
                        ("insert_text", "Écrire au curseur (réponse, traduction, jamais envoyé)", "character.cursor.ibeam"),
                        ("read_screen", "Lire la fenêtre affichée (texte, OCR local)", "macwindow"),
                        ("look_at_screen", "Regarder l'écran (image, modèle de vision local, ~2,5 Go)", "eye"),
                        ("calendar_events", "Calendrier (déplacer / supprimer : confirmation)", "calendar"),
                        ("add_reminder", "Ajouter un rappel", "checklist"),
                        ("open_website", "Ouvrir un site web", "safari"),
                        ("search_web", "Chercher sur le web", "magnifyingglass"),
                        ("open_folder", "Ouvrir un dossier", "folder"),
                        ("set_volume", "Régler le volume", "speaker.wave.2"),
                        ("get_current_time", "Donner l'heure et la date", "clock"),
                        ("list_open_apps", "Voir les applications ouvertes", "macwindow.on.rectangle"),
                        ("search_youtube", "Chercher sur YouTube (onglet ouvert réutilisé)", "play.rectangle"),
                        ("play_youtube_video", "Lancer une vidéo YouTube", "play.circle"),
                        ("draft_email", "Brouillon Gmail (jamais envoyé)", "envelope"),
                        ("ask_claude_code", "Déléguer à Claude Code (cloud)", "chevron.left.forwardslash.chevron.right"),
                        ("open_claude_session", "Ouvrir la session Claude Code dans Terminal", "terminal"),
                        ("open_terminal", "Nouveau Terminal (avec Claude Code au besoin)", "apple.terminal"),
                        ("media_control", "Pause, lecture, suivant, plein écran", "playpause"),
                        ("close_tab", "Fermer l'onglet Chrome affiché", "xmark.square"),
                        ("search_work_memory", "Mémoire de travail (claude-mem, si installé)", "brain")]

    /// Chrome profiles (= Google accounts) read from "Local State".
    static func chromeProfiles() -> [(dir: String, name: String, email: String)] {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Google/Chrome/Local State")
        guard let data = try? Data(contentsOf: url),
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let cache = (json["profile"] as? [String: Any])?["info_cache"] as? [String: [String: Any]]
        else { return [] }
        return cache.map { (dir: $0.key, name: $0.value["name"] as? String ?? "", email: $0.value["user_name"] as? String ?? "") }
            .sorted { $0.dir < $1.dir }
    }

    /// "af_heart" -> "Heart · US · femme".
    static func voiceLabel(_ id: String) -> String {
        let parts = id.split(separator: "_", maxSplits: 1)
        guard parts.count == 2, parts[0].count == 2 else { return id }
        let prefix = Array(parts[0])
        return "\(parts[1].capitalized) · \(prefix[0] == "b" ? "UK" : "US") · \(prefix[1] == "m" ? "homme" : "femme")"
    }
}
