import Foundation

struct TelegramAPIConfiguration: Equatable {
    let apiID: Int32
    let apiHash: String
    var isTestOnly: Bool { apiID == 17349 }

    init(apiID: String, apiHash: String, saved: TelegramAPIConfiguration? = nil) throws {
        let id = apiID.trimmingCharacters(in: .whitespacesAndNewlines)
        let inputHash = apiHash.trimmingCharacters(in: .whitespacesAndNewlines)
        let hash = inputHash.isEmpty && Int32(id) == saved?.apiID ? saved?.apiHash ?? "" : inputHash
        guard !id.isEmpty, id.utf8.allSatisfy({ (48...57).contains($0) }),
              let number = Int32(id), number > 0, hash.utf8.count == 32,
              hash.utf8.allSatisfy({ (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) }) else {
            throw NSError(domain: "通讯助手", code: 3, userInfo: [NSLocalizedDescriptionKey: "API ID 应为正整数，API Hash 应为 32 位十六进制字符串"])
        }
        self.apiID = number
        self.apiHash = hash
    }

    static func load(from url: URL) throws -> TelegramAPIConfiguration {
        struct Payload: Decodable { let api_id: Int32; let api_hash: String }
        let payload = try JSONDecoder().decode(Payload.self, from: Data(contentsOf: url))
        return try TelegramAPIConfiguration(apiID: String(payload.api_id), apiHash: payload.api_hash)
    }
}

enum TelegramAPISource: String, CaseIterable, Identifiable {
    case builtIn, custom
    var id: String { rawValue }
    var title: String { self == .builtIn ? "产品内置配置" : "自定义 API" }

    static func restore(savedValue: String?, legacyID: String) -> TelegramAPISource {
        if let value = savedValue, let source = TelegramAPISource(rawValue: value) { return source }
        return legacyID.isEmpty ? .builtIn : .custom
    }

    func resolve(builtIn: TelegramAPIConfiguration?, custom: TelegramAPIConfiguration?) -> TelegramAPIConfiguration? {
        self == .builtIn ? builtIn : custom
    }
}
