import SwiftUI
import AppKit

struct ArchiveCleanupItem: Identifiable {
    let id = UUID()
    let value: Object
}

struct ArchiveCleanupView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let options: Object
    @ViewState private var preview: Object = [:]
    @ViewState private var mediaOnly = false
    @ViewState private var keepArchiving = true
    @ViewState private var confirmation = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("清理本地归档").font(.title2)
            Text("账号：\(model.accountName)。只处理本地归档，不删除 Telegram 消息，不修改回复任务或发送账本。")
            if options.string("scope") == "conversation" {
                Toggle("清空历史后继续按原选择归档新消息", isOn: $keepArchiving)
                Text("关闭此项会停止所选会话归档。已经停止的会话不会因此自动开启。")
                    .font(.caption).foregroundStyle(.secondary)
            } else { Toggle("仅清理已保存图片，保留消息文字", isOn: $mediaOnly) }
            Button("预览清理范围") { loadPreview() }.disabled(model.busy)
            if !preview.isEmpty {
                Text("\(Int(preview.number("conversations"))) 个会话 · \(Int(preview.number("count"))) 条消息 · \(Int(preview.number("images"))) 张本地图片")
                Text("图片约 \(ByteCountFormatter.string(fromByteCount: Int64(preview.number("image_bytes")), countStyle: .file))。预览 5 分钟有效；内容变化时须重新预览。")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("输入“删除”确认", text: $confirmation).textFieldStyle(.roundedBorder)
            }
            if !model.error.isEmpty { Text(model.error).foregroundStyle(.orange).textSelection(.enabled) }
            Text("确认后不可撤销；需要保留时请先备份。清理也会移除应用自动迁移快照中的对应内容，不修改你另存的外部备份。清理图片后不会自动重新下载这些图片。")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("取消", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("确认删除本地内容", role: .destructive) {
                    model.request("archive_cleanup_apply", ["token": preview.string("token"), "confirmed_account": Int64(preview.number("account"))]) { result in
                        guard result != nil else { return }
                        model.info = "本地清理已完成，原回复任务和发送账本保留"
                        model.context = []; model.search(); dismiss()
                    }
                }.disabled(preview.string("token").isEmpty || confirmation != "删除" || model.busy)
            }
        }.padding(26).frame(width: 600)
            .onAppear { loadPreview() }
            .onChange(of: mediaOnly) { _, _ in preview = [:]; confirmation = "" }
            .onChange(of: keepArchiving) { _, _ in preview = [:]; confirmation = "" }
    }
    func loadPreview() {
        var args = options; args["media_only"] = mediaOnly; args["keep_archiving"] = keepArchiving
        preview = [:]; confirmation = ""
        model.request("archive_cleanup_preview", args) { preview = $0 ?? [:] }
    }
}

struct TaskSampleCase: Identifiable {
    var id = UUID()
    var text = ""
    var shouldMatch = true
    var expectedReply = ""
    var object: Object { ["text": text, "should_match": shouldMatch, "expected_reply": shouldMatch ? expectedReply : ""] }
}

struct TaskSampleCasesView: View {
    @Binding var cases: [TaskSampleCase]
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("多样例验证").font(.headline)
                Spacer()
                Button("添加样例") { cases.append(TaskSampleCase()) }.disabled(cases.count >= 20)
            }
            Text("最多 20 条、整组总长 8000 字符，至少有一条应匹配样例。可以验证不应匹配的通知，以及匹配后的准确回复；全部通过后才可重新开启任务。")
                .font(.caption).foregroundStyle(.secondary)
            ForEach($cases) { $item in
                VStack(alignment: .leading) {
                    HStack {
                        Toggle("这条消息应该匹配", isOn: $item.shouldMatch)
                        Spacer()
                        Button("移除样例") { cases.removeAll { $0.id == item.id } }
                    }
                    TextEditor(text: $item.text).font(.body.monospaced()).frame(height: 90).border(.quaternary)
                    if item.shouldMatch { TextField("预期回复（可选，填写后精确校验）", text: $item.expectedReply).textFieldStyle(.roundedBorder) }
                }.padding(10).background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }
}

struct SampleSuiteResult: View {
    let value: Object
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(value.bool("passed") ? "全部样例通过" : "样例未全部通过；至少需要一条应匹配样例")
                .foregroundStyle(value.bool("passed") ? Color.accentColor : .orange)
            ForEach(value.rows("rows"), id: \.caseIndex) { row in
                Text("样例 \(Int(row.number("index"))+1)：\(row.bool("passed") ? "通过" : "未通过") · 实际\(row.bool("recognized") ? "匹配" : "未匹配")\(row.string("reply").isEmpty ? "" : " · 回复：" + row.string("reply"))")
                    .font(.caption).textSelection(.enabled)
                if !row.bool("passed") { Text(row.string("explanation")).font(.caption).foregroundStyle(.orange) }
            }
        }
    }
}

extension Dictionary where Key == String, Value == Any {
    var caseIndex: Int { Int(number("index")) }
    var issueCode: String { string("code") }
}

struct IssueCenterView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    @ViewState private var rows: [Object] = []
    @ViewState private var diagnostics = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("异常处理").font(.title2)
            Text("当前账号：\(model.accountName)。核对原因后再操作；此页不会自动恢复发送或重试未知结果。")
                .foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if model.engineUnavailable {
                        Panel(title: "消息引擎未运行", icon: "exclamationmark.triangle") {
                            Text("请先保存或导出可用诊断，检查存储与活动记录，再从错误提示中手动重启引擎。重启后仍须核对账号和恢复任务。")
                        }
                    }
                    if rows.isEmpty && !model.engineUnavailable { Text("当前没有待处理的运行异常。固定回复仍需在原会话核对实际登记结果。") }
                    ForEach(rows, id: \.issueCode) { row in
                        Panel(title: row.string("title"), icon: "exclamationmark.triangle") {
                            Text(row.string("detail")).textSelection(.enabled)
                            Text(row.string("steps")).font(.callout).foregroundStyle(.secondary)
                            Button(actionTitle(row.string("action"))) { navigate(row.string("action")) }
                        }
                    }
                    if !diagnostics.isEmpty {
                        Panel(title: "匿名诊断预览", icon: "doc.text.magnifyingglass") {
                            Text(diagnostics).font(.caption.monospaced()).textSelection(.enabled)
                            Button("保存诊断报告") { model.diagnostics = diagnostics; model.exportDiagnostics() }
                        }
                    }
                    Panel(title: "通知与网络排查", icon: "stethoscope") {
                        Text("未看到横幅时先检查通知中心、系统通知权限和专注模式。网络不通时检查当前网络及代理线路；断网、合盖和睡眠期间无法实时处理。")
                        Button("检查使用准备") { navigate("setup") }
                    }
                }
            }
            HStack { Button("关闭") { dismiss() }; Spacer(); Button("刷新状态") { load() }.disabled(model.busy || model.engineUnavailable) }
        }.padding(24).frame(width: 680, height: 620).onAppear { load() }
            .onChange(of: model.selectedSlot) { _, _ in rows = []; load() }
    }
    func load() {
        rows = model.state.rows("issues")
        if model.ready { model.request("issues") { if let value = $0 { rows = value.rows("rows") } } }
    }
    func actionTitle(_ action: String) -> String {
        ["setup": "检查账号与恢复任务", "account": "检查连接与账号", "diagnostics": "预览诊断报告", "activity": "查看活动记录", "storage": "管理本地存储", "tasks": "核对任务与暂停状态", "results": "查看需关注结果"][action] ?? "查看详情"
    }
    func navigate(_ action: String) {
        if action == "diagnostics" {
            model.request("diagnostics") { value in
                if let value, let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]) { diagnostics = String(data: data, encoding: .utf8) ?? "" }
            }
            return
        }
        dismiss()
        switch action {
        case "activity": model.selection = .activity
        case "storage": model.selection = .settings
        case "tasks": model.selection = .automation
        case "results": model.resultsAttention = true; model.selection = .results
        default: model.selection = .setup
        }
    }
}

struct ArchiveOverviewView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let chat: Int64
    @ViewState private var rows: [Object] = []
    @ViewState private var loading = false
    @ViewState private var loaded = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("归档范围与进度").font(.title2)
            Text(model.accountName + " · 这里只统计本机现存记录，不代表完整聊天历史。").foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if !loaded { Text(loading ? "正在读取本地范围…" : "本地范围尚未读取，失败时可刷新。") }
                    else if rows.isEmpty { Text("暂无会话。先选择要归档的私聊或群组；未选择的会话不会保存消息。") }
                    ForEach(rows, id: \.chatID) { row in
                        Panel(title: row.string("title"), icon: "archivebox") {
                            Text(row.string("status"))
                            Text("本地现存 \(Int(row.number("count"))) 条 · 最早：\(dateText(row.number("earliest")))\n最晚：\(dateText(row.number("latest")))")
                            Text("起始依据：\(row.string("basis"))\n记录的起点：\(dateText(row.number("started")))").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Text("从最近300条、仅新消息或指定日期开始的归档各有边界。保留期、主动清理、停止归档和平台删除都会影响本地范围；同步结束也不表示已恢复平台全部历史。").font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack { Button("关闭") { dismiss() }; Spacer(); Button("刷新本地范围") { load() }.disabled(loading) }
        }.padding(26).frame(width: 650, height: 530).onAppear { load() }
    }
    func load() {
        guard !loading else { return }; loading = true
        model.request("archive_overview") { value in
            loading = false
            guard let value else { return }
            let next = value.rows("rows").filter { chat == 0 || $0.chatID == chat }
            if !sameJSON(rows, next) { rows = next }; loaded = true
        }
    }
}

struct BackupHealthView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        Panel(title: "备份与恢复检查", icon: "externaldrive.badge.checkmark") {
            let status = model.state.object("backup_status")
            Text(status.number("created") > 0 ? "上次成功创建：\(dateText(status.number("created"))) · \(status.bool("encrypted") ? "加密" : "明文") · \(Int(status.number("messages")))条消息" : "尚无此账号的成功备份记录。")
            if status.number("created") == 0 || Date().timeIntervalSince1970 - status.number("created") > 7 * 86400 {
                Text("建议保存一份近期加密备份，并校验文件；备份记录本身不能替代备份文件。").font(.caption).foregroundStyle(.secondary)
            }
            Text("最近所选文件校验：\(dateText(status.number("verified")))。历史校验不保证文件后来仍存在或能完成整机恢复。").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("创建加密备份…") { model.backupPasswordForRestore = false; model.showBackupPassword = true }.disabled(model.busy)
                Button("仅校验备份，不恢复…") { model.chooseBackup(verifyOnly: true) }.disabled(model.busy)
                Button("恢复检查清单") { model.showHelp = true }
            }
        }
    }
}

struct HelpRecoveryView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("使用帮助与恢复检查").font(.title2)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Panel(title: "账号与任务", icon: "person.crop.circle.badge.checkmark") {
                        Text("先核对当前账号、连接和暂停状态，再在准备与恢复中选择原任务。重启前的旧提示不补发；发送结果未知时先查原会话，不重复发送。")
                        Button("打开准备与恢复") { model.selection = .setup; dismiss() }
                    }
                    Panel(title: "为什么没记录或没回复", icon: "questionmark.circle") {
                        Text("归档只保存已选择会话及配置起点后的可访问内容。任务页显示本次启动后的最近检查原因；未匹配提示不保存正文，可自行粘贴样例复核。")
                        HStack { Button("查看归档") { model.selection = .archive; dismiss() }; Button("查看任务") { model.selection = .automation; dismiss() } }
                    }
                    Panel(title: "备份和恢复顺序", icon: "externaldrive") {
                        Text("1. 创建并保管近期备份。\n2. 在设置中仅校验文件和所属账号；这一步不恢复、不关闭任务。\n3. 确实需要恢复时，另行预览并确认；恢复后核对身份和任务。\n业务备份不含登录会话、API或代理密码；完整升级回退副本含登录会话，仅可留在受保护的本机。加密密码丢失无法恢复。")
                        Button("打开设置与备份") { model.selection = .settings; dismiss() }
                    }
                    Panel(title: "排查与反馈", icon: "stethoscope") {
                        Text("断网、睡眠或退出期间不能实时工作。通知已提交不代表横幅一定展示，可检查通知中心与专注模式。设置提供匿名诊断预览，分享前仍请自行核对。")
                        Button("查看活动与异常") { model.selection = .activity; dismiss() }
                        Link("公开问题反馈（仅无私人资料的复现）", destination: URL(string: "https://github.com/Aronlele/communications-assistant/issues")!)
                        Text("请勿公开发送消息正文、账号资料、手机号、验证码、密码、登录会话或API配置。实验在线策略位于高级设置，默认关闭。当前测试包尚未完成正式API及签名公证。").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            HStack {
                Button("关闭") { dismiss() }; Spacer()
                Button("复制版本信息") {
                    let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "未知"
                    let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "未知"
                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString("即讯 \(version)（构建\(build)） · macOS \(ProcessInfo.processInfo.operatingSystemVersionString)", forType: .string)
                }
            }
        }.padding(26).frame(width: 700, height: 620)
    }
}
