import LiveKit
import SwiftUI

/// The black frame that comes out of the notch: assistant state and live transcription.
struct IslandView: View {
    @ObservedObject var controller: AssistantController

    static let expandedWidth: CGFloat = 520

    private var notch: CGSize { Self.notch }

    /// Camera notch of the built-in screen; shared with the dictation frame so both open the same way.
    static var notch: CGSize {
        let screen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) ?? NSScreen.main
        let size = screen?.notchSize ?? .zero
        // Screen without a notch: a pill of equivalent size below the top of the screen.
        return CGSize(width: max(size.width, 190), height: max(size.height, 32))
    }

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .top) {
                if controller.expanded, let session = controller.session {
                    IslandContent(session: session, controller: controller, notch: notch)
                        .transition(.opacity.combined(with: .scale(scale: 0.92, anchor: .top)))
                } else {
                    IslandShape(radius: 12)
                        .fill(.black)
                        .frame(width: notch.width, height: notch.height)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(width: IslandPanel.size.width, height: IslandPanel.size.height, alignment: .top)
        .coordinateSpace(name: "panel")
    }
}

struct IslandContent: View {
    @ObservedObject var session: Session
    @ObservedObject var controller: AssistantController
    let notch: CGSize

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // First row at notch height: visualizer on the left, state on the right, camera in the middle.
            HStack(spacing: 0) {
                LevelBars(session: session, state: controller.micMuted && state == .listening ? nil : state)
                    .frame(width: 44, height: 16)
                    .opacity(controller.micMuted ? 0.35 : 1)
                Spacer(minLength: notch.width)
                Label(stateText, systemImage: stateIcon)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(stateColor)
                    .contentTransition(.symbolEffect(.replace))
                    .lineLimit(1)
                IslandButton(symbol: controller.voiceReplies ? "speaker.wave.2.fill" : "speaker.slash.fill",
                             help: controller.voiceReplies ? String(localized: "Replies in text only (⌃ ⌥ Space)")
                                 : String(localized: "Replies out loud (⌃ ⌥ Space)"),
                             tint: controller.voiceReplies ? nil : .orange) { controller.toggleVoiceReplies() }
                    .padding(.leading, 8)
                IslandButton(symbol: controller.micMuted ? "mic.slash.fill" : "mic.fill",
                             help: controller.micMuted ? String(localized: "Unmute the mic (⌃ ⌥ M)")
                                 : String(localized: "Mute the mic (⌃ ⌥ M)"),
                             tint: controller.micMuted ? .red : nil) { controller.toggleMute() }
                IslandButton(symbol: "keyboard", help: controller.typing ? String(localized: "Hide the field (⌃ ⌥ T)")
                                 : String(localized: "Type (⌃ ⌥ T)"),
                             tint: controller.typing ? .white : nil) { controller.toggleTyping() }
                IslandButton(symbol: "gearshape.fill", help: String(localized: "Dashboard")) { controller.openDashboard() }
                IslandButton(symbol: "xmark", help: String(localized: "Close")) { controller.stop() }
            }
            .frame(height: notch.height)

            transcript
            if controller.typing {
                ChatField(controller: controller)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
            if let action = controller.lastAction {
                Label(action.text, systemImage: Self.symbol(for: action))
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(!action.ok ? Color.orange : action.pending ? Color.white.opacity(0.75) : Color.green)
                    .lineLimit(2)
                    .transition(.opacity)
                if action.awaitsConfirmation {
                    // Or say "yes" / "no" ("oui" / "non"): the agent hears it too.
                    HStack(spacing: 8) {
                        Button("Yes") { controller.confirm(action, yes: true) }
                            .buttonStyle(.borderedProminent)
                        Button("No") { controller.confirm(action, yes: false) }
                            .buttonStyle(.bordered)
                    }
                    .controlSize(.small)
                    .transition(.opacity)
                }
            }
        }
        .padding(.horizontal, 20)
        .padding(.bottom, 14)
        .frame(width: IslandView.expandedWidth, alignment: .leading)
        .background(IslandShape(radius: 24).fill(.black))
        .background(GeometryReader { geo in
            // Frame position in the window: the mouse is captured only over it.
            Color.clear
                .onAppear { controller.islandRect = geo.frame(in: .named("panel")) }
                .onChange(of: geo.frame(in: .named("panel"))) { _, rect in controller.islandRect = rect }
        })
        .animation(.spring(response: 0.35, dampingFraction: 0.85), value: session.messages)
        .animation(.easeInOut(duration: 0.2), value: state)
        .animation(.easeInOut(duration: 0.2), value: controller.typing)
    }

    /// Action line icon: verified (seal), done, launched with no known result, pending, failed.
    static func symbol(for action: AgentAction) -> String {
        guard action.ok else { return "exclamationmark.triangle.fill" }
        switch action.status {
        case "verified": return "checkmark.seal.fill"
        case "attempted": return "paperplane.fill"
        case "proposed", "approved": return "questionmark.circle.fill"
        default: return "checkmark.circle.fill"
        }
    }

    // MARK: Transcript

    @ViewBuilder private var transcript: some View {
        if let error = session.error ?? session.agent.error.map({ $0 as Error }) {
            Text(error.localizedDescription)
                .font(.system(size: 13))
                .foregroundStyle(.red.opacity(0.9))
        } else {
            let (user, agent) = lastExchange
            VStack(alignment: .leading, spacing: 6) {
                if let user {
                    Text(user)
                        .font(.system(size: 13))
                        .foregroundStyle(.white.opacity(0.55))
                        .lineLimit(2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if let agent {
                    Text(agent)
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(.white)
                        .lineLimit(controller.mode == .chat ? 8 : 4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if user == nil && agent == nil {
                    Text(controller.mode == .chat
                         ? (session.agent.isConnected ? String(localized: "Type your request…") : String(localized: "Connecting…"))
                         : controller.micMuted ? String(localized: "Mic muted: I can't hear anything. ⌃ ⌥ M to unmute.")
                         : (session.agent.isConnected ? String(localized: "I'm listening… (in English)")
                            : String(localized: "Connecting…")))
                        .font(.system(size: 13))
                        .foregroundStyle(.white.opacity(0.45))
                }
            }
        }
    }

    /// The user's last sentence and the reply that follows it (including one still being written).
    private var lastExchange: (String?, String?) {
        var user: String?
        var agent: String?
        for message in session.messages.reversed() {
            switch message.content {
            case .agentTranscript(let text):
                if agent == nil && user == nil { agent = text }
            case .userTranscript(let text), .userInput(let text):
                if user == nil { user = text }
            }
            if user != nil { break }
        }
        return (user, agent)
    }

    // MARK: State

    private var state: AgentState? {
        session.agent.isConnected ? session.agent.agentState : nil
    }

    private var stateText: String {
        if controller.mode == .chat && controller.micMuted {
            return state == .thinking ? String(localized: "Thinking") : String(localized: "Chat")
        }
        if controller.micMuted { return String(localized: "Mic muted") }
        switch state {
        case .listening: return controller.voiceReplies ? String(localized: "Listening") : String(localized: "Listening · no voice")
        case .thinking: return String(localized: "Thinking")
        case .speaking: return String(localized: "Speaking")
        case .idle, .initializing: return String(localized: "Ready")
        case nil: return String(localized: "Connecting")
        }
    }

    private var stateIcon: String {
        if controller.mode == .chat && controller.micMuted { return state == .thinking ? "sparkles" : "text.bubble" }
        if controller.micMuted { return "mic.slash.fill" }
        switch state {
        case .listening: return controller.voiceReplies ? "ear" : "command"
        case .thinking: return "sparkles"
        case .speaking: return "speaker.wave.2.fill"
        default: return "circle.dotted"
        }
    }

    private var stateColor: Color {
        if controller.mode == .chat && controller.micMuted { return state == .thinking ? .orange : .white.opacity(0.7) }
        if controller.micMuted { return .red }
        switch state {
        case .listening: return .green
        case .thinking: return .orange
        case .speaking: return .cyan
        default: return .white.opacity(0.6)
        }
    }
}

/// Small round button in the frame (settings, close).
struct IslandButton: View {
    let symbol: String
    let help: String
    var tint: Color? = nil
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(tint ?? .white.opacity(hovering ? 1 : 0.6))
                .frame(width: 22, height: 22)
                .background(Circle().fill((tint ?? .white).opacity(hovering ? 0.3 : 0.15)))
        }
        .buttonStyle(.plain)
        .help(help)
        .onHover { hovering = $0 }
    }
}

/// Five bars that follow my voice when the assistant listens, and its own when it speaks.
struct LevelBars: View {
    let session: Session
    let state: AgentState?
    private let profile: [Double] = [0.55, 0.85, 1, 0.85, 0.55]

    var body: some View {
        TimelineView(.animation) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            let level = currentLevel()
            HStack(alignment: .center, spacing: 3) {
                ForEach(0..<5, id: \.self) { i in
                    Capsule()
                        .fill(color.opacity(opacity(i, t)))
                        .frame(width: 4, height: height(i, t, level))
                }
            }
            .frame(maxHeight: .infinity)
        }
    }

    private var color: Color {
        switch state {
        case .listening: return .green
        case .thinking: return .orange
        case .speaking: return .cyan
        default: return .white
        }
    }

    private func currentLevel() -> Double {
        switch state {
        case .listening:
            return Double(session.room.localParticipant.audioLevel)
        case .speaking:
            let agent = session.room.remoteParticipants.values.first { $0.kind == .agent }
            return Double(agent?.audioLevel ?? 0)
        default:
            return 0
        }
    }

    private func height(_ i: Int, _ t: Double, _ level: Double) -> CGFloat {
        switch state {
        case .thinking:
            return 4 + 6 * max(0, sin(t * 6 - Double(i) * 0.9))
        case .listening, .speaking:
            let jitter = 0.8 + 0.2 * sin(t * 10 + Double(i) * 1.7)
            let idle = 2 * (1 + sin(t * 2.5 + Double(i)))
            return 4 + CGFloat(max(idle, min(1, level * 2.2) * 12 * profile[i] * jitter))
        default:
            return 4
        }
    }

    private func opacity(_ i: Int, _ t: Double) -> Double {
        state == .thinking ? 0.4 + 0.6 * max(0, sin(t * 6 - Double(i) * 0.9)) : 1
    }
}

/// Notch shape: straight top edge flush with the screen, rounded bottom corners.
struct IslandShape: Shape {
    var radius: CGFloat

    func path(in rect: CGRect) -> Path {
        let r = min(radius, rect.height / 2, rect.width / 2)
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - r))
        path.addQuadCurve(to: CGPoint(x: rect.maxX - r, y: rect.maxY), control: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX + r, y: rect.maxY))
        path.addQuadCurve(to: CGPoint(x: rect.minX, y: rect.maxY - r), control: CGPoint(x: rect.minX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}

/// Discreet input field in the frame: Return sends, Esc hides it (and closes the chat).
struct ChatField: View {
    @ObservedObject var controller: AssistantController
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 8) {
            TextField("", text: $text, prompt: Text("Type to the assistant…").foregroundStyle(.white.opacity(0.35)))
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .foregroundStyle(.white)
                .focused($focused)
                .onSubmit {
                    controller.send(text)
                    text = ""
                }
                .onExitCommand {
                    if controller.mode == .chat { controller.stop() } else { controller.toggleTyping() }
                }
            if !text.isEmpty {
                Image(systemName: "return")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.45))
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 12).fill(.white.opacity(0.08)))
        .onAppear { focused = true }
    }
}
