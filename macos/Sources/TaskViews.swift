import SwiftUI
import AppKit
import UniformTypeIdentifiers

private let checkinSample = "🔔 查岗提醒（编号 00176）\n\n嗨，天枢 👋\n\n查岗小助手来啦～\n\n请在20分钟内只回复下面的关键词：\n\n香蕉\n\n回复时限说明：\n20分钟内回复：正常响应。\n20-30分钟内回复：超时回复。\n超过30分钟未正确回复：未响应。\n\n祝工作顺利 🌷"

func taskStatus(_ value: String) -> String {
    ["sending": "发送中", "sent": "已发送，等待回执", "sent_unverified": "已发送，登记未核实", "confirmed": "回执已确认", "send_unknown": "发送结果待核对", "expired": "已过期", "skipped": "已跳过", "reminder": "仅提醒"][value] ?? value
}

func taskReference(_ value: String) -> String {
    value.hasPrefix("message:") ? "消息 \(value.dropFirst(8))" : "编号 \(value)"
}

struct TaskEditorItem: Identifiable {
    let id = UUID()
    let value: Object
}

struct AutomationView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var editor: TaskEditorItem?
    @ViewState private var confirming: Object = [:]
    @ViewState private var enable = false
    @ViewState private var deleting: Object = [:]
    @ViewState private var showDelete = false
    @ViewState private var preview: Object = [:]
    @ViewState private var previewID = ""
    @ViewState private var templates: [Object] = []
    @ViewState private var showWizard = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageTitle(title: "任务管理", subtitle: "\(model.accountName) · 每个任务独立设置对象、模板、回复和时段。")
                HStack {
                    Button("新建任务", systemImage: "plus") { showWizard = true }.buttonStyle(.borderedProminent)
                    Menu("从模板新建") {
                        ForEach(Array(templates.enumerated()), id: \.offset) { item in
                            Button(item.element.string("name")) { editor = TaskEditorItem(value: item.element) }
                        }
                    }
                    Button("导入任务模板…") { importTemplate() }
                    Button("查看任务结果") { model.selection = .results }
                    Spacer()
                    Button(model.settings.bool("pause_sends") ? "恢复新发送" : "暂停新发送") { model.control(model.settings.bool("pause_sends") ? "resume_sends" : "pause_sends") }
                }.disabled(model.busy)
                if model.demo { Text("演示数据在独立目录，禁止向 Telegram 发送消息。").foregroundStyle(.orange) }
                if model.settings.bool("pause_all") || model.settings.bool("pause_sends") {
                    Label("当前账号的新发送已暂停；开启状态的任务也不会发送。", systemImage: "pause.circle").foregroundStyle(.orange)
                }
                if model.taskRules.isEmpty {
                    Panel(title: "创建第一个任务", icon: "bolt") {
                        Text("可以从查岗提示提取每次变化的关键词，也可以匹配指定提示、回复固定内容。保存和预览均不会发送消息。")
                        Button("配置回复任务") { showWizard = true }
                    }
                }
                ForEach(model.taskRules, id: \.taskID) { rule in
                    taskCard(rule)
                }
                Text("同一来源消息只提交一次。多个已开启任务同时匹配时，优先级较高者先执行；相同优先级按列表顺序。仅提醒的任务不遮挡已开启任务。删除任务保留发送账本及后续回执核对。").font(.caption).foregroundStyle(.secondary)
                if model.settings.object("rule").number("target") > 0 {
                    DisclosureGroup("原有任务的旧发送账本导入") {
                        Text("此入口仅用于第一条原有任务，合并前请核对账号和目标。其他任务及已有账本保留。").font(.caption)
                        Button("预览并导入旧发送账本") { model.chooseLegacyLedger() }.disabled(model.busy)
                    }
                }
            }.padding(28)
        }
        .onAppear { model.request("task_templates") { templates = $0?.rows("rows") ?? [] } }
        .sheet(isPresented: $showWizard) { TaskWizardView() }
        .sheet(item: $editor) { item in TaskEditorView(initial: item.value) }
        .alert("确认开启任务", isPresented: $enable, presenting: confirming) { rule in
            Button("取消", role: .cancel) { }
            Button("开启此任务") {
                model.request("task_toggle", ["id": rule.string("id"), "automatic": true,
                                              "confirmed_target": Int64(rule.number("target")), "revision": rule.string("revision")])
            }
        } message: { rule in
            Text("账号：\(model.accountName)\n任务：\(rule.string("name"))\n对象：\(rule.string("title"))（\(String(Int64(rule.number("target"))))）\n\(rule.string("mode") == "fixed" ? "匹配后固定回复：" + rule.string("reply_text") : "提取新提示中的精确关键词")\n只处理启用后的有效新消息；发送结果不明时不重试。")
        }
        .alert("删除任务", isPresented: $showDelete, presenting: deleting) { rule in
            Button("取消", role: .cancel) { }
            Button("删除", role: .destructive) { model.request("task_delete", ["id": rule.string("id"), "confirmed_id": rule.string("id")]) }
        } message: { rule in Text("将移除“\(rule.string("name"))”。其他任务继续运行；已经提交的发送可能仍会完成，历史记录和去重账本保留。") }
        .alert("合并原有发送账本", isPresented: $model.showLedger) {
            Button("取消", role: .cancel) { }
            Button("确认归属并合并") { model.importLegacyLedger() }
        } message: { Text("确认此文件属于当前账号 \(model.accountName) 和原有任务目标 ID \(Int64(model.ledgerPreview.number("target")))；合并 \(Int(model.ledgerPreview.number("count"))) 条记录，原有任务恢复仅提醒，其他任务不变。") }
    }
    func taskCard(_ rule: Object) -> some View {
        Panel(title: rule.string("name"), icon: "bolt.shield") {
            HStack {
                Text("\(rule.string("title")) · ID \(String(Int64(rule.number("target"))))").textSelection(.enabled)
                Spacer()
                Text(rule.bool("automatic") ? "已开启" : rule.bool("resume_pending") ? "待恢复确认" : "仅提醒")
                    .foregroundStyle(rule.bool("automatic") ? Color.accentColor : .secondary)
            }
            Text(rule.string("mode") == "fixed" ? "\(rule.string("match_mode") == "exact" ? "全文相同" : "包含文本") → 回复“\(rule.string("reply_text"))” · 有效期 \(Int(rule.number("valid_minutes"))) 分钟" : rule.string("prompt_template").isEmpty ? "内置查岗模板 → 提取编号、关键词和时限" : "自定义提取模板 → 提取编号、关键词和时限").font(.callout)
            Text("优先级 \(Int(rule.number("priority"))) · \(rule.bool("sending_limited") ? "按指定时段发送" : "不限发送时段")").font(.caption).foregroundStyle(.secondary)
            if let conflicts = rule["conflicts"] as? [String], !conflicts.isEmpty {
                Label("同一对象还有：" + conflicts.joined(separator: "、") + "。如同时匹配，只执行优先任务。", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
            }
            HStack {
                Button("编辑任务") { editor = TaskEditorItem(value: rule) }
                Menu("更多") {
                    Button("复制为关闭的副本") { model.request("task_duplicate", ["id": rule.string("id")]) { if $0 != nil { model.info = "任务副本已创建，未开启发送；原任务保持运行" } } }
                    Button("导出任务模板…") { exportTemplate(rule) }
                    Button("验证全部已保存样例") {
                        previewID = rule.string("id")
                        model.request("task_cases_preview", ["id": rule.string("id")]) { preview = $0 ?? [:] }
                    }.disabled(rule.rows("sample_cases").isEmpty)
                }
                Button("验证已保存样例") {
                    previewID = rule.string("id")
                    model.request("task_preview", ["id": rule.string("id"), "text": rule.string("sample_text")]) { preview = $0 ?? [:] }
                }.disabled(rule.string("sample_text").isEmpty)
                Button(rule.bool("automatic") ? "关闭任务" : "开启任务…") {
                    if rule.bool("automatic") { model.request("task_toggle", ["id": rule.string("id"), "automatic": false]) }
                    else { confirming = rule; enable = true }
                }.disabled(!rule.bool("automatic") && (!rule.bool("preview_validated") || model.demo || !model.state.bool("connected")))
                Spacer()
                Button("删除…", role: .destructive) { deleting = rule; showDelete = true }
            }.disabled(model.busy)
            if !rule.bool("preview_validated") { Text("开启前请编辑任务，保存并成功预览一条真实格式的样例。").font(.caption).foregroundStyle(.secondary) }
            if previewID == rule.string("id"), !preview.isEmpty {
                if preview["rows"] != nil { SampleSuiteResult(value: preview) } else { PreviewResult(value: preview) }
            }
            DisclosureGroup("手动连接测试") { TaskSendTestView(rule: rule) }
        }
    }
    func exportTemplate(_ rule: Object) {
        model.request("task_template_export", ["id": rule.string("id")]) { value in
            guard let value else { return }
            let notice = NSAlert(); notice.messageText = "导出当前任务模板"
            notice.informativeText = "包含匹配文本、回复、时段和测试样例，可能包含个人信息，请核对后分享。不包含登录凭据、目标账号或发送授权。"
            notice.addButton(withTitle: "选择保存位置"); notice.addButton(withTitle: "取消")
            guard notice.runModal() == .alertFirstButtonReturn else { return }
            let panel = NSSavePanel(); panel.allowedContentTypes = [.json]; panel.nameFieldStringValue = "即讯任务模板.json"
            guard panel.runModal() == .OK, let url = panel.url else { return }
            do { try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic); model.info = "任务模板已导出；接收方仍需绑定对象并确认开启" }
            catch { model.error = "任务模板保存失败，请检查目标目录" }
        }
    }
    func importTemplate() {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.json]; panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size <= 512 * 1024 else { model.error = "任务模板不能超过 512 KB"; return }
            let value = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? Object ?? [:]
            model.request("task_template_import", ["document": value]) { result in
                if let result { editor = TaskEditorItem(value: result); model.info = "模板已读入草稿，请重新选择当前账号的对象；尚未保存或开启" }
            }
        } catch { model.error = "无法读取 JSON 任务模板" }
    }

}

struct PreviewResult: View {
    let value: Object
    var body: some View {
        Text(value.bool("recognized") ? "匹配成功 · \(taskReference(value.string("number"))) · 将回复“\(value.string("keyword"))” · \(Int(value.number("window_minutes"))) 分钟内有效\(value.string("reply_mode") == "fixed" ? " · 登记未核实" : "")" : "未匹配，不会按此样例自动回复。\(value.string("explanation"))")
            .textSelection(.enabled).foregroundStyle(value.bool("recognized") ? Color.accentColor : .orange)
    }
}

struct TaskEditorView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let initial: Object
    @ViewState private var name = "回复任务"
    @ViewState private var target = ""
    @ViewState private var mode = "keyword"
    @ViewState private var matchMode = "exact"
    @ViewState private var matchText = ""
    @ViewState private var reply = "1"
    @ViewState private var minutes = 5
    @ViewState private var custom = false
    @ViewState private var template = ""
    @ViewState private var sample = checkinSample
    @ViewState private var allowUser = false
    @ViewState private var priority = 0
    @ViewState private var limited = false
    @ViewState private var zone = "Asia/Shanghai"
    @ViewState private var weekly = Array(repeating: "", count: 7)
    @ViewState private var preview: Object = [:]
    @ViewState private var previewFingerprint = ""
    @ViewState private var cases: [TaskSampleCase] = []
    @ViewState private var suite: Object = [:]
    @ViewState private var suiteFingerprint = ""
    var draft: Object { ["mode": mode, "match_mode": matchMode, "match_text": matchText, "reply_text": reply,
                        "valid_minutes": minutes, "prompt_template": custom ? template : "", "sample_text": sample, "sample_cases": cases.map { $0.object }] }
    var fingerprint: String { (try? String(data: JSONSerialization.data(withJSONObject: draft, options: [.sortedKeys]), encoding: .utf8)) ?? "" }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(initial.string("id") == "new" ? "新建回复任务" : "编辑回复任务").font(.title2)
            Text("账号：\(model.accountName)。修改回复条件后只暂停此任务；其他任务继续运行。").font(.caption).foregroundStyle(.secondary)
            if !model.error.isEmpty { Label(model.error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).textSelection(.enabled) }
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    TextField("任务名称", text: $name).textFieldStyle(.roundedBorder)
                    TextField("机器人或联系人 @用户名、数字 ID", text: $target).textFieldStyle(.roundedBorder)
                    if model.demo { Text("本地演示对象：1003（查岗机器人）或 1002（同事消息）。只能保存和预览，不能开启真实发送。").font(.caption).foregroundStyle(.secondary) }
                    Toggle("允许选择普通联系人（请核对固定身份）", isOn: $allowUser)
                    Picker("回复方式", selection: $mode) {
                        Text("从提示提取关键词").tag("keyword")
                        Text("匹配提示，固定回复").tag("fixed")
                    }
                    if mode == "keyword" {
                        Toggle("自定义提取模板", isOn: $custom)
                        if custom {
                            Text("提取模板（会保存并用于匹配）").font(.headline)
                            TextEditor(text: $template).font(.body.monospaced()).frame(height: 160).border(.quaternary)
                            Text("各保留一个 {number}、{minutes}、{keyword}，关键词占位符单独一行；可用一个 {name} 匹配变化的称呼。其余文字、空行和符号须与消息一致。编号保留开头的 0。").font(.caption).foregroundStyle(.secondary)
                        } else { Text("内置规则允许每条提示中的编号、称呼、关键词和时限变化，无需为“香蕉”单独保存固定回复。").font(.caption).foregroundStyle(.secondary) }
                    } else {
                        Picker("匹配方式", selection: $matchMode) { Text("全文相同").tag("exact"); Text("包含指定文本").tag("contains") }
                        TextEditor(text: $matchText).font(.body.monospaced()).frame(height: 100).border(.quaternary)
                        TextField("固定回复，例如 1", text: $reply).textFieldStyle(.roundedBorder)
                        Stepper("本地有效期：\(minutes) 分钟", value: $minutes, in: 1...1440)
                        Text("没有可靠关联的回执时，只确认发送，不能认定对方已登记。").font(.caption).foregroundStyle(.secondary)
                    }
                    Divider()
                    Text("测试样例（会保存，供下次编辑和预览）").font(.headline)
                    TextEditor(text: $sample).font(.body.monospaced()).frame(height: 170).border(.quaternary)
                    HStack {
                        if mode == "keyword" {
                            Button("从查岗样例生成提取模板") {
                                model.request("task_template_from_sample", ["text": sample]) { result in
                                    if let result { template = result.string("prompt_template"); custom = true; preview = [:] }
                                }
                            }
                        } else { Button("将样例用作匹配文本") { matchText = sample } }
                        Button("预览当前草稿，不发送") {
                            let tested = fingerprint
                            model.request("task_draft_preview", ["draft": draft, "text": sample]) { result in
                                preview = result ?? [:]; previewFingerprint = tested
                            }
                        }
                    }
                    if !preview.isEmpty {
                        if previewFingerprint == fingerprint { PreviewResult(value: preview) }
                        else { Text("草稿已变化，请重新预览。").foregroundStyle(.orange) }
                    }
                    Divider()
                    TaskSampleCasesView(cases: $cases)
                    if !cases.isEmpty {
                        Button("验证全部草稿样例，不发送") {
                            let tested = fingerprint
                            model.request("task_cases_preview", ["draft": draft, "cases": cases.map { $0.object }]) { value in suite = value ?? [:]; suiteFingerprint = tested }
                        }
                        if !suite.isEmpty {
                            if suiteFingerprint == fingerprint { SampleSuiteResult(value: suite) }
                            else { Text("草稿或样例已变化，请重新验证全部样例。 ").foregroundStyle(.orange) }
                        }
                    }
                    Divider()
                    Stepper("优先级：\(priority)（数字越大越优先）", value: $priority, in: 0...100)
                    Toggle("限制允许发送的时段", isOn: $limited)
                    if limited {
                        TextField("IANA 时区", text: $zone).textFieldStyle(.roundedBorder)
                        ForEach(0..<7, id: \.self) { day in
                            HStack { Text(["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day]); TextField("09:00-18:00，多段用逗号分隔", text: $weekly[day]).textFieldStyle(.roundedBorder) }
                        }
                        Text("空白日期禁止发送，支持跨午夜；时区和系统时区分别保存。").font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(.trailing, 8)
            }
            HStack {
                Button("取消", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("保存任务并验证样例") { save() }.buttonStyle(.borderedProminent).disabled(model.busy)
            }
            Text("保存不会发送消息，也不会自动开启新的或已修改的任务。没有样例也可先保存，稍后补充验证。").font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(width: 720, height: 720)
        .onAppear { load() }
    }
    func load() {
        guard initial.count > 1 else { return }
        name = initial.string("name"); target = initial.number("target") > 0 ? String(Int64(initial.number("target"))) : ""
        mode = initial.string("mode"); matchMode = initial.string("match_mode"); matchText = initial.string("match_text")
        reply = initial.string("reply_text"); minutes = Int(initial.number("valid_minutes"))
        template = initial.string("prompt_template"); custom = !template.isEmpty
        cases = initial.rows("sample_cases").map { TaskSampleCase(text: $0.string("text"), shouldMatch: $0.bool("should_match"), expectedReply: $0.string("expected_reply")) }
        sample = initial.string("sample_text"); allowUser = initial.bool("allow_user_test")
        priority = Int(initial.number("priority")); limited = initial.bool("sending_limited"); zone = initial.string("sending_timezone")
        if let values = initial["sending_weekly"] as? [[[String]]], values.count == 7 {
            weekly = values.map { $0.map { $0.joined(separator: "-") }.joined(separator: ", ") }
        }
    }
    func save() {
        guard let times = parseWeekly(weekly) else { model.error = "时间段请使用 09:00-18:00 格式"; return }
        var args = draft
        args.merge(["id": initial.string("id"), "name": name, "target": target, "allow_user_test": allowUser,
                    "priority": priority, "sending_limited": limited, "sending_weekly": times, "sending_timezone": zone,
                    "base_revision": initial.string("revision")]) { _, new in new }
        let savedSample = sample
        model.request("task_save", args) { result in
            guard let result else { return }
            let identifier = result.string("saved_task_id")
            if !cases.isEmpty {
                model.request("task_cases_preview", ["id": identifier]) { tested in
                    guard let tested else { return }
                    model.info = tested.bool("passed") ? "任务和全部样例已保存并通过；请核对后开启" : "任务已保存，部分样例未通过，需修正后才可重新开启"
                    dismiss()
                }
            }
            else if savedSample.isEmpty { model.info = "任务已保存；补充并验证样例后可开启"; dismiss() }
            else {
                model.request("task_preview", ["id": identifier, "text": savedSample]) { tested in
                    guard let tested else { return }
                    model.info = tested.bool("recognized") ? "任务和样例已保存，预览通过；请核对后开启此任务" : "任务已保存，样例未匹配；请编辑模板后重新验证"
                    dismiss()
                }
            }
        }
    }
}

struct TaskSendTestView: View {
    @EnvironmentObject var model: AppModel
    let rule: Object
    @ViewState private var text = "即讯连接测试"
    @ViewState private var show = false
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("单行测试消息，最多 100 字符", text: $text).textFieldStyle(.roundedBorder)
            Button("发送测试…") { show = true }.disabled(model.demo || model.busy || !model.state.bool("connected"))
            Text("此操作向所选对象发送真实消息，不代表任务登记成功。").font(.caption).foregroundStyle(.secondary)
        }.alert("确认发送测试消息", isPresented: $show) {
            Button("取消", role: .cancel) { }
            Button("发送一次") { model.request("send_test", ["id": rule.string("id"), "text": text, "confirmed_target": Int64(rule.number("target"))]) }
        } message: { Text("账号：\(model.accountName)\n对象：\(rule.string("title"))（ID \(String(Int64(rule.number("target"))))）\n内容：\(text)") }
    }
}

struct TaskResultsView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var task = ""
    @ViewState private var status = ""
    @ViewState private var rows: [Object] = []
    @ViewState private var count = 0
    @ViewState private var offset = 0
    @ViewState private var generation = 0
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Button("处理异常") { model.showIssues = true }
                PageTitle(title: "任务结果", subtitle: "\(model.accountName) · 发送动作和对方确认分别记录，结果不明时不自动重试。")
                HStack {
                    Picker("任务", selection: $task) {
                        Text("所有任务（含已删除）").tag("")
                        ForEach(model.taskRules, id: \.taskID) { rule in Text(rule.string("name")).tag(rule.string("id")) }
                    }
                    Picker("结果", selection: $status) {
                        Text("全部结果").tag(""); Text("需要关注").tag("attention")
                        ForEach(["sent", "sent_unverified", "confirmed", "send_unknown", "sending", "expired", "skipped", "reminder"], id: \.self) { value in Text(taskStatus(value)).tag(value) }
                    }
                    Button("刷新") { load() }
                }
                Text("共 \(count) 条 · 当前 \(rows.isEmpty ? 0 : offset + 1)–\(min(offset + rows.count, count)) 条").font(.caption).foregroundStyle(.secondary)
                if rows.isEmpty { Text("此筛选下暂无任务结果。新提示被识别或发送后会出现在这里。").foregroundStyle(.secondary) }
                ForEach(rows, id: \.taskResultID) { row in
                    Panel(title: "\(row.string("task_name")) · \(taskReference(row.string("number")))", icon: row.bool("receipt_overdue") || row.string("status") == "send_unknown" ? "exclamationmark.circle" : "checklist") {
                        Text(taskStatus(row.string("status"))).font(.headline)
                        Text("收到提示：\(dateText(row.number("date")))\n最后更新：\(dateText(row.number("updated")))\n正常回复截止：\(dateText(row.number("deadline")))").font(.caption).foregroundStyle(.secondary)
                        if !row.string("keyword").isEmpty { Text("回复内容：\(row.string("keyword"))").textSelection(.enabled) }
                        if !row.string("reason").isEmpty { Text(row.string("reason")).foregroundStyle(.secondary) }
                        if row.bool("receipt_overdue") { Text("正常回复期限结束 5 分钟后仍无同编号回执，请到来源会话核对。").foregroundStyle(.orange) }
                        if row.string("status") == "send_unknown" { Text("平台可能已收到请求，不能据此再次发送；请核对来源会话。").foregroundStyle(.orange) }
                        if row.string("status") == "sent_unverified" { Text("固定回复已提交成功；未配置可关联回执，登记结果未核实。").font(.caption).foregroundStyle(.secondary) }
                        HStack {
                            Button("查看原消息") { model.showTaskMessage(row) }
                            Button("打开 Telegram 会话") { model.openTelegramChat(Int64(row.number("target"))) }
                            Button("复制回复内容") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(row.string("keyword"), forType: .string) }.disabled(row.string("keyword").isEmpty)
                        }
                    }
                }
                HStack {
                    Button("上一页") { offset = max(0, offset - 50); load() }.disabled(offset == 0)
                    Spacer()
                    Button("下一页") { offset += 50; load() }.disabled(offset + rows.count >= count)
                }
            }.padding(28)
        }
        .onAppear { if model.resultsAttention { status = "attention"; model.resultsAttention = false }; load() }
        .onChange(of: model.resultsAttention) { _, value in if value { status = "attention"; offset = 0; model.resultsAttention = false; load() } }
        .onChange(of: task) { offset = 0; load() }
        .onChange(of: status) { offset = 0; load() }
        .onReceive(model.$state) { _ in if model.ready { load() } }
    }
    func load() {
        generation += 1; let current = generation
        model.request("task_results", ["task_id": task, "status": status, "offset": offset], background: true) { value in
            guard let value, generation == current else { return }
            rows = value.rows("rows"); count = Int(value.number("count"))
        }
    }
}

struct AccountsOverviewView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageTitle(title: "所有账号", subtitle: "\(model.accounts.filter { $0.bool("connected") }.count) 个账号已连接 · \(model.accountAttentionCount) 个账号需要关注。")
                Text("账号切换只改变当前查看内容，其他账号继续运行。未登录的空入口不计为连接故障。").font(.caption).foregroundStyle(.secondary)
                ForEach(model.accounts, id: \.slotID) { account in
                    Panel(title: account.object("identity").string("name").isEmpty ? "待登录账号" : account.object("identity").string("name"), icon: "person.crop.circle") {
                        HStack(alignment: .top, spacing: 16) {
                            AccountAvatar(identity: account.object("identity"), root: model.dataRoot(for: account.string("slot")), size: 48)
                            VStack(alignment: .leading, spacing: 6) {
                                Text(accountStatus(account)).font(.headline)
                                Text("任务 \(Int(account.number("enabled_tasks"))) / \(Int(account.number("task_count"))) 已开启 · \(Int(account.number("attention_count"))) 条结果需要关注")
                                Text("最近归档同步：\(dateText(account.number("last_sync")))").font(.caption).foregroundStyle(.secondary)
                                if account.number("resume_count") > 0 { Text("\(Int(account.number("resume_count"))) 个任务待恢复确认").foregroundStyle(.orange) }
                                if account.bool("pause_all") || account.bool("pause_sends") { Text("新发送已暂停，请切换到此账号核对暂停状态").foregroundStyle(.orange) }
                                if account.bool("quota_paused") { Text("本地归档或写入受阻，请检查数据管理").foregroundStyle(.orange) }
                                if account.bool("error_present") { Text("存在运行异常，请打开此账号查看说明").foregroundStyle(.orange) }
                                if account.number("cooldown") > Date().timeIntervalSince1970 { Text("平台要求等待至 \(dateText(account.number("cooldown")))").foregroundStyle(.orange) }
                            }
                        }
                        HStack {
                            Button("打开此账号") { open(account, .overview) }
                            Button("任务管理") { open(account, .automation) }
                            Button("查看结果") { open(account, .results) }
                            Button("处理异常") { model.selectAccount(account.string("slot")); model.showIssues = true }
                            Button("准备与恢复") { open(account, .setup) }
                        }.disabled(model.busy)
                    }
                }
                Button("添加 Telegram 账号…") { model.addAccount() }.disabled(model.busy || model.demo)
            }.padding(28)
        }
    }
    func open(_ account: Object, _ destination: Destination) {
        model.selectAccount(account.string("slot")); model.selection = destination
    }
}

struct SetupRecoveryView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var selected = Set<String>()
    @ViewState private var confirm = false
    @ViewState private var resumeRules: [Object] = []
    var body: some View {
        if !model.ready { LoginView() }
        else {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    PageTitle(title: "准备与恢复", subtitle: "逐项确认 \(model.accountName) 的使用准备，已有账号和配置可以继续使用。")
                    Panel(title: "1 · 核对账号与连接", icon: "person.crop.circle.badge.checkmark") {
                        Text("\(model.accountName) · \(model.connection)")
                        Text("关闭窗口后继续在菜单栏运行；退出应用后停止。Mac 睡眠或断网期间无法实时处理任务。").font(.caption).foregroundStyle(.secondary)
                        if model.state.string("auth") == "offline" { Button("重新连接此账号") { model.connect() } }
                        Button("查看所有账号") { model.selection = .accounts }
                    }
                    Panel(title: "2 · 检查消息提醒", icon: "bell") {
                        Text(model.notificationStatus)
                        HStack {
                            Button("发送本地测试通知") { model.testNotification() }
                            Button("刷新权限状态") { model.updateNotificationStatus() }
                            Button("打开系统通知设置") { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension")!) }
                        }
                        Button("设置提醒与静音时段") { model.selection = .reminders }
                        Text("拒绝通知权限仍可使用归档和任务；没有横幅时也可查看通知中心。").font(.caption).foregroundStyle(.secondary)
                    }
                    Panel(title: "3 · 选择需要归档的会话", icon: "archivebox") {
                        Text("当前选择 \(model.conversations.filter { $0.bool("selected") }.count) 个会话。未选择的会话不会开始本地归档。")
                        Button("选择会话或暂不归档") { model.selection = .archive }
                    }
                    Panel(title: "4 · 核对并恢复任务", icon: "bolt.shield") {
                        Text("共 \(model.taskRules.count) 个任务，\(model.enabledTaskCount) 个已开启；\(model.pendingRecovery.count) 个等待恢复确认。")
                        if model.pendingRecovery.isEmpty { Text("没有待恢复任务。新任务保存并验证后，可在任务管理中单独开启。").foregroundStyle(.secondary) }
                        ForEach(model.pendingRecovery, id: \.taskID) { rule in
                            Toggle(isOn: Binding(get: { selected.contains(rule.string("id")) }, set: { value in
                                if value { selected.insert(rule.string("id")) } else { selected.remove(rule.string("id")) }
                            })) {
                                VStack(alignment: .leading) {
                                    Text(rule.string("name"))
                                    Text("\(rule.string("title"))（ID \(String(Int64(rule.number("target"))))） · \(rule.string("mode") == "fixed" ? "固定回复：" + rule.string("reply_text") : "提取每条新提示中的关键词")").font(.caption).foregroundStyle(.secondary)
                                }
                            }.disabled(!rule.bool("preview_validated"))
                        }
                        HStack {
                            Button("管理与预览任务") { model.selection = .automation }
                            if !model.pendingRecovery.isEmpty {
                                Button("恢复选中的任务…") {
                                    resumeRules = model.pendingRecovery.filter { selected.contains($0.string("id")) }; confirm = true
                                }.disabled(selected.isEmpty || model.demo || model.busy || !model.state.bool("connected"))
                            }
                        }
                        if model.settings.bool("pause_sends") || model.settings.bool("pause_all") { Text("账号仍处于暂停状态；恢复任务不会解除账号暂停，请核对后单独恢复。").foregroundStyle(.orange) }
                        Text("恢复只作用于选中的原有任务；手动关闭的任务保持关闭，不补发旧提示。规则编辑后需重新验证和单独开启。").font(.caption).foregroundStyle(.secondary)
                    }
                    Panel(title: "5 · 后台运行与完成", icon: "menubar.rectangle") {
                        Toggle("登录 Mac 时打开即讯", isOn: Binding(get: { model.launchAtLogin }, set: { model.setLoginItem($0) }))
                        Text("账号和归档保存在本机，退出账号保留本地资料；备份不包含 Telegram 登录授权或 API 凭据。").font(.caption).foregroundStyle(.secondary)
                        Button("完成准备，查看概览") { model.request("setup_complete") { result in if result != nil { model.selection = .overview } } }.buttonStyle(.borderedProminent)
                        Text("可以暂不启用归档或自动回复，稍后随时回到此向导。").font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(28)
            }
            .onAppear { model.updateNotificationStatus() }
            .alert("确认恢复任务", isPresented: $confirm, presenting: resumeRules) { selectedRules in
                Button("取消", role: .cancel) { }
                Button("确认恢复 \(selectedRules.count) 个任务") {
                    var revisions: Object = [:]
                    for rule in selectedRules { revisions[rule.string("id")] = rule.string("revision") }
                    model.request("task_resume", ["ids": selectedRules.map { $0.string("id") }, "revisions": revisions,
                                                  "confirmed_account": Int64(model.state.number("account"))]) { result in if result != nil { selected.removeAll(); model.info = "选中任务已恢复，开始处理有效新提示" } }
                }
            } message: { selectedRules in Text("账号：\(model.accountName)\n" + selectedRules.map { "\($0.string("name")) → \($0.string("title"))（\(String(Int64($0.number("target"))))）" }.joined(separator: "\n") + "\n只恢复所选任务；保持原有时段和账号暂停设置。") }
        }
    }
}

extension Dictionary where Key == String, Value == Any {
    var taskID: String { string("id") }
    var taskResultID: String { "\(Int64(number("target"))):\(string("number")):\(Int64(number("message_id")))" }
}
