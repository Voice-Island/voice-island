import Foundation

/// Status of the local stack's services, and start / stop via web/dev.sh. The web interface is optional:
/// the app connects without it.
@MainActor
final class ServiceMonitor: ObservableObject {
    struct Service: Identifiable {
        let id: String
        let name: String
        let detail: String
        var isUp: Bool?
    }

    @Published private(set) var services: [Service] = [
        Service(id: "livekit", name: "LiveKit", detail: String(localized: "audio transport · port 7880")),
        Service(id: "llm", name: "LLM", detail: "mlx_lm.server · port 8080"),
        Service(id: "tts", name: String(localized: "Voice"), detail: "tts_server.py · port 8090"),
        Service(id: "agent", name: "Agent", detail: "agent.py"),
        Service(id: "web", name: String(localized: "Web interface (optional)"), detail: "Next.js · port 3010 · ./dev.sh web"),
    ]
    @Published private(set) var busy: String?

    private var timer: Timer?

    var allUp: Bool { services.filter { $0.id != "web" }.allSatisfy { $0.isUp == true } }

    func startPolling() {
        refresh()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

    func refresh() {
        Task {
            async let livekit = Self.httpOK("http://127.0.0.1:7880")
            async let llm = Self.httpOK("http://127.0.0.1:8080/v1/models")
            async let tts = Self.httpOK("http://127.0.0.1:8090/health")
            async let web = Self.httpOK("http://127.0.0.1:3010/api/settings")
            async let agent = Self.processRunning("agent.py dev")
            let states = ["livekit": await livekit, "llm": await llm, "tts": await tts, "agent": await agent,
                          "web": await web]
            for i in services.indices { services[i].isUp = states[services[i].id] }
        }
    }

    func startAll() { run("dev.sh", label: String(localized: "Starting… (1 to 2 min the first time)")) }
    func stopAll() { run("dev.sh stop", label: String(localized: "Stopping…")) }

    /// Runs dev.sh in a login shell (the user's PATH: uv, livekit-server, nvm).
    private func run(_ command: String, label: String) {
        busy = label
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-lc", "cd \(Self.quoted(Paths.web.path)) && ./\(command)"]
        let log = Paths.logs.appendingPathComponent("dashboard.log")
        try? FileManager.default.createDirectory(at: Paths.logs, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: log.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: log) {
            process.standardOutput = handle
            process.standardError = handle
        }
        process.terminationHandler = { _ in
            Task { @MainActor [weak self] in
                self?.busy = nil
                self?.refresh()
            }
        }
        do {
            try process.run()
        } catch {
            busy = nil
        }
    }

    private static func quoted(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

    private static func httpOK(_ url: String) async -> Bool {
        var request = URLRequest(url: URL(string: url)!)
        request.timeoutInterval = 1.5
        guard let (_, response) = try? await URLSession.shared.data(for: request) else { return false }
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    private static func processRunning(_ pattern: String) async -> Bool {
        await withCheckedContinuation { continuation in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
            process.arguments = ["-f", pattern]
            process.standardOutput = FileHandle.nullDevice
            process.terminationHandler = { continuation.resume(returning: $0.terminationStatus == 0) }
            do { try process.run() } catch { continuation.resume(returning: false) }
        }
    }
}
