import KeyboardShortcuts
import SwiftUI

extension KeyboardShortcuts.Name {
    /// Global shortcut that opens or closes the assistant (⌥ Space by default, customizable).
    static let toggleAssistant = Self("toggleAssistant", default: .init(.space, modifiers: [.option]))
    /// Opens the dashboard (⌥ ⇧ Space by default).
    static let openDashboard = Self("openDashboard", default: .init(.space, modifiers: [.option, .shift]))
    /// Without voice: transcription + actions, answer in text (⌃ ⌥ Space by default). During a conversation,
    /// turns the reply voice off / back on.
    static let commandMode = Self("commandMode", default: .init(.space, modifiers: [.control, .option]))
    /// Mutes / unmutes the mic without closing the conversation (⌃ ⌥ M by default).
    static let toggleMute = Self("toggleMute", default: .init(.m, modifiers: [.control, .option]))
    /// Chat: type to the assistant, without mic or voice (⌃ ⌥ T by default). During a conversation,
    /// shows / hides the input field.
    static let chatMode = Self("chatMode", default: .init(.t, modifiers: [.control, .option]))
    /// Dictation anywhere: hold, speak, release (⌃ ⌥ D by default).
    static let dictate = Self("dictate", default: .init(.d, modifiers: [.control, .option]))
}

@main
struct VoiceIslandApp: App {
    @StateObject private var controller = AssistantController()

    var body: some Scene {
        MenuBarExtra("Voice Island", systemImage: "waveform") {
            Button(controller.isActive ? String(localized: "Stop the assistant") : String(localized: "Talk to the assistant")) {
                controller.toggle()
            }
            if controller.isActive {
                Button(controller.voiceReplies ? String(localized: "Reply in text only") : String(localized: "Reply out loud")) {
                    controller.toggleVoiceReplies()
                }
                Button(controller.typing ? String(localized: "Hide the input field") : String(localized: "Type to the assistant")) {
                    controller.toggleTyping()
                }
            } else {
                Button("Talk without voice replies") { controller.toggle(mode: .command) }
                Button("Type to the assistant (chat)") { controller.toggle(mode: .chat) }
            }
            if controller.isActive {
                Button(controller.micMuted ? String(localized: "Unmute the mic") : String(localized: "Mute the mic")) { controller.toggleMute() }
            }
            Button("Dashboard…") { controller.openDashboard() }
            Divider()
            Button("Web interface") {
                NSWorkspace.shared.open(URL(string: "http://localhost:3010")!)
            }
            Divider()
            Button("Relaunch Voice Island") { controller.relaunch() }
            Button("Quit") { NSApp.terminate(nil) }
        }
    }
}
