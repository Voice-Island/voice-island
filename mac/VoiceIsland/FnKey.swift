import AppKit
import IOKit.hid

/// Fn key alone, held down: the shortcut library (KeyboardShortcuts) can't record it, and macOS doesn't
/// know Fn as a global shortcut.
/// - Fn press and release are read at the keyboard level (IOKit HID, "Input Monitoring" permission).
///   AppKit events aren't enough: on this Mac (🌐 key, macOS 26), they arrived without the "Fn" flag,
///   and the key state read at that moment always said "released" (Oct 31).
/// - Other keys (Fn + arrow, Fn + F1…) go through the AppKit monitors (Accessibility).
/// `onPress` when Fn alone is pressed; `onRelease(true)` (cancel) if another key was used in the meantime or if
/// the press lasted less than 0.3 s, `onRelease(false)` otherwise.
@MainActor
final class FnKeyMonitor {
    var onPress: () -> Void = {}
    var onRelease: (_ cancelled: Bool) -> Void = { _ in }

    private var monitors: [Any] = []
    private var hid: IOHIDManager?
    private var down = false
    private var usedWithOtherKey = false
    private var pressedAt = Date()
    private var otherModifiers = false
    private var logged = 0

    func start() {
        guard monitors.isEmpty else { return }
        startHID()
        // Other keys and modifiers while Fn is held: the dictation will be cancelled.
        monitors.append(NSEvent.addGlobalMonitorForEvents(matching: [.flagsChanged, .keyDown]) { [weak self] event in
            MainActor.assumeIsolated { self?.other(event) }
        } as Any)
        monitors.append(NSEvent.addLocalMonitorForEvents(matching: [.flagsChanged, .keyDown]) { [weak self] event in
            MainActor.assumeIsolated { self?.other(event) }
            return event
        } as Any)
    }

    func stop() {
        monitors.forEach { NSEvent.removeMonitor($0) }
        monitors = []
        if let hid { IOHIDManagerClose(hid, IOOptionBits(kIOHIDOptionsTypeNone)) }
        hid = nil
    }

    private func startHID() {
        if IOHIDCheckAccess(kIOHIDRequestTypeListenEvent) != kIOHIDAccessTypeGranted {
            Dictation.log("touche Fn : autorisation « Surveillance de l'entrée » demandée")
            IOHIDRequestAccess(kIOHIDRequestTypeListenEvent)
        }
        let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
        IOHIDManagerSetDeviceMatching(manager, [kIOHIDDeviceUsagePageKey: kHIDPage_GenericDesktop,
                                                kIOHIDDeviceUsageKey: kHIDUsage_GD_Keyboard] as CFDictionary)
        IOHIDManagerRegisterInputValueCallback(manager, { context, _, _, value in
            let element = IOHIDValueGetElement(value)
            // Fn: "Apple top case" page (0xFF, usage 3) on built-in keyboards, "Apple keyboard" (0xFF01) elsewhere.
            guard IOHIDElementGetUsage(element) == 0x03,
                [0x00FF, 0xFF01].contains(IOHIDElementGetUsagePage(element)), let context
            else { return }
            let pressed = IOHIDValueGetIntegerValue(value) != 0
            let monitor = Unmanaged<FnKeyMonitor>.fromOpaque(context).takeUnretainedValue()
            MainActor.assumeIsolated { monitor.fn(pressed) }
        }, Unmanaged.passUnretained(self).toOpaque())
        IOHIDManagerScheduleWithRunLoop(manager, CFRunLoopGetMain(), CFRunLoopMode.defaultMode.rawValue)
        let result = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))
        Dictation.log("touche Fn : clavier \(result == kIOReturnSuccess ? "suivi" : "inaccessible (\(result))")")
        hid = manager
    }

    private func fn(_ pressed: Bool) {
        if logged < 6 {
            logged += 1
            Dictation.log("touche Fn : \(pressed ? "enfoncée" : "relâchée")")
        }
        if pressed, !down {
            down = true
            usedWithOtherKey = otherModifiers   // ⌘ / ⌥ / ⌃ / ⇧ already held: not a dictation
            pressedAt = Date()
            if !usedWithOtherKey { onPress() }
        } else if !pressed, down {
            down = false
            onRelease(usedWithOtherKey || Date().timeIntervalSince(pressedAt) < 0.3)
        }
    }

    private func other(_ event: NSEvent) {
        if event.type == .keyDown {
            if down { usedWithOtherKey = true }
            return
        }
        otherModifiers = !event.modifierFlags.intersection([.command, .option, .control, .shift]).isEmpty
        if down, otherModifiers { usedWithOtherKey = true }
    }
}
