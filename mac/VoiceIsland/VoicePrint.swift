import AVFoundation
import SwiftUI

/// Products where "N'écouter que ma voix" (listen only to my voice) is set separately.
enum VoiceProduct: String {
    case dictation, command, assistant

    var keyPath: WritableKeyPath<VoiceSettings.VoiceID, Bool> {
        switch self {
        case .dictation: return \.dictation
        case .command: return \.command
        case .assistant: return \.assistant
        }
    }
}

/// Voiceprint, held by the voice server (web/agent/voice_id.py, macOS keychain): the app keeps none of it.
@MainActor
final class VoicePrintStore: ObservableObject {
    static let shared = VoicePrintStore()

    @Published private(set) var enrolled = false
    @Published private(set) var reachable = true

    private static let base = "http://127.0.0.1:8090/voice_id"

    func refresh() async {
        guard let url = URL(string: Self.base),
            let (data, _) = try? await URLSession.shared.data(from: url),
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            reachable = false
            return
        }
        reachable = true
        enrolled = json["enrolled"] as? Bool ?? false
    }

    /// Readings end to end (16 kHz mono) -> voiceprint. nil if all went well, otherwise the error message.
    func enroll(_ samples: [Float]) async -> String? {
        let json = await Self.post("/enroll", samples)
        await refresh()
        if json?["ok"] as? Bool == true { return nil }
        return json?["error"] as? String ?? "le serveur de voix ne répond pas (web/dev.sh lancé ?)"
    }

    /// -> (score, recognized); score is nil if there was no voice.
    func score(_ samples: [Float]) async -> (Double?, Bool)? {
        guard let json = await Self.post("/score", samples) else { return nil }
        return (json["score"] as? Double, json["accepted"] as? Bool ?? false)
    }

    func erase() async {
        guard let url = URL(string: Self.base) else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        _ = try? await URLSession.shared.data(for: request)
        await refresh()
    }

    private static func post(_ path: String, _ samples: [Float]) async -> [String: Any]? {
        guard let url = URL(string: base + path) else { return nil }
        var request = URLRequest(url: url, timeoutInterval: 60)
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.httpBody = samples.withUnsafeBufferPointer { Data(buffer: $0) }
        guard let (data, _) = try? await URLSession.shared.data(for: request) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }
}

/// Mic -> 16 kHz mono, between start() and stop(). Same precautions as dictation: voice processing after a
/// conversation (otherwise nothing arrives), turned off and engine released on stop (otherwise other apps go deaf).
@MainActor
final class VoiceRecorder {
    private var engine = AVAudioEngine()
    private var recorder: Recorder?

    func start(voiceProcessing: Bool) throws {
        let input = engine.inputNode
        if voiceProcessing {
            try? input.setVoiceProcessingEnabled(true)
            input.voiceProcessingOtherAudioDuckingConfiguration =
                AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
        }
        let recorder = Recorder()
        self.recorder = recorder
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
            recorder.add(buffer)
        }
        engine.prepare()
        try engine.start()
    }

    func stop() -> [Float] {
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        if engine.inputNode.isVoiceProcessingEnabled { try? engine.inputNode.setVoiceProcessingEnabled(false) }
        engine = AVAudioEngine()
        defer { recorder = nil }
        return recorder?.samples ?? []
    }
}

/// A product's "N'écouter que ma voix" toggle, on its dashboard page.
struct VoiceIDSection: View {
    @Binding var settings: VoiceSettings
    let product: VoiceProduct
    @ObservedObject private var store = VoicePrintStore.shared

    var body: some View {
        Section {
            Toggle("N'écouter que ma voix", isOn: Binding(
                get: { (settings.voiceId ?? .standard)[keyPath: product.keyPath] },
                set: {
                    var value = settings.voiceId ?? .standard
                    value[keyPath: product.keyPath] = $0
                    settings.voiceId = value
                }
            ))
            .disabled(!store.enrolled)
        } header: {
            Text("Biométrie vocale")
        } footer: {
            Text(store.enrolled
                 ? "Une autre voix est ignorée en silence : ni transcrite, ni affichée. Empreinte, seuil et test : page « Ma voix ». "
                   + "Ce n'est pas un verrou : un enregistrement de ta voix passerait."
                 : "Enregistre d'abord ta voix dans la page « Ma voix ».")
        }
        .task { await store.refresh() }
    }
}

/// "Ma voix" page: record the voiceprint (three readings), test it, adjust the threshold, erase it.
struct MyVoicePage: View {
    @Binding var settings: VoiceSettings
    @ObservedObject var controller: AssistantController
    @ObservedObject private var store = VoicePrintStore.shared

    private static let readings = [
        "Bonjour, voici ma voix. J'utilise cet assistant tous les jours pour ouvrir mes applications, écrire mes "
            + "messages et chercher des informations sur le web.",
        "Hello, this is my normal speaking voice. I use this assistant every day to open my apps, move my windows "
            + "around, search the web and play some music while I work.",
        "Le matin, je commence par lire mes emails, puis je prépare mes présentations. Ensuite je passe des appels "
            + "et je prends des notes pendant les réunions.",
    ]

    @State private var recorder = VoiceRecorder()
    @State private var step: Int?            // current reading (0, 1, 2), nil when not recording
    @State private var recording = false
    @State private var startedAt = Date()
    @State private var clips: [[Float]] = []
    @State private var testing = false
    @State private var message = ""
    @State private var confirmErase = false

    var body: some View {
        Form {
            Section {
                LabeledContent("Empreinte", value: !store.reachable ? "serveur de voix arrêté"
                               : store.enrolled ? "enregistrée" : "aucune")
                if let step {
                    Text("Lecture \(step + 1) sur \(Self.readings.count) : lis ce texte à voix normale.")
                        .font(.callout.weight(.semibold))
                    Text(Self.readings[step])
                        .font(.title3)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
                    HStack {
                        Button(recording ? "Arrêter" : "Commencer") { toggleReading() }
                            .buttonStyle(.borderedProminent)
                        if recording {
                            TimelineView(.periodic(from: startedAt, by: 0.5)) { context in
                                Label("\(Int(context.date.timeIntervalSince(startedAt))) s", systemImage: "record.circle")
                                    .foregroundStyle(.red)
                            }
                        }
                        Spacer()
                        Button("Annuler") { cancelEnrollment() }
                    }
                } else {
                    Button(store.enrolled ? "Refaire mon empreinte" : "Enregistrer ma voix") { beginEnrollment() }
                        .disabled(!store.reachable || testing)
                }
            } header: {
                Text("Mon empreinte vocale")
            } footer: {
                Text("Trois lectures d'environ 15 s, avec le micro que tu utilises d'habitude. L'empreinte (256 nombres, "
                     + "pas le son) reste sur ce Mac, dans le trousseau macOS ; les lectures ne sont pas gardées.")
            }

            Section {
                HStack {
                    Button(testing ? "Arrêter et vérifier" : "Tester : dis une phrase") { toggleTest() }
                        .disabled(!store.enrolled || step != nil)
                    if !message.isEmpty { Text(message).foregroundStyle(.secondary) }
                }
                SliderRow("Seuil", value: Binding(
                    get: { (settings.voiceId ?? .standard).threshold },
                    set: { var v = settings.voiceId ?? .standard; v.threshold = $0; settings.voiceId = v }
                ), in: 0.2...0.7, step: 0.01, format: "%.2f",
                          help: "Plus haut : les autres voix passent encore moins, mais tes mots très courts risquent d'être refusés. Mesures : ta voix ≥ 0,42, autres voix ≤ 0,36.")
            } header: {
                Text("Tester et régler")
            }

            Section {
                ForEach([(VoiceProduct.dictation, "Dictée"), (.command, "Commandes vocales"), (.assistant, "Assistant vocal")],
                        id: \.0.rawValue) { product, title in
                    Toggle(title, isOn: Binding(
                        get: { (settings.voiceId ?? .standard)[keyPath: product.keyPath] },
                        set: { var v = settings.voiceId ?? .standard; v[keyPath: product.keyPath] = $0; settings.voiceId = v }
                    ))
                    .disabled(!store.enrolled)
                }
            } header: {
                Text("N'écouter que ma voix pour…")
            } footer: {
                Text("Une autre voix est ignorée en silence : elle n'est ni transcrite ni affichée. Un mot très court "
                     + "(« Stop ») est accepté s'il suit de près une phrase reconnue. Ce n'est pas un verrou : un "
                     + "enregistrement ou une imitation de ta voix peut passer.")
            }

            Section {
                Button("Effacer mon empreinte", role: .destructive) { confirmErase = true }
                    .disabled(!store.enrolled)
            }
        }
        .formStyle(.grouped)
        .task { await store.refresh() }
        .confirmationDialog("Effacer ton empreinte vocale ?", isPresented: $confirmErase) {
            Button("Effacer", role: .destructive) {
                Task {
                    await store.erase()
                    var v = settings.voiceId ?? .standard
                    v.dictation = false
                    v.command = false
                    v.assistant = false
                    settings.voiceId = v
                    message = "Empreinte effacée."
                }
            }
        } message: {
            Text("« N'écouter que ma voix » sera désactivé partout.")
        }
    }

    private var voiceProcessing: Bool { controller.dictation.needsVoiceProcessing }

    private func beginEnrollment() {
        guard !controller.isActive else { message = "Ferme d'abord la conversation en cours."; return }
        clips = []
        message = ""
        step = 0
    }

    private func cancelEnrollment() {
        if recording { _ = recorder.stop() }
        recording = false
        step = nil
        clips = []
    }

    private func toggleReading() {
        guard let current = step else { return }
        if !recording {
            do {
                try recorder.start(voiceProcessing: voiceProcessing)
                startedAt = Date()
                recording = true
            } catch {
                message = "Micro indisponible : \(error.localizedDescription)"
            }
            return
        }
        recording = false
        let samples = recorder.stop()
        guard samples.count > 16000 * 5 else {
            message = "Lecture trop courte : recommence-la en entier."
            return
        }
        clips.append(samples)
        if current + 1 < Self.readings.count {
            step = current + 1
            return
        }
        step = nil
        message = "Calcul de l'empreinte…"
        let all = clips.flatMap { $0 }
        clips = []
        Task {
            let error = await store.enroll(all)
            message = error.map { "Échec : \($0)" } ?? "Empreinte enregistrée. Active-la produit par produit ci-dessous."
        }
    }

    private func toggleTest() {
        if !testing {
            guard !controller.isActive else { message = "Ferme d'abord la conversation en cours."; return }
            do {
                try recorder.start(voiceProcessing: voiceProcessing)
                testing = true
                message = "Parle…"
            } catch {
                message = "Micro indisponible : \(error.localizedDescription)"
            }
            return
        }
        testing = false
        let samples = recorder.stop()
        message = "Vérification…"
        Task {
            guard let (score, accepted) = await store.score(samples) else {
                message = "Le serveur de voix ne répond pas."
                return
            }
            guard let score else { message = "Pas de voix entendue."; return }
            message = String(format: "%.2f : ", score) + (accepted ? "c'est bien toi ✓" : "voix non reconnue ✗")
        }
    }
}
