import AVFoundation
import LiveKit

/// Checks that the microphone really sends sound. A working mic never sends perfect zeros (there is
/// always slight background noise): zeros, or no sound at all, mean the capture is broken
/// (wake from sleep, mic change, permission revoked). Seen on Oct 24: the agent stopped receiving anything.
final class MicProbe: NSObject, AudioRenderer, @unchecked Sendable {
    private let lock = NSLock()
    private var lastBuffer: Date?
    private var lastSound: Date?

    func render(pcmBuffer: AVAudioPCMBuffer) {
        let frames = Int(pcmBuffer.frameLength)
        var sound = false
        if let samples = pcmBuffer.floatChannelData?[0] {
            for i in stride(from: 0, to: frames, by: 8) where samples[i] != 0 { sound = true; break }
        } else if let samples = pcmBuffer.int16ChannelData?[0] {
            for i in stride(from: 0, to: frames, by: 8) where samples[i] != 0 { sound = true; break }
        }
        let now = Date()
        lock.lock()
        lastBuffer = now
        if sound { lastSound = now }
        lock.unlock()
    }

    func reset() {
        lock.lock()
        lastBuffer = nil
        lastSound = nil
        lock.unlock()
    }

    /// Seconds since the last real sound received from the mic (nil: nothing received at all since reset()).
    var secondsSinceSound: TimeInterval? {
        lock.lock()
        defer { lock.unlock() }
        return lastSound.map { Date().timeIntervalSince($0) }
    }
}
