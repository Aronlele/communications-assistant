import AppKit
import SwiftUI

struct TaskWizardView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    @ViewState private var step = 0
    @ViewState private var name = "回复任务"
    @ViewState private var target = ""
    @ViewState private var allowUser = false
    @ViewState private var sample = ""
    @ViewState private var mode = "fixed"
    @ViewState private var reply = "1"
    @ViewState private var minutes = 5
    @ViewState private var custom = false
    @ViewState private var template = ""
    @ViewState private var result: Object = [:]
    @ViewState private var validatedDraft = ""
    let titles = ["选择对象", "粘贴提示", "设置回复", "验证与保存"]
    var draft: Object { ["mode": mode, "match_text": sample, "match_mode": "exact", "reply_text": reply,
                        "valid_minutes": minutes, "prompt_template": custom && mode == "keyword" ? template : "", "sample_text": sample] }
    var fingerprint: String { String(data: (try? JSONSerialization.data(withJSONObject: draft, options: [.sortedKeys])) ?? Data(), encoding: .utf8) ?? "" }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("新建任务 · \(step + 1)/4 \(titles[step])").font(.title2)
            Text("当前账号：\(model.accountName)。只创建关闭的新任务，其他任务继续运行。").font(.caption).foregroundStyle(.secondary)
            if !model.error.isEmpty { Text(model.error).foregroundStyle(.orange).textSelection(.enabled) }
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if step == 0 {
                        TextField("任务名称", text: $name).textFieldStyle(.roundedBorder)
                        TextField("机器人或联系人 @用户名、数字 ID", text: $target).textFieldStyle(.roundedBorder)
                        Toggle("允许选择普通联系人，稍后核对固定身份", isOn: $allowUser)
                        Text(model.demo ? "演示对象：1003或1002，不连接Telegram。" : "保存时核对对象身份；输入用户名不会向对方发送消息。")
                    } else if step == 1 {
                        Text("粘贴一条完整提示，保留符号和换行。编号、时限和关键词会随消息变化时，下一步选择提取关键词。")
                        TextEditor(text: $sample).frame(height: 220).border(.quaternary)
                    } else if step == 2 {
                        Picker("回复方式", selection: $mode) { Text("匹配这条提示，回复固定内容").tag("fixed"); Text("从编号提示提取关键词").tag("keyword") }
                        if mode == "fixed" {
                            TextField("固定回复，例如1", text: $reply).textFieldStyle(.roundedBorder)
                            Stepper("本地有效期：\(minutes)分钟", value: $minutes, in: 1...1440)
                            Text("使用全文匹配。没有可靠关联的回执时，只能确认发送，不能确认登记。")
                        } else {
                            Text("内置格式支持变化的编号、称呼、分钟数和关键词；不会把当前样例中的词保存为固定回复。")
                            DisclosureGroup("高级：自定义提取结构") {
                                Toggle("使用自定义提取模板", isOn: $custom)
                                if custom {
                                    Button("从标准提示生成") { model.request("task_template_from_sample", ["text": sample]) { if let value = $0 { template = value.string("prompt_template") } } }
                                    TextEditor(text: $template).font(.body.monospaced()).frame(height: 180).border(.quaternary)
                                    Text("各保留一个{number}、{minutes}、{keyword}，关键词独占一行；可选{name}，其余文字精确匹配。")
                                }
                            }
                        }
                    } else {
                        Text("\(name) → \(target)").font(.headline)
                        Text("仅处理该对象的新私聊；仍需核对身份并单独开启。")
                        Button("验证草稿，不发送消息") {
                            let tested = fingerprint
                            model.request("task_draft_preview", ["draft": draft, "text": sample]) { value in
                                result = value ?? [:]; validatedDraft = result.bool("recognized") ? tested : ""
                            }
                        }
                        if !result.isEmpty { PreviewResult(value: result) }
                        if validatedDraft != fingerprint && !result.isEmpty { Text("样例或回复已变化，请重新验证。").foregroundStyle(.orange) }
                    }
                }.padding(2)
            }
            HStack {
                Button("取消", role: .cancel) { dismiss() }
                if step > 0 { Button("上一步") { step -= 1 } }
                Spacer()
                if step < 3 { Button("下一步") { step += 1 }.disabled((step == 0 && (name.trimmingCharacters(in: .whitespaces).isEmpty || target.trimmingCharacters(in: .whitespaces).isEmpty)) || (step == 1 && sample.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)) }
                else { Button("保存为关闭的任务") { save() }.buttonStyle(.borderedProminent).disabled(validatedDraft != fingerprint || validatedDraft.isEmpty || model.busy) }
            }
        }.padding(26).frame(width: 700, height: 600)
    }
    func save() {
        var args = draft
        args.merge(["id": "new", "name": name, "target": target, "allow_user_test": allowUser]) { _, new in new }
        model.request("task_save", args) { value in
            guard let value else { return }
            model.request("task_preview", ["id": value.string("saved_task_id"), "text": sample]) { tested in
                guard tested != nil else { return }
                model.info = "任务已保存并验证，保持关闭；请在任务列表核对对象后开启"; dismiss()
            }
        }
    }
}

struct NetworkSettingsView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var mode = "direct"
    @ViewState private var host = ""
    @ViewState private var port = "1080"
    @ViewState private var username = ""
    @ViewState private var needsPassword = false
    @ViewState private var password = ""
    @ViewState private var result: Object = [:]
    @ViewState private var apply = false
    var config: Object { ["mode": mode, "host": host, "port": Int(port) ?? 0, "username": username, "requires_password": needsPassword] }
    func modeTitle(_ value: String) -> String { ["direct": "直连", "socks5": "SOCKS5", "http": "HTTP CONNECT"][value] ?? "尚未建立" }
    var body: some View {
        Panel(title: "网络诊断与代理", icon: "network") {
            Text("仅作用于当前账号。保存供下次启动或主动重连使用；链路测试不登录、不发送消息，也不会改变当前连接。")
            Picker("连接方式", selection: $mode) { Text("直连").tag("direct"); Text("SOCKS5").tag("socks5"); Text("HTTP CONNECT").tag("http") }
            if mode != "direct" {
                TextField("代理主机名或IP，不含协议和路径", text: $host).textFieldStyle(.roundedBorder)
                TextField("端口", text: $port).textFieldStyle(.roundedBorder)
                TextField("用户名（可选）", text: $username).textFieldStyle(.roundedBorder)
                Toggle("代理需要密码", isOn: $needsPassword)
                if needsPassword { SecureField("代理密码；相同地址留空保留原密码", text: $password).textFieldStyle(.roundedBorder) }
                Text("密码仅保存在此账号的macOS钥匙串中，不进入数据库、普通备份或诊断报告。HTTP代理需支持CONNECT隧道；暂不支持MTProto代理。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button("测试当前填写的链路") {
                    guard let args = arguments() else { return }
                    model.request("network_probe", ["proxy": args]) { result = $0 ?? [:] }
                }
                Button("保存，保留当前连接") { save(reconnect: false) }.disabled(model.demo)
                Button("保存并立即重新连接…") { apply = true }.disabled(model.demo || !model.state.bool("connected"))
            }.disabled(model.busy)
            if !result.isEmpty { Text((result.bool("ok") ? "链路测试通过 · " : "链路测试未通过 · ") + result.string("detail")).foregroundStyle(result.bool("ok") ? Color.secondary : .orange).textSelection(.enabled) }
            let active = model.state.object("active_proxy")
            Text("当前连接：\(modeTitle(active.string("mode")))；已保存：\(modeTitle(model.state.object("network_proxy").string("mode")))。代理或网络变化后，请核对账号和任务恢复状态。")
                .font(.caption).foregroundStyle(.secondary)
        }
        .onAppear { load() }
        .onDisappear { password = "" }
        .alert("重新连接当前账号", isPresented: $apply) {
            Button("取消", role: .cancel) { }
            Button("确认保存并重新连接") { save(reconnect: true) }
        } message: { Text("此账号会短暂中断并暂停原有自动回复，重新连接后需确认账号并恢复原任务；其他账号继续运行。不补发重连前的旧提示。") }
    }
    func load() {
        let cfg = model.state.object("network_proxy")
        mode = cfg.string("mode").isEmpty ? "direct" : cfg.string("mode")
        host = cfg.string("host"); port = cfg.number("port") > 0 ? String(Int(cfg.number("port"))) : "1080"
        username = cfg.string("username"); needsPassword = cfg.bool("requires_password"); password = ""
    }
    func arguments() -> Object? {
        var value = config
        if mode != "direct" && needsPassword {
            let saved = model.state.object("network_proxy")
            if password.isEmpty && (mode != saved.string("mode") || host != saved.string("host") || Int(port) != Int(saved.number("port")) || username != saved.string("username") || !saved.bool("requires_password")) {
                model.error = "代理地址或认证已变化，请重新输入密码，不能把旧密码用于另一个代理"; return nil
            }
            let secret = password.isEmpty && !model.demo ? Keychain.read(slot: model.selectedSlot, kind: "proxy_password") : password
            guard !secret.isEmpty else { model.error = "请输入代理密码"; return nil }
            value["password"] = secret
        }
        return value
    }
    func save(reconnect: Bool) {
        guard !model.demo, let value = arguments() else { return }
        let slot = model.selectedSlot
        let previous = Keychain.read(slot: slot, kind: "proxy_password")
        do { if let secret = value["password"] as? String { try Keychain.save(secret, slot: slot, kind: "proxy_password") } }
        catch { model.error = "钥匙串保存失败，网络配置未修改"; return }
        model.request("network_save", ["proxy": config]) { saved in
            guard saved != nil else {
                if previous.isEmpty { try? Keychain.clear(slot: slot, kind: "proxy_password") }
                else { try? Keychain.save(previous, slot: slot, kind: "proxy_password") }
                return
            }
            if mode == "direct" || !needsPassword { try? Keychain.clear(slot: slot, kind: "proxy_password") }
            password = ""
            if reconnect {
                model.request("network_apply", ["proxy": value, "confirmed_account": Int64(model.state.number("account"))]) { if $0 != nil { model.info = "当前账号网络已重新连接，请确认身份和恢复原任务" } }
            } else { model.info = "网络配置已保存，当前连接和任务不变，下次启动或主动重连时生效" }
        }
    }
}

struct BackupPasswordView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var password = ""
    @ViewState private var confirmation = ""
    var restoring: Bool { model.backupPasswordForRestore }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(restoring ? "解锁加密备份" : "创建加密备份").font(.title2)
            Text("业务备份不含Telegram登录会话或API凭据。密码不保存，遗失后无法恢复；请保存在你信任的密码管理器中。")
            SecureField("备份密码", text: $password).textFieldStyle(.roundedBorder)
            if !restoring { SecureField("再次输入密码（至少10个字符）", text: $confirmation).textFieldStyle(.roundedBorder) }
            HStack {
                Button("取消", role: .cancel) { password = ""; confirmation = ""; model.restorePassword = ""; model.showBackupPassword = false }
                Spacer()
                Button(restoring ? "解锁并预览" : "选择保存位置") {
                    let secret = password; password = ""; confirmation = ""; model.showBackupPassword = false
                    if restoring { model.restorePassword = secret; model.previewBackup() }
                    else { model.backupData(password: secret) }
                }.disabled(password.isEmpty || (!restoring && (password.count < 10 || password != confirmation)))
            }
        }.padding(26).frame(width: 540)
        .onDisappear { password = ""; confirmation = "" }
    }
}

struct RetainedAccountsView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var rows: [Object] = []
    @ViewState private var confirming: Object = [:]
    @ViewState private var confirm = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("恢复保留的账号资料").font(.title2)
            Text("只查找本应用中已移除入口保留的目录，不扫描其他位置。恢复原入口和资料后需确认身份与任务，不自动开启发送。")
            if !model.error.isEmpty { Text(model.error).foregroundStyle(.orange) }
            ScrollView {
                ForEach(Array(rows.enumerated()), id: \.offset) { item in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(item.element.string("name")).font(.headline)
                        if item.element.bool("unavailable") { Text(item.element.string("detail")) }
                        else {
                            Text("ID \(String(Int64(item.element.number("account")))) · \(Int(item.element.object("counts").number("messages")))条消息 · \(Int(item.element.object("counts").number("executions")))条发送记录")
                            if item.element.bool("duplicate") { Text("此身份已在现有入口中，不能重复恢复。").foregroundStyle(.orange) }
                            Button("恢复此资料入口…") { confirming = item.element; confirm = true }.disabled(model.busy || item.element.bool("duplicate"))
                        }
                    }.padding(12).frame(maxWidth: .infinity, alignment: .leading).background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
                }
                if rows.isEmpty { Text("没有发现可恢复的保留入口。现有账号不受影响。") }
            }
            HStack { Button("关闭") { model.showRetainedAccounts = false }; Spacer(); Button("刷新") { load() }.disabled(model.busy) }
        }.padding(26).frame(width: 640, height: 500)
        .onAppear { load() }
        .alert("确认恢复本地资料入口", isPresented: $confirm) {
            Button("取消", role: .cancel) { }
            Button("恢复入口") {
                model.request("restore_account_entry", ["slot": confirming.string("slot"), "digest": confirming.string("digest"), "confirmed_account": Int64(confirming.number("account"))]) { result in
                    if result != nil { model.showRetainedAccounts = false; model.info = "原资料入口已恢复，请确认对应身份；自动回复保持关闭" }
                }
            }
        } message: { Text("\(confirming.string("name"))（\(String(Int64(confirming.number("account"))))）\n挂回原资料目录，保留归档和防重账本，不与其他账号合并。") }
    }
    func load() { model.request("retained_accounts") { if let value = $0 { rows = value.rows("rows") } } }
}

struct ProductUpdateView: View {
    @EnvironmentObject var model: AppModel
    @StateObject private var updater = ProductUpdater()
    @ViewState private var includeTests = true
    @ViewState private var acceptUnsigned = false
    @ViewState private var confirm = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("检查更新与安全升级").font(.title2)
            Text("当前：\(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "未知") · Apple Silicon")
            Toggle("包含测试预发布", isOn: $includeTests).disabled(updater.busy).onChange(of: includeTests) { _, _ in acceptUnsigned = false; updater.resetChannel() }
            HStack {
                Button("检查官方更新") { acceptUnsigned = false; updater.check(includeTests: includeTests) }.disabled(updater.busy)
                Link("官网下载与版本记录", destination: URL(string: ReleasePolicy.base + "/releases")!)
            }
            Text(updater.status).textSelection(.enabled)
            if !model.error.isEmpty { Text(model.error).foregroundStyle(.orange) }
            if let offer = updater.offer {
                Text("新版 \(offer.version)\(offer.prerelease ? " · 测试版" : "")").font(.headline)
                ScrollView { Text(offer.notes).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(maxHeight: 160)
                Button("下载并验证安装包") { updater.download() }.disabled(updater.busy)
                if let file = updater.downloaded {
                    if !updater.trustedInstaller { Toggle("我已核对来源，理解测试包签名或公证尚未确认", isOn: $acceptUnsigned) }
                    Text("升级会短暂停止全部账号。准备完整本机回退副本（包含登录会话，请勿分享），再打开系统安装器并退出当前应用。安装后重新确认身份和恢复原任务，不补发旧提示；取消安装时可重新打开原应用。")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("当前\(model.accounts.count)个入口，\(model.accounts.reduce(0) { $0 + Int($1.number("enabled_tasks")) })条已开启任务；原暂停状态保留。")
                    Button("备份并准备升级…") { confirm = true }.disabled(model.demo || model.busy || updater.busy || (!updater.trustedInstaller && !acceptUnsigned))
                        .alert("确认升级并短暂中断任务", isPresented: $confirm) {
                            Button("取消", role: .cancel) { }
                            Button("备份并打开安装器") { model.prepareUpgrade(file, offer: offer, allowUnsigned: acceptUnsigned) }
                        } message: { Text("先备份全部入口、数据、旧应用和偏好。任何校验或备份失败都保留原应用继续运行；成功打开系统安装器后退出当前应用。") }
                }
            }
            Spacer()
            HStack { Button("关闭") { model.showUpdates = false }; Spacer(); if updater.busy { Button("取消检查或下载") { updater.cancel() } } }
        }.padding(26).frame(width: 700, height: 650)
        .onDisappear { updater.cancel() }
    }
}
