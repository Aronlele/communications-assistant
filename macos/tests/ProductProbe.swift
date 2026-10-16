import AppKit
import CryptoKit
import Foundation

@main
struct ProductProbe {
    @MainActor static func main() async {
        NSApplication.shared.setActivationPolicy(.prohibited)
        var count = 0
        func check(_ value: Bool, _ name: String) { count += 1; if !value { print("FAIL: " + name); exit(1) } }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let package = root.appendingPathComponent("synthetic.pkg"); let bytes = Data("synthetic update package".utf8)
        try! bytes.write(to: package)
        let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        let offer = UpdateOffer(version: "0.7.0", url: URL(string: "https://github.com")!, page: URL(string: "https://github.com")!, sha256: digest, size: Int64(bytes.count), notes: "", prerelease: true)
        let updater = ProductUpdater(); updater.offer = offer; updater.downloaded = package; updater.trustedInstaller = true
        updater.resetChannel()
        check(updater.offer == nil && updater.downloaded == nil && !updater.trustedInstaller, "切换渠道清除上个渠道的下载授权")
        let model = AppModel(); model.demo = false
        model.accounts = [["slot":"legacy", "account":101,"enabled_tasks":1]]
        model.state = ["auth":"ready","account":101,"settings":["rule":["automatic":true]]]
        let original = model.settings
        model.prepareUpgrade(package, offer: offer, allowUnsigned: true)
        let first = model.probeRequests.last!
        check(first.string("method") == "upgrade_prepare", "安装前先准备完整回退备份")
        check(first.object("args")["confirmed_slots"] as? [String] == ["legacy"], "升级清单固定全部原入口")
        model.receive(["id":first.string("id"),"slot":"legacy","error":"synthetic backup failure"])
        check(!model.preparingUpgrade && model.probeInstallers.isEmpty && model.probeTerminations == 0, "备份失败不打开安装器或退出")
        model.error = ""
        model.prepareUpgrade(package, offer: offer, allowUnsigned: true)
        let second = model.probeRequests.last!
        let backup = root.appendingPathComponent("installer-failure");try! FileManager.default.createDirectory(at: backup, withIntermediateDirectories: true)
        model.receive(["id":second.string("id"),"slot":"legacy","result":["backup_root":backup.path]])
        for _ in 0..<100 { if !model.preparingUpgrade { break }; try? await Task.sleep(nanoseconds:50_000_000) }
        check(FileManager.default.fileExists(atPath:backup.appendingPathComponent("previous.app").path), "打开安装器前保留旧应用")
        check(FileManager.default.fileExists(atPath:backup.appendingPathComponent("preferences.plist").path), "打开安装器前保留偏好")
        check(model.probeTerminations == 0 && !model.isShuttingDown, "安装器打不开时工作应用继续运行")
        check(model.settings.object("rule").bool("automatic") == original.object("rule").bool("automatic"), "升级准备失败不改变原任务")
        let oldRequests = model.probeRequests.count
        try! Data([0]).write(to:package)
        model.prepareUpgrade(package, offer:offer,allowUnsigned:true)
        check(model.probeRequests.count == oldRequests && model.probeTerminations == 0, "下载被改动时不备份或退出")
        try! bytes.write(to:package)
        model.error = "";model.probeShouldOpen = true
        model.prepareUpgrade(package,offer:offer,allowUnsigned:true)
        let third = model.probeRequests.last!
        let final = root.appendingPathComponent("successful-preparation");try! FileManager.default.createDirectory(at:final,withIntermediateDirectories:true)
        model.receive(["id":third.string("id"),"slot":"legacy","result":["backup_root":final.path]])
        for _ in 0..<100 { if model.probeTerminations > 0 { break };try? await Task.sleep(nanoseconds:50_000_000) }
        check(model.probeTerminations == 1 && model.probeInstallers.count == 2, "备份完整后才请求安装器与正常退出")
        check(FileManager.default.fileExists(atPath:final.appendingPathComponent("previous.app").path), "成功准备保留可回退旧应用")
        print("Native safe upgrade simulation: \(count) checks passed; no real installer opened or app stopped")
        exit(0)
    }
}
