import AppKit
import CryptoKit
import Foundation

struct UpdateOffer {
    let version: String
    let url: URL
    let page: URL
    let sha256: String
    let size: Int64
    let notes: String
    let prerelease: Bool
}

enum ReleasePolicy {
    static let repository = "Aronlele/communications-assistant"
    static let base = "https://github.com/" + repository
    static func parts(_ version: String) -> [Int]? {
        guard version.range(of: #"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"#, options: .regularExpression) != nil, version.count <= 32 else { return nil }
        let values = version.split(separator: ".").compactMap { Int($0) }
        return values.count == 3 ? values : nil
    }
    static func offer(_ rows: [Object], current: String, includeTests: Bool) throws -> UpdateOffer? {
        guard let currentParts = parts(current) else { throw failure("当前应用版本格式无效，未进行更新") }
        var offers: [UpdateOffer] = []
        for row in rows {
            guard row["draft"] as? Bool == false, let testing = row["prerelease"] as? Bool, includeTests || !testing else { continue }
            let tag = row.string("tag_name")
            guard tag.hasPrefix("v"), let versionParts = parts(String(tag.dropFirst())), currentParts.lexicographicallyPrecedes(versionParts) else { continue }
            let version = String(tag.dropFirst())
            let name = "CommunicationsAssistant-\(version)-macOS-arm64.pkg"
            let assets = row.rows("assets").filter { $0.string("name") == name && $0.string("state") == "uploaded" }
            guard assets.count == 1, let asset = assets.first else { continue }
            let url = base + "/releases/download/\(tag)/\(name)"
            let page = base + "/releases/tag/\(tag)"
            let digest = asset.string("digest")
            guard row.string("html_url") == page, asset.string("browser_download_url") == url,
                  digest.range(of: #"^sha256:[0-9a-f]{64}$"#, options: .regularExpression) != nil,
                  asset.number("size") > 0, asset.number("size") <= 1024*1024*1024 else { continue }
            offers.append(UpdateOffer(version: version, url: URL(string: url)!, page: URL(string: page)!, sha256: String(digest.dropFirst(7)), size: Int64(asset.number("size")), notes: String(row.string("body").prefix(12000)), prerelease: testing))
        }
        return offers.max { parts($0.version)!.lexicographicallyPrecedes(parts($1.version)!) }
    }
    static func verify(_ file: URL, offer: UpdateOffer) throws {
        let size = (try FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.int64Value
        guard size == offer.size else { throw failure("下载文件长度不符，保留原应用，请重新下载") }
        let input = try FileHandle(forReadingFrom: file); defer { try? input.close() }
        var hasher = SHA256()
        while let data = try input.read(upToCount: 1024*1024), !data.isEmpty { hasher.update(data: data) }
        let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        guard digest == offer.sha256 else { throw failure("安装包SHA256不符，已拒绝安装；原应用未改动") }
    }
    static func failure(_ text: String) -> NSError { NSError(domain: "即讯更新", code: 1, userInfo: [NSLocalizedDescriptionKey: text]) }
}

final class UpdateRedirectPolicy: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    static func allowed(_ url: URL?) -> Bool {
        guard let url, url.scheme == "https", url.user == nil, url.password == nil, url.port == nil || url.port == 443 else { return false }
        return ["api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"].contains(url.host ?? "")
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(Self.allowed(request.url) ? request : nil)
    }
}

@MainActor
final class ProductUpdater: ObservableObject {
    @Published var offer: UpdateOffer?
    @Published var status = "尚未检查"
    @Published var downloaded: URL?
    @Published var busy = false
    @Published var trustedInstaller = false
    private var operation: Task<Void, Never>?
    private var generation = UUID()
    private static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 30; config.timeoutIntervalForResource = 600
        config.httpShouldSetCookies = false; config.urlCredentialStorage = nil
        return URLSession(configuration: config, delegate: UpdateRedirectPolicy(), delegateQueue: nil)
    }
    func resetChannel() {
        cancel(); offer = nil; downloaded = nil; trustedInstaller = false; status = "渠道已变化，请重新检查更新"
    }
    func check(includeTests: Bool) {
        cancel(); busy = true; offer = nil; downloaded = nil; status = "正在检查官方发布记录…"
        let token = generation
        operation = Task {
            defer { if generation == token { busy = false } }
            do {
                let session = Self.session(); defer { session.invalidateAndCancel() }
                var request = URLRequest(url: URL(string: "https://api.github.com/repos/\(ReleasePolicy.repository)/releases?per_page=30")!)
                request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
                request.setValue("Jixun-Update", forHTTPHeaderField: "User-Agent")
                let (data, response) = try await session.data(for: request)
                guard let response = response as? HTTPURLResponse, response.statusCode == 200, data.count <= 4*1024*1024,
                      let rows = try JSONSerialization.jsonObject(with: data) as? [Object] else { throw ReleasePolicy.failure("发布记录暂不可用；请稍后重试或打开官网下载页") }
                try Task.checkCancellation()
                offer = try ReleasePolicy.offer(rows, current: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0", includeTests: includeTests)
                status = offer == nil ? "当前渠道没有可验证的更新。缺少完整校验信息的附件不会用于升级。" : "发现新版本，请阅读变化和升级影响。"
            } catch is CancellationError { if generation == token { status = "已取消检查" } }
            catch { if generation == token { status = "检查失败：请检查网络或稍后重试，原应用继续运行。" } }
        }
    }
    func download() {
        guard let offer else { return }
        cancel(); busy = true; downloaded = nil; trustedInstaller = false; status = "正在下载安装包，完成后核对完整长度与SHA256…"
        let token = generation
        operation = Task {
            defer { if generation == token { busy = false } }
            do {
                let session = Self.session(); defer { session.invalidateAndCancel() }
                let (temporary, response) = try await session.download(from: offer.url)
                defer { try? FileManager.default.removeItem(at: temporary) }
                guard let response = response as? HTTPURLResponse, response.statusCode == 200, UpdateRedirectPolicy.allowed(response.url) else { throw ReleasePolicy.failure("下载来源或状态无效") }
                try Task.checkCancellation()
                try await Task.detached { try ReleasePolicy.verify(temporary, offer: offer) }.value
                try Task.checkCancellation()
                let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                let directory = support.appendingPathComponent("即讯更新").appendingPathComponent(offer.version)
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                let destination = directory.appendingPathComponent(offer.url.lastPathComponent)
                if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
                try FileManager.default.moveItem(at: temporary, to: destination)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
                downloaded = destination
                let assessed = await Task.detached { Self.assess(destination) }.value
                try Task.checkCancellation()
                guard generation == token else { return }
                trustedInstaller = assessed
                status = trustedInstaller ? "下载及SHA256验证通过，系统安装器信任检查通过。" : "下载及SHA256验证通过；系统未确认此安装器的签名或公证。测试包需要你明确确认，不能视为正式可信发行。"
            } catch is CancellationError { if generation == token { status = "下载已取消，原应用未改动" } }
            catch { if generation == token { status = (error as NSError).domain == "即讯更新" ? error.localizedDescription : "下载或校验失败，原应用未改动，请重试" } }
        }
    }
    nonisolated static func assess(_ file: URL) -> Bool {
        let process = Process(); process.executableURL = URL(fileURLWithPath: "/usr/sbin/spctl")
        process.arguments = ["--assess", "--type", "install", file.path]
        process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        do { try process.run(); process.waitUntilExit(); return process.terminationStatus == 0 } catch { return false }
    }
    func cancel() { generation = UUID(); operation?.cancel(); operation = nil; busy = false }
}

extension AppModel {
    func proxyArguments(for slot: String, configuration: Object? = nil) -> Object {
        var value = configuration ?? (slot == selectedSlot ? state.object("network_proxy") : accounts.first(where: { $0.string("slot") == slot })?.object("network_proxy") ?? [:])
        if value.bool("requires_password") && !demo { value["password"] = Keychain.read(slot: slot, kind: "proxy_password") }
        return value
    }
    func connectionArguments(_ configuration: TelegramAPIConfiguration, slot: String) -> Object {
        ["api_id": Int(configuration.apiID), "api_hash": configuration.apiHash, "proxy": proxyArguments(for: slot)]
    }
    func prepareUpgrade(_ file: URL, offer: UpdateOffer, allowUnsigned: Bool) {
        guard !demo, !busy, !preparingUpgrade, Bundle.main.bundleIdentifier == "local.communications.assistant" else { error = "独立演示或其他操作进行中，不能替换工作应用"; return }
        do { try ReleasePolicy.verify(file, offer: offer) } catch { self.error = error.localizedDescription; return }
        if !allowUnsigned && !ProductUpdater.assess(file) { error = "系统未确认安装器信任，请先核对测试包提示"; return }
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let destination = support.appendingPathComponent("即讯升级备份").appendingPathComponent(UUID().uuidString)
        preparingUpgrade = true
        request("upgrade_prepare", ["destination": destination.path, "confirmed_slots": accounts.map { $0.string("slot") }]) { [weak self] value in
            guard let self else { return }
            guard let value else { preparingUpgrade = false; return }
            let backup = URL(fileURLWithPath: value.string("backup_root"))
            Task {
                do {
                    let app = Bundle.main.bundleURL
                    let preferences = UserDefaults.standard.persistentDomain(forName: "local.communications.assistant") ?? [:]
                    try await Task.detached {
                        try FileManager.default.copyItem(at: app, to: backup.appendingPathComponent("previous.app"))
                        let data = try PropertyListSerialization.data(fromPropertyList: preferences, format: .binary, options: 0)
                        try data.write(to: backup.appendingPathComponent("preferences.plist"), options: .atomic)
                        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: backup.appendingPathComponent("preferences.plist").path)
                        try ReleasePolicy.verify(file, offer: offer)
                    }.value
                    guard !self.isShuttingDown else { self.preparingUpgrade = false; return }
                    guard NSWorkspace.shared.open(file) else { throw ReleasePolicy.failure("系统安装器未能打开，当前应用继续运行，备份已保留") }
                    NSApp.terminate(nil)
                } catch { self.preparingUpgrade = false; self.error = "升级准备失败，原应用继续运行；完整或部分备份已保留供核对。" }
            }
        }
    }
}
