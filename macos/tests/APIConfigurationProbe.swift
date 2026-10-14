import Foundation

@main
struct APIConfigurationProbe {
    static func main() throws {
        var checks = 0
        func check(_ condition: Bool) {
            precondition(condition, "API configuration regression failed")
            checks += 1
        }
        func invalid(_ id: String, _ hash: String, saved: TelegramAPIConfiguration? = nil) {
            check((try? TelegramAPIConfiguration(apiID: id, apiHash: hash, saved: saved)) == nil)
        }
        // Synthetic test values, never used to connect to Telegram.
        let hash = String(repeating: "a", count: 32)
        let builtIn = try TelegramAPIConfiguration(apiID: "12345", apiHash: hash)
        let custom = try TelegramAPIConfiguration(apiID: "67890", apiHash: String(repeating: "b", count: 32))
        check(try TelegramAPIConfiguration(apiID: " 12345\n", apiHash: " \(hash) ") == builtIn)
        invalid("0", hash); invalid("-1", hash); invalid("2147483648", hash)
        invalid("1.5", hash); invalid("+12", hash); invalid("１２", hash)
        invalid("", hash); invalid("42", ""); invalid("42", String(repeating: "g", count: 32))
        invalid("42", String(repeating: "a", count: 31))
        check(try TelegramAPIConfiguration(apiID: "67890", apiHash: "", saved: custom) == custom)
        invalid("12345", "", saved: custom)
        check(TelegramAPISource.restore(savedValue: nil, legacyID: "") == .builtIn)
        check(TelegramAPISource.restore(savedValue: nil, legacyID: "67890") == .custom)
        check(TelegramAPISource.restore(savedValue: "builtIn", legacyID: "67890") == .builtIn)
        check(TelegramAPISource.restore(savedValue: "custom", legacyID: "") == .custom)
        check(TelegramAPISource.restore(savedValue: "invalid", legacyID: "67890") == .custom)
        check(TelegramAPISource.builtIn.resolve(builtIn: builtIn, custom: custom) == builtIn)
        check(TelegramAPISource.custom.resolve(builtIn: builtIn, custom: custom) == custom)
        check(TelegramAPISource.builtIn.resolve(builtIn: nil, custom: custom) == nil)
        check(TelegramAPISource.custom.resolve(builtIn: builtIn, custom: nil) == nil)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("TelegramAPI.json")
        func write(_ id: Any, _ value: Any) throws {
            try JSONSerialization.data(withJSONObject: ["api_id": id, "api_hash": value]).write(to: url)
        }
        try write(12345, hash)
        check(try TelegramAPIConfiguration.load(from: url) == builtIn)
        try write(true, hash)
        check((try? TelegramAPIConfiguration.load(from: url)) == nil)
        try write("12345", hash)
        check((try? TelegramAPIConfiguration.load(from: url)) == nil)
        try write(12345, "bad")
        check((try? TelegramAPIConfiguration.load(from: url)) == nil)
        print("Native API configuration: \(checks) checks passed (validation, migration, source selection, missing credentials)")
    }
}
