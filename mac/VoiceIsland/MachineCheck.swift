import Foundation
import IOKit
import Metal

/// What this Mac can run: chip, memory, GPU, disk, then a verdict per product.
/// Used by the dashboard's "Your Mac" page, and will be used by the first-launch assistant (pack choice).
struct MachineInfo {
    let chip: String            // "Apple M3", "Apple M1 Pro"…
    let model: String           // hardware identifier, e.g. "Mac15,13"
    let memoryGB: Int           // total unified memory
    let gpuBudgetGB: Double     // memory Metal recommends not exceeding for the GPU (MLX depends on it)
    let gpuCores: Int?
    let freeDiskGB: Double
    let appleSilicon: Bool

    static func current() -> MachineInfo {
        let device = MTLCreateSystemDefaultDevice()
        let home = URL(fileURLWithPath: NSHomeDirectory())
        let free = (try? home.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage ?? 0
        return MachineInfo(
            chip: sysctlString("machdep.cpu.brand_string") ?? String(localized: "Unknown chip"),
            model: sysctlString("hw.model") ?? "",
            memoryGB: Int((Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824).rounded()),
            gpuBudgetGB: Double(device?.recommendedMaxWorkingSetSize ?? 0) / 1_073_741_824,
            gpuCores: gpuCoreCount(),
            freeDiskGB: Double(free) / 1_000_000_000,
            appleSilicon: device?.hasUnifiedMemory == true && (sysctlString("machdep.cpu.brand_string") ?? "").hasPrefix("Apple"))
    }

    private static func sysctlString(_ name: String) -> String? {
        var size = 0
        guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var buffer = [CChar](repeating: 0, count: size)
        guard sysctlbyname(name, &buffer, &size, nil, 0) == 0 else { return nil }
        return String(cString: buffer)
    }

    /// Number of GPU cores, read from the IOKit registry (AGXAccelerator), without any permission.
    private static func gpuCoreCount() -> Int? {
        let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("AGXAccelerator"))
        guard service != 0 else { return nil }
        defer { IOObjectRelease(service) }
        let value = IORegistryEntryCreateCFProperty(service, "gpu-core-count" as CFString, kCFAllocatorDefault, 0)
        return value?.takeRetainedValue() as? Int
    }
}

/// Model packs offered depending on memory. Sizes are approximate downloads: to be re-measured on
/// an 8 GB Mac and a 16 GB Mac before the first-launch assistant.
enum ModelPack: String, CaseIterable {
    case light, balanced, full

    var title: String {
        switch self {
        case .light: return String(localized: "Light")
        case .balanced: return String(localized: "Balanced")
        case .full: return String(localized: "Full")
        }
    }

    var summary: String {
        switch self {
        case .light: return String(localized: "Parakeet (dictation), Qwen2.5 1.5B (commands), Kokoro (voice)")
        case .balanced: return String(localized: "Parakeet (dictation), Qwen3 4B (commands and assistant), Kokoro (voice)")
        case .full: return String(localized: "Balanced + vision (Qwen3.5 2B looks at the screen on demand)")
        }
    }

    var downloadGB: Double {
        switch self {
        case .light: return 3
        case .balanced: return 4.5
        case .full: return 7
        }
    }

    /// Recommended total memory (the system and your other apps keep a good share of it).
    var minimumMemoryGB: Int {
        switch self {
        case .light: return 8
        case .balanced: return 16
        case .full: return 24
        }
    }
}

struct ProductVerdict: Identifiable {
    enum Level { case good, slow, no }
    let id: String
    let product: String
    let level: Level
    let note: String
}

extension MachineInfo {
    /// The largest pack that fits in memory, with room to download it.
    var recommendedPack: ModelPack? {
        guard appleSilicon else { return nil }
        return ModelPack.allCases.reversed().first { memoryGB >= $0.minimumMemoryGB && freeDiskGB >= $0.downloadGB + 2 }
    }

    /// First-generation base chip (M1 without Pro / Max): everything works, just slower.
    var isEntryChip: Bool { chip == "Apple M1" }

    var verdicts: [ProductVerdict] {
        let all = String(localized: "All three products")
        let dictation = String(localized: "Dictation")
        let command = String(localized: "Voice commands")
        let assistant = String(localized: "Voice assistant")
        guard appleSilicon else {
            let note = String(localized: "Voice Island requires a Mac with Apple silicon (M1 or later).")
            return [ProductVerdict(id: "all", product: all, level: .no, note: note)]
        }
        // On an M1, every note gets a second sentence; each variant is one full string for the translation.
        let m1 = isEntryChip
        switch memoryGB {
        case ..<8:
            return [ProductVerdict(id: "all", product: all, level: .no,
                                   note: String(localized: "You need at least 8 GB of memory."))]
        case ..<16:
            return [
                ProductVerdict(id: "dictation", product: dictation, level: .good,
                               note: m1 ? String(localized: "Fast. M1 chip: a bit slower.") : String(localized: "Fast.")),
                ProductVerdict(id: "command", product: command, level: .slow,
                               note: String(localized: "With a small model: slower and less clever.")),
                ProductVerdict(id: "assistant", product: assistant, level: .no,
                               note: String(localized: "Not recommended: not enough memory to listen, think and speak at once.")),
            ]
        case ..<24:
            return [
                ProductVerdict(id: "dictation", product: dictation, level: .good,
                               note: m1 ? String(localized: "Fast. M1 chip: a bit slower.") : String(localized: "Fast.")),
                ProductVerdict(id: "command", product: command, level: .good,
                               note: m1 ? String(localized: "Quick to respond. M1 chip: a bit slower.")
                                   : String(localized: "Quick to respond.")),
                ProductVerdict(id: "assistant", product: assistant, level: .slow,
                               note: String(localized: "Works, with replies in a few seconds. Close memory-hungry apps.")),
            ]
        default:
            return [
                ProductVerdict(id: "dictation", product: dictation, level: .good,
                               note: m1 ? String(localized: "Fast. M1 chip: a bit slower.") : String(localized: "Fast.")),
                ProductVerdict(id: "command", product: command, level: .good,
                               note: m1 ? String(localized: "Quick to respond. M1 chip: a bit slower.")
                                   : String(localized: "Quick to respond.")),
                ProductVerdict(id: "assistant", product: assistant, level: .good,
                               note: m1 ? String(localized: "Works well, screen vision included. M1 chip: a bit slower.")
                                   : String(localized: "Works well, screen vision included.")),
            ]
        }
    }
}
