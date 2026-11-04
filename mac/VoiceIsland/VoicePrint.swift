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
        return json?["error"] as? String ?? String(localized: "the voice server isn't responding (is web/dev.sh running?)")
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
            Toggle("Listen only to my voice", isOn: Binding(
                get: { (settings.voiceId ?? .standard)[keyPath: product.keyPath] },
                set: {
                    var value = settings.voiceId ?? .standard
                    value[keyPath: product.keyPath] = $0
                    settings.voiceId = value
                }
            ))
            .disabled(!store.enrolled)
        } header: {
            Text("Voice biometrics")
        } footer: {
            if store.enrolled {
                Text("Any other voice is silently ignored: never transcribed, never shown. Voiceprint, threshold and test: “My voice” page. This is not a lock: a recording of your voice would get through.")
            } else {
                Text("First record your voice on the “My voice” page.")
            }
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
        String(localized: "Hi, this is my voice. I use this assistant every day to open my apps, write my messages and look things up on the web."),
        "Hello, this is my normal speaking voice. I use this assistant every day to open my apps, move my windows "
            + "around, search the web and play some music while I work.",
        String(localized: "In the morning, I start by reading my emails, then I prepare my presentations. After that I make calls and take notes during meetings."),
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
                LabeledContent("Voiceprint", value: !store.reachable ? String(localized: "voice server stopped")
                               : store.enrolled ? String(localized: "recorded") : String(localized: "none"))
                if let step {
                    Text("Reading \(step + 1) of \(Self.readings.count): read this text in your normal voice.")
                        .font(.callout.weight(.semibold))
                    Text(Self.readings[step])
                        .font(.title3)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
                    HStack {
                        Button(recording ? String(localized: "Stop") : String(localized: "Start")) { toggleReading() }
                            .buttonStyle(.borderedProminent)
                        if recording {
                            TimelineView(.periodic(from: startedAt, by: 0.5)) { context in
                                Label("\(Int(context.date.timeIntervalSince(startedAt))) s", systemImage: "record.circle")
                                    .foregroundStyle(.red)
                            }
                        }
                        Spacer()
                        Button("Cancel") { cancelEnrollment() }
                    }
                } else {
                    Button(store.enrolled ? String(localized: "Redo my voiceprint") : String(localized: "Record my voice")) { beginEnrollment() }
                        .disabled(!store.reachable || testing)
                }
            } header: {
                Text("My voiceprint")
            } footer: {
                Text("Three readings of about 15 s each, with the mic you usually use. The voiceprint (256 numbers, not the audio) stays on this Mac, in the macOS keychain; the readings are not kept.")
            }

            Section {
                HStack {
                    Button(testing ? String(localized: "Stop and check") : String(localized: "Test: say a sentence")) { toggleTest() }
                        .disabled(!store.enrolled || step != nil)
                    if !message.isEmpty { Text(message).foregroundStyle(.secondary) }
                }
                SliderRow(String(localized: "Threshold"), value: Binding(
                    get: { (settings.voiceId ?? .standard).threshold },
                    set: { var v = settings.voiceId ?? .standard; v.threshold = $0; settings.voiceId = v }
                ), in: 0.2...0.7, step: 0.01, format: "%.2f",
                          help: String(localized: "Higher: other voices get through even less, but your very short words may be rejected. Measured: your voice ≥ 0.42, other voices ≤ 0.36."))
            } header: {
                Text("Test and adjust")
            }

            Section {
                ForEach([(VoiceProduct.dictation, String(localized: "Dictation")), (.command, String(localized: "Voice commands")),
                         (.assistant, String(localized: "Voice assistant"))],
                        id: \.0.rawValue) { product, title in
                    Toggle(title, isOn: Binding(
                        get: { (settings.voiceId ?? .standard)[keyPath: product.keyPath] },
                        set: { var v = settings.voiceId ?? .standard; v[keyPath: product.keyPath] = $0; settings.voiceId = v }
                    ))
                    .disabled(!store.enrolled)
                }
            } header: {
                Text("Listen only to my voice for…")
            } footer: {
                Text("Any other voice is silently ignored: it is neither transcribed nor shown. A very short word (“Stop”) is accepted if it closely follows a recognized sentence. This is not a lock: a recording or an imitation of your voice may get through.")
            }

            Section {
                Button("Erase my voiceprint", role: .destructive) { confirmErase = true }
                    .disabled(!store.enrolled)
            }
        }
        .formStyle(.grouped)
        .task { await store.refresh() }
        .confirmationDialog("Erase your voiceprint?", isPresented: $confirmErase) {
            Button("Erase", role: .destructive) {
                Task {
                    await store.erase()
                    var v = settings.voiceId ?? .standard
                    v.dictation = false
                    v.command = false
                    v.assistant = false
                    settings.voiceId = v
                    message = String(localized: "Voiceprint erased.")
                }
            }
        } message: {
            Text("“Listen only to my voice” will be turned off everywhere.")
        }
    }

    private var voiceProcessing: Bool { controller.dictation.needsVoiceProcessing }

    private func beginEnrollment() {
        guard !controller.isActive else { message = String(localized: "First close the current conversation."); return }
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
                message = String(localized: "Mic unavailable: \(error.localizedDescription)")
            }
            return
        }
        recording = false
        let samples = recorder.stop()
        guard samples.count > 16000 * 5 else {
            message = String(localized: "Reading too short: start it again from the beginning.")
            return
        }
        clips.append(samples)
        if current + 1 < Self.readings.count {
            step = current + 1
            return
        }
        step = nil
        message = String(localized: "Computing the voiceprint…")
        let all = clips.flatMap { $0 }
        clips = []
        Task {
            let error = await store.enroll(all)
            message = error.map { String(localized: "Failed: \($0)") }
                ?? String(localized: "Voiceprint recorded. Turn it on product by product below.")
        }
    }

    private func toggleTest() {
        if !testing {
            guard !controller.isActive else { message = String(localized: "First close the current conversation."); return }
            do {
                try recorder.start(voiceProcessing: voiceProcessing)
                testing = true
                message = String(localized: "Speak…")
            } catch {
                message = String(localized: "Mic unavailable: \(error.localizedDescription)")
            }
            return
        }
        testing = false
        let samples = recorder.stop()
        message = String(localized: "Checking…")
        Task {
            guard let (score, accepted) = await store.score(samples) else {
                message = String(localized: "The voice server isn't responding.")
                return
            }
            guard let score else { message = String(localized: "No voice heard."); return }
            let value = String(format: "%.2f", score)
            message = accepted ? String(localized: "\(value): that's you ✓") : String(localized: "\(value): voice not recognized ✗")
        }
    }
}
