import AppKit
import Foundation
import CryptoKit

@main
struct AccountsProbe {
    @MainActor static func main() {
        NSApplication.shared.setActivationPolicy(.prohibited)
        let model = AppModel()
        let second = "11111111-1111-4111-8111-111111111111"
        let accountRows: [Object] = [
            ["slot": "legacy", "account": 101, "identity": ["name": "第一账号"], "auth": "ready", "connected": true],
            ["slot": second, "account": 202, "identity": ["name": "第二账号"], "auth": "ready", "connected": true]]
        func state(_ slot: String, active: String = "legacy") -> Object {
            ["event": "state", "slot": slot, "data": ["slot": slot, "active_slot": active, "accounts": accountRows,
              "account": slot == "legacy" ? 101 : 202, "identity": ["name": slot == "legacy" ? "第一账号" : "第二账号"],
              "auth": "ready", "connected": true, "settings": ["reminders": true]]]
        }
        var checks = 0
        func check(_ condition: Bool, _ text: String) {
            checks += 1
            guard condition else { print("FAIL: " + text); exit(1) }
        }
        model.receive(state("legacy"))
        model.receive(state(second))
        check(model.accountName == "第一账号", "后台账号不能覆盖当前页面")
        check(model.accounts.count == 2, "账号列表应包含全部账号")
        check(model.root == model.storageRoot, "旧账号目录保持原路径")
        model.deliverReminder(["account": 202, "chat": 42, "message_id": 7, "title": "机器人", "count": 1, "body": "样例", "notify": true], slot: second)
        check(model.probeNotices.count == 1, "后台账号仍应提交系统通知")
        check(model.probeNotices[0].object("userInfo").string("slot") == second, "通知必须保留来源账号目录")
        check(model.probeNotices[0].object("userInfo").string("account") == "202", "通知必须保留来源身份")
        check(model.probeNotices[0].string("title").contains("第二账号"), "通知标题区分账号")
        model.deliverReminder(["account": 101, "notify": true], slot: second)
        check(model.probeNotices.count == 1, "错配身份通知必须拒绝")
        model.results = [["text": "第一账号私密内容"]]; model.preview = ["keyword": "样例"]
        model.restorePath = "/tmp/first-account.zip"; model.phone = "synthetic"; model.password = "synthetic"
        model.receive(state(second, active: second))
        check(model.selectedSlot == second && model.accountName == "第二账号", "切换账号显示正确身份")
        check(model.results.isEmpty && model.preview.isEmpty && model.restorePath.isEmpty, "切换账号清空查询、预览和待恢复文件")
        check(model.phone.isEmpty && model.password.isEmpty, "切换账号不保留登录输入")
        check(model.root.path.hasSuffix("accounts/" + second), "新增账号使用独立目录")
        check(model.preferenceKey("apiID") == "apiID." + second, "自定义 API 设置按账号隔离")
        let identity = model.selectedSlot
        model.selectLocalSlot("../../other")
        check(model.selectedSlot == identity, "非法账号目录不能选中")
        model.openNotification(["account": "101", "chat": "42", "message_id": "7"])
        check(model.error.contains("其他账号"), "禁止在错误账号打开消息")
        model.receive(state("legacy", active: "legacy"))
        var completed = false
        model.request("search") { _ in completed = true }
        let request = model.probeRequests.last!
        check(request.string("slot") == "legacy", "每个业务请求必须固定来源账号")
        model.receive(state(second, active: second))
        model.receive(["id": request.string("id"), "slot": "legacy", "result": ["messages": [["text": "私密"]]]])
        check(!completed, "迟到的旧账号查询不能覆盖新账号")
        check(accountStatus(["connected": true, "error_present": true]).contains("需检查"), "后台账号异常应在账号菜单中提示")
        var taskState = state("legacy")
        var taskData = taskState.object("data")
        taskData["tasks"] = [["id": "legacy", "automatic": true], ["id": "second-task", "automatic": false, "resume_pending": true]]
        taskState["data"] = taskData
        model.selection = .automation
        model.receive(taskState)
        check(model.enabledTaskCount == 1 && model.pendingRecovery.count == 1, "任务数量与待恢复状态独立统计")
        check(model.selection == .automation, "周期状态刷新不能把任务编辑页强制切到向导")
        model.accounts = [["slot": "legacy", "account": 101, "connected": true], ["slot": second, "auth": "disconnected"]]
        check(model.accountAttentionCount == 0, "未登录空入口不计为故障")
        model.accounts[1] = ["slot": second, "account": 202, "auth": "offline", "connected": false]
        check(model.accountAttentionCount == 1, "已有身份离线应在全局总览中提示")
        model.accounts[1] = ["slot": second, "auth": "identity", "connected": false]
        check(model.accountAttentionCount == 1, "待确认身份应进入全局关注数量")
        model.accounts = accountRows
        model.showTaskMessage(["target": 42, "message_id": 71])
        let taskRequest = model.probeRequests.last!
        check(taskRequest.string("method") == "notification_context" && taskRequest.string("slot") == "legacy", "任务原消息查询固定来源账号")
        model.receive(["id": taskRequest.string("id"), "slot": "legacy", "result": ["messages": [["id": 71]], "source": "remote"]])
        check(model.showContext && model.context.first?.number("id") == 71, "任务结果可只读定位来源消息")
        model.showContext = false
        model.demo = false
        check(!model.canRemoveAccount, "已连接账号不能直接移除")
        let empty = "22222222-2222-4222-8222-222222222222"
        model.receive(["event": "state", "data": ["slot": empty, "active_slot": empty,
            "accounts": accountRows + [["slot": empty, "auth": "disconnected"]], "auth": "disconnected"]])
        check(model.canRemoveAccount, "空登录入口可移除")
        let before = model.probeRequests.count
        model.removeAccount(second)
        check(model.probeRequests.count == before, "确认后切换账号不得误删另一个入口")
        model.removeAccount(empty)
        let removal = model.probeRequests.last!
        check(removal.string("method") == "remove_account" && removal.string("slot") == empty, "移除请求固定到确认的入口")
        check(removal.object("args").string("confirm") == empty, "移除请求包含精确确认")
        model.receive(state("legacy"))
        model.receive(["id": removal.string("id"), "slot": empty, "result": ["removed_slot": empty]])
        check(model.accounts.count == 2 && model.selectedSlot == "legacy", "移除后切回存留账号")
        check(model.info.contains("已移除"), "切换后仍显示移除完成提示")
        model.receive(["event": "state", "data": ["slot": "legacy", "active_slot": "legacy",
            "accounts": [["slot": "legacy", "auth": "disconnected"]], "auth": "disconnected"]])
        check(!model.canRemoveAccount, "至少保留一个登录入口")
        model.restorePassword = "synthetic-backup-password"
        model.showBackupPassword = true
        model.selectLocalSlot(second)
        check(model.restorePassword.isEmpty && !model.showBackupPassword, "切换账号清除备份密码")
        model.demo = true
        let proxy: Object = ["mode": "socks5", "host": "localhost", "port": 1080, "requires_password": false]
        let proxyArgs = model.proxyArguments(for: second, configuration: proxy)
        check(proxyArgs.string("mode") == "socks5" && proxyArgs["password"] == nil, "代理不需要认证时不读取或转发密码")
        check(ReleasePolicy.parts("0.6.0") == [0,6,0], "更新版本按数字比较")
        check(ReleasePolicy.parts("1.2.3;run") == nil && ReleasePolicy.parts("01.2.3") == nil, "拒绝不合法更新版本")
        let digest = String(repeating: "a", count: 64)
        func release(_ version: String, testing: Bool = true) -> Object {
            let tag = "v" + version
            let name = "CommunicationsAssistant-\(version)-macOS-arm64.pkg"
            return ["tag_name": tag, "draft": false, "prerelease": testing,
                    "html_url": ReleasePolicy.base + "/releases/tag/" + tag,
                    "assets": [["name": name, "state": "uploaded", "size": 12,
                                "browser_download_url": ReleasePolicy.base + "/releases/download/\(tag)/\(name)", "digest": "sha256:" + digest]]]
        }
        func offer(_ rows: [Object], tests: Bool = true) -> UpdateOffer? { try? ReleasePolicy.offer(rows, current: "0.6.0", includeTests: tests) }
        check(offer([release("0.5.0")]) == nil, "拒绝降级")
        check(offer([release("0.7.0")], tests: false) == nil, "正式渠道不混入测试包")
        check(offer([release("0.7.0"),release("0.10.0")])?.version == "0.10.0", "选择最高数字版本")
        var bad = release("0.7.0"); bad["draft"] = true
        check(offer([bad]) == nil, "拒绝草稿发行")
        bad = release("0.7.0"); bad["html_url"] = "https://example.invalid/release"
        check(offer([bad]) == nil, "拒绝其他仓库或来源")
        for field in ["digest", "browser_download_url", "name", "state"] {
            bad = release("0.7.0"); var assets = bad.rows("assets"); assets[0][field] = "invalid"; bad["assets"] = assets
            check(offer([bad]) == nil, "拒绝无效安装附件字段：" + field)
        }
        bad = release("0.7.0"); bad["assets"] = bad.rows("assets") + bad.rows("assets")
        check(offer([bad]) == nil, "拒绝重复同名安装器")
        check(UpdateRedirectPolicy.allowed(URL(string: "https://release-assets.githubusercontent.com/asset")), "只允许官方附件重定向")
        for url in ["http://github.com/a", "https://evil.invalid/a", "https://user:pass@github.com/a", "https://github.com:444/a"] {
            check(!UpdateRedirectPolicy.allowed(URL(string: url)), "拒绝不安全重定向")
        }
        let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: temporary) }
        let file = temporary.appendingPathComponent("synthetic.pkg"); let data = Data("synthetic-package".utf8)
        try! data.write(to: file)
        let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let valid = UpdateOffer(version: "0.7.0", url: URL(string: "https://github.com")!, page: URL(string: "https://github.com")!, sha256: hash, size: Int64(data.count), notes: "", prerelease: true)
        do { try ReleasePolicy.verify(file, offer: valid); check(true, "有效安装包通过校验") } catch { check(false, "有效包应通过完整校验") }
        try! Data(repeating: 0, count: data.count).write(to: file)
        do { try ReleasePolicy.verify(file, offer: valid); check(false, "篡改包必须拒绝") } catch { check(error.localizedDescription.contains("SHA256"), "同长度篡改由SHA256拒绝") }
        try! Data([0]).write(to: file)
        do { try ReleasePolicy.verify(file, offer: valid); check(false, "截断包必须拒绝") } catch { check(error.localizedDescription.contains("长度"), "截断文件由长度拒绝") }
        check(FileManager.default.fileExists(atPath: file.path), "校验失败不删除其他文件或修改应用")
        model.prepareUpgrade(file, offer: valid, allowUnsigned: true)
        check(model.error.contains("不能替换"), "演示模式不能升级工作应用")
        print("Native multi-account and readiness state: \(checks) checks passed")
        exit(0)
    }
}
