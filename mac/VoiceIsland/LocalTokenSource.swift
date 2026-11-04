import CryptoKit
import Foundation
import LiveKit

/// Connection token signed right here, with this install's keys (P0-4): web/livekit.keys, randomly generated
/// by dev.sh on first launch (web/agent/lk_keys.py). livekit-server rejects the old public keys
/// devkey / secret. The app doesn't need the web interface (Next.js, ~4 GB of memory).
struct LocalTokenSource: TokenSourceFixed {
    static let serverURL = URL(string: "ws://127.0.0.1:7880")!
    static let keysFile = Paths.web.appendingPathComponent("livekit.keys")
    /// "command": the agent answers in text, without voice. "chat": the user types, answers in text.
    var roomPrefix: String?

    enum Failure: LocalizedError {
        case unreachable, noKeys
        var errorDescription: String? {
            switch self {
            case .unreachable: String(localized: "Can't reach the local server. Run ~/s2s-voice-assistant/web/dev.sh")
            case .noKeys: String(localized: "LiveKit keys missing (web/livekit.keys). Run ~/s2s-voice-assistant/web/dev.sh again")
            }
        }
    }

    /// One "key: secret" line, the `livekit-server --key-file` format.
    static func keys() throws -> (key: String, secret: String) {
        guard let text = try? String(contentsOf: keysFile, encoding: .utf8),
              let line = text.split(whereSeparator: \.isNewline).first,
              let colon = line.firstIndex(of: ":") else { throw Failure.noKeys }
        let key = line[..<colon].trimmingCharacters(in: .whitespaces)
        let secret = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        guard !key.isEmpty, !secret.isEmpty else { throw Failure.noKeys }
        return (key, secret)
    }

    func fetch() async throws -> TokenSourceResponse {
        // The LiveKit server must be running; otherwise a clear message rather than an opaque connection failure.
        var probe = URLRequest(url: URL(string: "http://127.0.0.1:7880")!)
        probe.timeoutInterval = 2
        guard (try? await URLSession.shared.data(for: probe)) != nil else { throw Failure.unreachable }

        let (apiKey, apiSecret) = try Self.keys()
        let identity = "voice_assistant_user_\(Int.random(in: 0..<10_000))"
        let prefix = ["command", "chat"].contains(roomPrefix ?? "") ? roomPrefix! : "voice_assistant"
        let room = "\(prefix)_room_\(Int.random(in: 0..<10_000))"
        let now = Int(Date().timeIntervalSince1970)
        let claims: [String: Any] = [
            "iss": apiKey, "sub": identity, "name": "user", "nbf": now, "exp": now + 15 * 60,
            "video": ["room": room, "roomJoin": true, "canPublish": true, "canPublishData": true,
                      "canSubscribe": true],
        ]
        return TokenSourceResponse(
            serverURL: Self.serverURL, participantToken: try Self.jwt(claims, secret: apiSecret),
            participantName: "user", roomName: room)
    }

    /// JWT HS256, as livekit-server-sdk does it.
    private static func jwt(_ claims: [String: Any], secret: String) throws -> String {
        func base64URL(_ data: Data) -> String {
            data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
                .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        }
        let header = base64URL(try JSONSerialization.data(withJSONObject: ["alg": "HS256", "typ": "JWT"]))
        let payload = base64URL(try JSONSerialization.data(withJSONObject: claims))
        let signature = HMAC<SHA256>.authenticationCode(
            for: Data("\(header).\(payload)".utf8), using: SymmetricKey(data: Data(secret.utf8)))
        return "\(header).\(payload).\(base64URL(Data(signature)))"
    }
}
