import Foundation
import IOKit
import Metal

/// What this Mac can run: chip, memory, GPU, disk, then a verdict per product.
/// Used by the dashboard's "Ton Mac" page, and will be used by the first-launch assistant (pack choice).
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
            chip: sysctlString("machdep.cpu.brand_string") ?? "Puce inconnue",
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
        case .light: return "Léger"
        case .balanced: return "Équilibré"
        case .full: return "Complet"
        }
    }

    var summary: String {
        switch self {
        case .light: return "Parakeet (dictée), Qwen2.5 1.5B (commandes), Kokoro (voix)"
        case .balanced: return "Parakeet (dictée), Qwen3 4B (commandes et assistant), Kokoro (voix)"
        case .full: return "Équilibré + vision (Qwen3.5 2B regarde l'écran à la demande)"
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
        guard appleSilicon else {
            let note = "Voice Island demande un Mac avec puce Apple (M1 ou plus récent)."
            return [ProductVerdict(id: "all", product: "Les trois produits", level: .no, note: note)]
        }
        let slower = isEntryChip ? " Puce M1 : un peu plus lent." : ""
        switch memoryGB {
        case ..<8:
            return [ProductVerdict(id: "all", product: "Les trois produits", level: .no,
                                   note: "Il faut au moins 8 Go de mémoire.")]
        case ..<16:
            return [
                ProductVerdict(id: "dictation", product: "Dictée", level: .good, note: "Rapide." + slower),
                ProductVerdict(id: "command", product: "Commandes vocales", level: .slow,
                               note: "Avec un petit modèle : plus lent et moins malin."),
                ProductVerdict(id: "assistant", product: "Assistant vocal", level: .no,
                               note: "Déconseillé : pas assez de mémoire pour écouter, réfléchir et parler à la fois."),
            ]
        case ..<24:
            return [
                ProductVerdict(id: "dictation", product: "Dictée", level: .good, note: "Rapide." + slower),
                ProductVerdict(id: "command", product: "Commandes vocales", level: .good, note: "Rapides." + slower),
                ProductVerdict(id: "assistant", product: "Assistant vocal", level: .slow,
                               note: "Fonctionne, réponses en quelques secondes. Ferme les apps gourmandes."),
            ]
        default:
            return [
                ProductVerdict(id: "dictation", product: "Dictée", level: .good, note: "Rapide." + slower),
                ProductVerdict(id: "command", product: "Commandes vocales", level: .good, note: "Rapides." + slower),
                ProductVerdict(id: "assistant", product: "Assistant vocal", level: .good,
                               note: "Fonctionne bien, regard sur l'écran compris." + slower),
            ]
        }
    }
}
