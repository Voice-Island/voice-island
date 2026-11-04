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
            Button(controller.isActive ? "Arrêter l'assistant" : "Parler à l'assistant") {
                controller.toggle()
            }
            if controller.isActive {
                Button(controller.voiceReplies ? "Répondre en texte seulement" : "Répondre à voix haute") {
                    controller.toggleVoiceReplies()
                }
                Button(controller.typing ? "Masquer le champ de saisie" : "Écrire à l'assistant") {
                    controller.toggleTyping()
                }
            } else {
                Button("Parler sans réponse vocale") { controller.toggle(mode: .command) }
                Button("Écrire à l'assistant (chat)") { controller.toggle(mode: .chat) }
            }
            if controller.isActive {
                Button(controller.micMuted ? "Rallumer le micro" : "Couper le micro") { controller.toggleMute() }
            }
            Button("Tableau de bord…") { controller.openDashboard() }
            Divider()
            Button("Interface web") {
                NSWorkspace.shared.open(URL(string: "http://localhost:3010")!)
            }
            Divider()
            Button("Relancer Voice Island") { controller.relaunch() }
            Button("Quitter") { NSApp.terminate(nil) }
        }
    }
}
