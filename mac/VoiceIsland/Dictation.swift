import AVFoundation
import AppKit
import LiveKit
import Speech
import SwiftUI

/// Dictation anywhere (idea taken from Atticus): hold the shortcut (⌃ ⌥ D), speak, release. The text is typed
/// wherever the cursor is, in any app, without being sent.
/// Apple's recognition on the Mac (requiresOnDeviceRecognition): nothing leaves, and it understands French,
/// whereas Moonshine (the assistant's voice) only knows English. The local LLM then restores the punctuation
/// and removes hesitations ("nettoyer" / clean up setting), otherwise the raw text is inserted.
@MainActor
final class Dictation: ObservableObject {
    @Published private(set) var listening = false
    @Published private(set) var text = ""
    @Published private(set) var status = ""

    /// New for each dictation. It started without error but no longer received anything after a conversation
    /// ("0 chunks", Oct 26, 27 and 31), even when new: LiveKit's engine (voice processing, VPIO) stayed
    /// on and kept the process's mic. So dictation takes the mic away from it while listening (freeMic), and
    /// the app turns it off at the end of each conversation. LiveKit's mic delivers nothing outside a conversation.
    private var engine = AVAudioEngine()
    private var liveKitAvailability: AudioEngineAvailability?
    /// True from the first conversation on: VPIO is then active in the process. Before that, the raw mic is enough
    /// (and voice processing takes ~1 s to start, which cut off the start of sentences).
    var needsVoiceProcessing = false
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var panel: IslandPanel?
    private var finished: CheckedContinuation<Void, Never>?
    private var meter: Meter?

    /// Open conversation: its mic is muted during the dictation (otherwise it would hear too), then restored.
    /// Awaited before listening: the mute must not land after the mic has been restarted for the dictation.
    var pauseAssistantMic: (Bool) async -> Void = { _ in }

    /// Start in progress (mic not open yet) and what to do if the key is released in the meantime.
    private var starting = false
    private var pendingStop: Bool?   // true: cancel, false: stop and type
    private var discard = false

    func start() {
        Self.log("raccourci pressé (écoute en cours : \(listening))")
        guard !listening, !starting else { return }
        starting = true
        Task {
            await pauseAssistantMic(true)
            freeMic()
            Self.log("micro de la conversation libéré")
            await begin()
            starting = false
            // Released before the mic was open: without this, the dictation stayed on.
            if let cancelled = pendingStop {
                pendingStop = nil
                cancelled ? cancel() : stop()
            }
        }
    }

    /// Stops without transcribing or typing anything (Fn used with another key, press too short).
    func cancel() {
        Self.log("dictée annulée")
        guard listening else {
            if starting { pendingStop = true }
            return
        }
        discard = true
        stop()
    }

    func stop() {
        Self.log("raccourci relâché (écoute en cours : \(listening))")
        guard listening else {
            if starting, pendingStop == nil { pendingStop = false }
            return
        }
        listening = false
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        // A VPIO engine kept between two dictations made the mic silent for all other apps (Oct 31:
        // Terminal recordings at 0.002 until Voice Island was quit). Voice processing
        // turned off and engine released: the new one only opens the mic at the next dictation.
        if engine.inputNode.isVoiceProcessingEnabled { try? engine.inputNode.setVoiceProcessingEnabled(false) }
        engine = AVAudioEngine()
        restoreMic()
        request?.endAudio()
        Task { await finish() }
    }

    /// Takes the mic away from LiveKit's engine (its output stays: a reply in progress keeps playing).
    private func freeMic() {
        let current = AudioManager.shared.engineAvailability
        liveKitAvailability = current
        guard current.isInputAvailable else { return }
        do {
            try AudioManager.shared.setEngineAvailability(
                AudioEngineAvailability(isInputAvailable: false, isOutputAvailable: current.isOutputAvailable))
        } catch {
            Self.log("micro LiveKit non libéré : \(error.localizedDescription)")
        }
    }

    private func restoreMic() {
        guard let previous = liveKitAvailability else { return }
        liveKitAvailability = nil
        try? AudioManager.shared.setEngineAvailability(previous)
    }

    private func begin() async {
        engine = AVAudioEngine()
        if SettingsStore.dictation().engine != "apple" { return await beginRecording() }
        let allowed = await withCheckedContinuation { c in
            SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0 == .authorized) }
        }
        Self.log("autorisation reconnaissance vocale : \(allowed)")
        guard allowed else { return flash("Dictée : autorise la reconnaissance vocale dans Réglages Système.") }
        let settings = SettingsStore.dictation()
        let recognizer = SFSpeechRecognizer(locale: Locale(identifier: settings.language))
        Self.log("langue \(settings.language) : reconnaisseur \(recognizer != nil), disponible "
            + "\(recognizer?.isAvailable ?? false), sur le Mac \(recognizer?.supportsOnDeviceRecognition ?? false)")
        guard let recognizer, recognizer.isAvailable, recognizer.supportsOnDeviceRecognition
        else { return flash("Dictée : langue « \(settings.language) » non disponible sur ce Mac (Réglages Système › Clavier › Dictée).") }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        request.addsPunctuation = true
        self.request = request
        text = ""
        status = "Dictée…"
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        Self.log("micro : \(format.sampleRate) Hz, \(format.channelCount) canal(aux)")
        let meter = Meter()
        self.meter = meter
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            meter.add(buffer)
            request.append(buffer)
        }
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor in
                guard let self else { return }
                if let result { self.text = result.bestTranscription.formattedString }
                if let error {
                    Self.log("reconnaissance : erreur \(error.localizedDescription)")
                    // Apple's recognition, even on the Mac, requires macOS Dictation to be enabled.
                    if error.localizedDescription.lowercased().contains("disabled") {
                        self.stop()
                        self.flash("Active la Dictée : Réglages Système › Clavier › Dictée. Rien ne quitte le Mac.")
                    }
                }
                if result?.isFinal == true || error != nil {
                    self.finished?.resume()
                    self.finished = nil
                }
            }
        }
        do {
            engine.prepare()
            try engine.start()
        } catch {
            Self.log("micro : \(error.localizedDescription)")
            return flash("Dictée : micro indisponible (\(error.localizedDescription)).")
        }
        Self.log("écoute démarrée")
        listening = true
        show()
    }

    // MARK: Parakeet (local voice server)

    private var recorder: Recorder?

    /// Records the mic at 16 kHz mono; on release, the local voice server transcribes (Parakeet v3).
    private func beginRecording() async {
        let input = engine.inputNode
        // After a conversation, an engine without voice processing no longer receives anything (0 chunks), even with
        // LiveKit's turned off: once VPIO is enabled in the process, only VPIO engines hear
        // (reproduced by -DictationAfterConversationTest, Oct 31). Without lowering other apps' volume.
        if needsVoiceProcessing {
            do {
                try input.setVoiceProcessingEnabled(true)
                input.voiceProcessingOtherAudioDuckingConfiguration =
                    AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
                Self.log("traitement de la voix activé")
            } catch {
                Self.log("traitement de la voix indisponible : \(error.localizedDescription)")
            }
        }
        let format = input.outputFormat(forBus: 0)
        let recorder = Recorder()
        self.recorder = recorder
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in recorder.add(buffer) }
        do {
            engine.prepare()
            try engine.start()
        } catch {
            restoreMic()
            Self.log("micro : \(error.localizedDescription)")
            return flash("Dictée : micro indisponible (\(error.localizedDescription)).")
        }
        Self.log("enregistrement démarré (\(format.sampleRate) Hz -> 16 kHz, LiveKit : \(AudioManager.shared.isEngineRunning ? "allumé" : "éteint"))")
        text = ""
        status = "Dictée… relâche pour écrire"
        listening = true
        show()
    }

    private func finishRecording() async {
        await pauseAssistantMic(false)
        guard let recorder else { return hide() }
        self.recorder = nil
        if discard {
            discard = false
            return hide()
        }
        let samples = recorder.samples
        Self.log("son reçu : \(String(format: "%.1f", Double(samples.count) / 16000)) s, \(recorder.summary)")
        guard samples.count > 4000 else { return hide() }
        if Self.testMode { Self.saveWAV(samples) }
        status = "Transcription…"
        guard let (raw, rejected) = await Self.transcribe(samples) else {
            return flash("Dictée : le serveur de voix ne répond pas (web/dev.sh lancé ?).")
        }
        if rejected {   // "N'écouter que ma voix": another voice, silently ignored
            Self.log("autre voix : ignorée")
            return hide()
        }
        Self.log("texte reconnu : « \(raw) »")
        guard !raw.isEmpty else { return hide() }
        text = raw
        await insert(raw)
    }

    /// Test: the recording goes to web/logs/dictation-test.wav, to listen to it or transcribe it again.
    static func saveWAV(_ samples: [Float]) {
        let url = Paths.web.appendingPathComponent("logs/dictation-test.wav")
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false),
            let file = try? AVAudioFile(forWriting: url, settings: format.settings),
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count))
        else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        try? file.write(from: buffer)
    }

    /// -> (text, rejected because it isn't the owner's voice), or nil if the server doesn't respond.
    static func transcribe(_ samples: [Float]) async -> (String, Bool)? {
        guard let url = URL(string: "http://127.0.0.1:8090/transcribe") else { return nil }
        var request = URLRequest(url: url, timeoutInterval: 60)   // 1st call: model loading (~10 s)
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.httpBody = samples.withUnsafeBufferPointer { Data(buffer: $0) }
        guard let (data, _) = try? await URLSession.shared.data(for: request),
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        if let error = json["error"] as? String { log("serveur : \(error)") }
        if let score = json["score"] as? Double { log(String(format: "voix : %.2f", score)) }
        let text = (json["text"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return (text, json["rejected"] as? Bool ?? false)
    }

    private func finish() async {
        if recorder != nil { return await finishRecording() }
        if discard {
            discard = false
            task?.cancel()
            task = nil
            request = nil
            await pauseAssistantMic(false)
            return hide()
        }
        // Gives the engine time to return the end of the sentence (1.5 s at most).
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            finished = c
            Task {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                self.finished?.resume()
                self.finished = nil
            }
        }
        task = nil
        request = nil
        await pauseAssistantMic(false)
        if let meter { Self.log("son reçu : \(meter.summary)") }
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        Self.log("texte reconnu : « \(raw) »")
        guard !raw.isEmpty else {   // the "Active la Dictée" message stays shown (flash removes it itself)
            if !status.hasPrefix("Active la Dictée") { hide() }
            return
        }
        await insert(raw)
    }

    /// Optional cleanup by the LLM, then typed at the cursor (in test mode: only written to the log).
    private func insert(_ raw: String) async {
        var final = raw
        if SettingsStore.dictation().cleanup {
            status = "Mise au propre…"
            final = await Self.cleanUp(raw) ?? raw
        }
        hide()
        if Self.testMode { return Self.log("test : « \(final) » (pas inséré)") }
        Self.log("inséré : « \(final) »")
        TextInsert.paste(final)
    }

    /// Test launch (-DictationTest, -DictationAfterConversationTest): nothing is inserted.
    static let testMode = CommandLine.arguments.contains { $0.hasPrefix("-Dictation") }

    /// Milliseconds: the delay between pressing Fn and the mic opening is measured in tenths of a second (Nov 4).
    private static let logTime: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss.SSS"
        return f
    }()

    /// web/logs/dictation.log: every step, to understand a dictation that doesn't work.
    static func log(_ message: String) {
        let url = Paths.web.appendingPathComponent("logs/dictation.log")
        let line = "\(logTime.string(from: Date())) \(message)\n"
        guard let data = line.data(using: .utf8) else { return }
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            try? data.write(to: url)
        }
    }

    /// Punctuation, capitals, hesitations and false starts, by the local LLM (mlx_lm.server). Same language.
    static func cleanUp(_ raw: String) async -> String? {
        guard let url = URL(string: "http://127.0.0.1:8080/v1/chat/completions") else { return nil }
        let body: [String: Any] = [
            "model": SettingsStore.llmModelPath(),
            "messages": [
                ["role": "system", "content":
                    "You clean up dictated text. Fix punctuation and capitalization, remove filler words (um, euh, "
                    + "like), stutters and false starts. Keep the same language, words and meaning; do not answer, "
                    + "translate or add anything. Output only the cleaned text."],
                ["role": "user", "content": raw],
            ],
            "temperature": 0, "max_tokens": max(64, raw.count),
        ]
        var request = URLRequest(url: url, timeoutInterval: 8)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        guard let (data, _) = try? await URLSession.shared.data(for: request),
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let message = ((json["choices"] as? [[String: Any]])?.first?["message"] as? [String: Any]),
            let content = (message["content"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines),
            !content.isEmpty, content.count < raw.count * 2 + 40
        else { return nil }
        return content
    }

    private func flash(_ message: String) {
        Task { await pauseAssistantMic(false) }
        status = message
        text = ""
        show()
        Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if !listening { hide() }
        }
    }

    private func show() {
        if panel == nil { panel = IslandPanel(rootView: DictationView(dictation: self)) }
        panel?.place(at: SettingsStore.islandPosition())
        panel?.orderFrontRegardless()
    }

    private func hide() {
        status = ""
        panel?.orderOut(nil)
    }
}

/// Black pill below the camera: mic and live text.
struct DictationView: View {
    @ObservedObject var dictation: Dictation

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: dictation.listening ? "mic.fill" : "text.cursor")
                    .foregroundStyle(dictation.listening ? .red : .white)
                    .symbolEffect(.pulse, isActive: dictation.listening)
                Text(dictation.text.isEmpty ? dictation.status : dictation.text)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .truncationMode(.head)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(width: 420)
            .background(.black, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .padding(.top, 38)
            Spacer(minLength: 0)
        }
        .frame(width: IslandPanel.size.width, height: IslandPanel.size.height, alignment: .top)
    }
}

/// Types text at the cursor of the frontmost app: clipboard + ⌘ V, then the previous clipboard is
/// restored. Works everywhere (Chrome, Slack, VS Code, Mail…), where Accessibility alone often fails. Never sent.
enum TextInsert {
    @MainActor
    static func paste(_ text: String) {
        let board = NSPasteboard.general
        let saved = board.pasteboardItems?.compactMap { item -> NSPasteboardItem? in
            let copy = NSPasteboardItem()
            for type in item.types { if let data = item.data(forType: type) { copy.setData(data, forType: type) } }
            return copy
        } ?? []
        board.clearContents()
        board.setString(text, forType: .string)
        let source = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: down)  // V
            event?.flags = .maskCommand
            event?.post(tap: .cghidEventTap)
        }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 600_000_000)
            board.clearContents()
            if !saved.isEmpty { board.writeObjects(saved) }
        }
    }
}


/// Counts the audio chunks received and their peak level (dictation log).
final class Meter: @unchecked Sendable {
    private let lock = NSLock()
    private var buffers = 0
    private var peak: Float = 0

    func add(_ buffer: AVAudioPCMBuffer) {
        guard let data = buffer.floatChannelData else { return }
        var top: Float = 0
        for i in 0..<Int(buffer.frameLength) { top = max(top, abs(data[0][i])) }
        lock.lock()
        buffers += 1
        peak = max(peak, top)
        lock.unlock()
    }

    var summary: String {
        lock.lock()
        defer { lock.unlock() }
        return "\(buffers) morceaux, niveau max \(String(format: "%.3f", peak))"
    }
}


/// Mic -> 16 kHz mono float32 (what Parakeet expects), accumulated while the shortcut is held.
/// The converter follows the format of the chunks received (that of LiveKit's engine, known at the first chunk).
final class Recorder: @unchecked Sendable {
    private var converter: AVAudioConverter?
    private let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    private let lock = NSLock()
    private var buffer: [Float] = []
    private let meter = Meter()
    private var outPeak: Float = 0
    private(set) var inputFormat = ""

    func add(_ input: AVAudioPCMBuffer) {
        // With voice processing, the mic arrives on several channels: converted as-is to mono,
        // they gave silence (Parakeet returned "", Oct 31). Only the first channel (the processed voice) is kept.
        guard let mono = Self.firstChannel(input) else { return }
        if inputFormat.isEmpty {
            inputFormat = "\(input.format.channelCount) canal(aux), \(Int(input.format.sampleRate)) Hz"
        }
        if converter?.inputFormat != mono.format {
            converter = AVAudioConverter(from: mono.format, to: target)
        }
        guard let converter else { return }
        meter.add(mono)
        let capacity = AVAudioFrameCount(Double(mono.frameLength) * 16000 / mono.format.sampleRate) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
        var consumed = false
        converter.convert(to: out, error: nil) { _, status in
            if consumed {
                status.pointee = .noDataNow
                return nil
            }
            consumed = true
            status.pointee = .haveData
            return mono
        }
        guard let data = out.floatChannelData else { return }
        let samples = UnsafeBufferPointer(start: data[0], count: Int(out.frameLength))
        lock.lock()
        buffer.append(contentsOf: samples)
        outPeak = max(outPeak, samples.map(abs).max() ?? 0)
        lock.unlock()
    }

    /// First channel, as float32 mono at the original sample rate (interleaved or not, float or int16).
    private static func firstChannel(_ input: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        let frames = Int(input.frameLength)
        let channels = Int(input.format.channelCount)
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: input.format.sampleRate,
                                         channels: 1, interleaved: false),
            let mono = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: input.frameLength),
            let out = mono.floatChannelData?[0]
        else { return nil }
        mono.frameLength = input.frameLength
        let step = input.format.isInterleaved ? channels : 1
        if let data = input.floatChannelData {
            for i in 0..<frames { out[i] = data[0][i * step] }
        } else if let data = input.int16ChannelData {
            for i in 0..<frames { out[i] = Float(data[0][i * step]) / 32768 }
        } else {
            return nil
        }
        return mono
    }

    var samples: [Float] {
        lock.lock()
        defer { lock.unlock() }
        return buffer
    }

    var summary: String {
        lock.lock()
        defer { lock.unlock() }
        return "\(meter.summary), après conversion \(String(format: "%.3f", outPeak)), micro \(inputFormat)"
    }
}
