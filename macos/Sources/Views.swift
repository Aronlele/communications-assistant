import SwiftUI
import AppKit

// 显式引用状态包装器，兼容只有 Command Line Tools 的构建环境。
typealias ViewState<Value> = SwiftUI.State<Value>

func dateText(_ timestamp: Double) -> String {
    guard timestamp > 0 else { return "暂无" }
    return Date(timeIntervalSince1970: timestamp).formatted(date: .abbreviated, time: .standard)
}

struct Panel<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(title, systemImage: icon).font(.headline)
            content
        }
        .padding(20).frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(.quaternary))
    }
}

struct PageTitle: View {
    let title: String
    let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.largeTitle.weight(.semibold))
            Text(subtitle).foregroundStyle(.secondary)
        }.padding(.bottom, 8)
    }
}

struct AccountAvatar: View {
    let identity: Object
    let root: URL
    var size: CGFloat = 36
    var body: some View {
        let relative = identity.string("avatar")
        let path = root.appendingPathComponent(relative).standardizedFileURL
        Group {
            if !relative.isEmpty, !relative.contains(".."), path.path.hasPrefix(root.standardizedFileURL.path + "/"), let image = NSImage(contentsOf: path) {
                Image(nsImage: image).resizable().scaledToFill()
            } else {
                ZStack {
                    Circle().fill(Color.accentColor.opacity(0.15))
                    Text(String(identity.string("name").prefix(1))).font(.system(size: size * 0.45, weight: .semibold)).foregroundStyle(.tint)
                    if identity.string("name").isEmpty { Image(systemName: "person.fill").foregroundStyle(.tint) }
                }
            }
        }.frame(width: size, height: size).clipShape(Circle()).accessibilityLabel("账号头像")
    }
}

struct AccountMenu: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var showRemove = false
    @ViewState private var removalSlot = ""
    var body: some View {
        HStack(spacing: 10) {
            AccountAvatar(identity: model.identity, root: model.root)
            VStack(alignment: .leading, spacing: 5) {
                Menu {
                    ForEach(model.accounts, id: \.slotID) { account in
                        Button {
                            model.selectAccount(account.string("slot"))
                        } label: {
                            let name = account.object("identity").string("name")
                            Label((name.isEmpty ? "待登录账号" : name) + " · " + accountStatus(account), systemImage: account.string("slot") == model.selectedSlot ? "checkmark.circle.fill" : "person.crop.circle")
                        }
                    }
                    if !model.demo {
                        Divider()
                        Button("添加 Telegram 账号…", systemImage: "person.badge.plus") { model.addAccount() }
                        Button("移除当前账号…", role: .destructive) {
                            removalSlot = model.selectedSlot
                            showRemove = true
                        }.disabled(!model.canRemoveAccount)
                        if model.state.string("auth") != "disconnected" { Text("移除前请先在设置中退出账号或取消登录") }
                        else if model.accounts.count == 1 { Text("至少保留一个登录入口") }
                    }
                } label: {
                    Text(model.accountName).font(.headline).lineLimit(1)
                }.menuStyle(.borderlessButton).disabled(model.busy).accessibilityLabel("切换或添加账号：" + model.accountName)
                Text(model.connection).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .alert("移除当前账号", isPresented: $showRemove) {
            Button("取消", role: .cancel) { }
            Button("移除账号", role: .destructive) { model.removeAccount(removalSlot) }
        } message: {
            Text("仅从账号列表移除当前已退出的登录入口。本地归档、规则、发送账本和自定义配置保留，其他账号继续运行。重新添加账号不会自动合并旧归档，可通过原目录的本地备份恢复。")
        }
    }
}

func accountStatus(_ account: Object) -> String {
    if account.bool("error_present") { return account.bool("connected") ? "已连接 · 需检查" : "需检查连接" }
    if account.bool("connected") { return account.bool("pause_all") ? "已连接 · 已暂停" : "已连接" }
    return ["offline": "离线", "identity": "待确认", "phone": "待登录", "code": "待验证", "password": "待验证", "connecting": "连接中"][account.string("auth")] ?? "未连接"
}

extension Dictionary where Key == String, Value == Any {
    var slotID: String { string("slot") }
}

struct MainView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.openWindow) var openWindow
    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 0) {
                Label("即讯", systemImage: "paperplane.circle.fill")
                    .font(.title2.weight(.semibold)).padding(20)
                List(Destination.allCases, selection: $model.selection) { destination in
                    Label(destination.rawValue, systemImage: destination.icon).tag(destination)
                }.listStyle(.sidebar)
                VStack(alignment: .leading, spacing: 6) {
                    AccountMenu()
                    if model.accounts.count > 1 { Text("\(model.accounts.filter { $0.bool("connected") }.count) / \(model.accounts.count) 个账号已连接").font(.caption).foregroundStyle(.secondary) }
                    if model.demo { Text("所有数据均为演示示例").font(.caption).foregroundStyle(.orange) }
                }.padding(18)
            }.navigationSplitViewColumnWidth(min: 190, ideal: 220, max: 260)
        } detail: {
            VStack(spacing: 0) {
                if !model.error.isEmpty {
                    HStack {
                        Label(model.error, systemImage: "exclamationmark.triangle")
                        Spacer()
                        Button("处理异常") { model.showIssues = true }
                        if model.engineUnavailable { Button("重启消息引擎") { model.retryEngine() } }
                        Button { model.error = "" } label: { Image(systemName: "xmark") }.buttonStyle(.plain).accessibilityLabel("关闭提示")
                    }.font(.callout).padding(12).background(.orange.opacity(0.12))
                }
                if !model.info.isEmpty {
                    HStack {
                        Text(model.info).foregroundStyle(.secondary)
                        Spacer()
                        Button { model.info = "" } label: { Image(systemName: "xmark") }.buttonStyle(.plain).accessibilityLabel("关闭操作提示")
                    }.font(.caption).padding(10).background(.quaternary.opacity(0.3))
                }
                if model.selection == .accounts { AccountsOverviewView() }
                else if model.selection == .setup { SetupRecoveryView() }
                else if !model.ready && model.selection == .settings { LoginSettingsView() }
                else if !model.ready { LoginView() }
                else {
                    switch model.selection ?? .overview {
                    case .overview: OverviewView()
                    case .reminders: RemindersView()
                    case .archive: ArchiveView()
                    case .automation: AutomationView()
                    case .results: TaskResultsView()
                    case .accounts: AccountsOverviewView()
                    case .setup: SetupRecoveryView()
                    case .activity: ActivityView()
                    case .settings: SettingsView()
                    }
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity).id(model.selectedSlot)
        }
        .frame(minWidth: 960, minHeight: 640)
        .disabled(model.preparingUpgrade)
        .onAppear { model.openMain = { openWindow(id: "main") } }
        .toolbar {
            Button("处理异常", systemImage: "exclamationmark.bubble") { model.showIssues = true }
            if model.busy { ProgressView().controlSize(.small) }
            if model.ready {
                Button(model.settings.bool("pause_all") ? "恢复当前账号" : "暂停当前账号") {
                    model.control(model.settings.bool("pause_all") ? "resume_all" : "pause_all")
                }
            }
        }
        .sheet(isPresented: $model.showIssues) { IssueCenterView() }
        .sheet(isPresented: $model.showUpdates) { ProductUpdateView() }
        .sheet(isPresented: $model.showRetainedAccounts) { RetainedAccountsView() }
        .sheet(isPresented: $model.showBackupPassword) { BackupPasswordView() }
        .sheet(isPresented: $model.showDiagnostics) { DiagnosticsView() }
        .sheet(isPresented: $model.showContext) { ContextView() }
        .alert("导出聊天记录", isPresented: $model.showExport) {
            Button("取消", role: .cancel) { }
            Button("选择保存位置") { model.export() }
        } message: { Text(model.exportSummary) }
    }
}

struct LoginView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var country = "CN:86"
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Image(systemName: "paperplane.circle").font(.system(size: 56)).foregroundStyle(.tint)
                PageTitle(title: "让通讯工作更从容", subtitle: "集中管理消息提醒、聊天记录与固定规则。数据保存在这台 Mac。")
                Panel(title: "连接自己的 Telegram 账号", icon: "person.crop.circle") {
                    if model.effectiveAPI?.isTestOnly == true { Text("当前使用官方测试 API，仅供内部测试；正式产品配置仍待申请。").font(.caption).foregroundStyle(.orange) }
                    switch model.state.string("auth") {
                    case "identity":
                        AccountAvatar(identity: model.identity, root: model.root, size: 64)
                        Text(model.identity.string("name")).font(.title2)
                        Text("账号 ID：\(String(Int64(model.identity.number("id")))) · \(model.identity.string("phone"))").foregroundStyle(.secondary)
                        Text("请确认这是要使用的账号。恢复登录后，自动回复仍需手动开启。")
                        Button("确认并进入应用") { model.request("confirm_identity") }.buttonStyle(.borderedProminent).disabled(model.busy)
                        Button("取消本次登录") { model.cancelLogin() }.disabled(model.busy)
                    case "phone", "code", "password": authFields
                    default:
                        Text("登录你的 Telegram 账号，开启消息提醒与本地归档。验证码和二次验证密码不会保存。")
                            .foregroundStyle(.secondary)
                        if model.effectiveAPI == nil { Text(model.missingAPIMessage).foregroundStyle(.orange) }
                        if model.apiSource == .custom { Text("当前使用自定义连接配置").font(.caption).foregroundStyle(.secondary) }
                        Button("登录 Telegram") { model.connect() }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.busy || model.effectiveAPI == nil || ["starting", "connecting"].contains(model.state.string("auth")))
                    }
                }
                HStack {
                    Button("先体验本地演示") { model.switchMode(true) }.disabled(model.busy)
                    Spacer()
                    Button("高级设置…") { model.showAdvancedAPI = true; model.selection = .settings }
                }
                HStack(alignment: .top, spacing: 24) {
                    Label("关闭窗口后继续运行", systemImage: "menubar.rectangle")
                    Label("退出应用后停止工作", systemImage: "power")
                    Label("自动发送默认关闭", systemImage: "hand.raised")
                }.font(.caption).foregroundStyle(.secondary)
                Text("Mac 睡眠或断网期间无法实时接收消息；恢复连接后会尝试补齐所选会话的归档。")
                    .font(.caption).foregroundStyle(.secondary)
            }.frame(maxWidth: 640, alignment: .leading).padding(40).frame(maxWidth: .infinity)
        }
    }
    @ViewBuilder var authFields: some View {
        if model.state.string("auth") == "phone" {
            Picker("国家/地区区号", selection: $country) {
                ForEach(model.state.rows("countries"), id: \.countryKey) { row in Text(row.string("name")).tag(row.string("key")) }
            }
            TextField("手机号（包含国家区号）", text: $model.phone).textFieldStyle(.roundedBorder)
            Button("获取验证码") { sendCode() }.buttonStyle(.borderedProminent).disabled(model.busy)
            Text("可输入本地号码，或直接粘贴以 + 开头的完整国际号码。国家列表不可用时仍支持完整号码。").font(.caption).foregroundStyle(.secondary)
        } else if model.state.string("auth") == "code" {
            Text("请从 Telegram 官方客户端或短信中获取验证码。").foregroundStyle(.secondary)
            SecureField("验证码", text: $model.code).textFieldStyle(.roundedBorder)
            Button("验证登录") { model.loginCode() }.buttonStyle(.borderedProminent).disabled(model.busy)
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let remaining = max(0, Int(ceil(max(model.state.number("code_resend_at"), model.state.number("cooldown")) - context.date.timeIntervalSince1970)))
                Button(remaining > 0 ? "重发验证码（等待 \(remaining) 秒）" : "重发验证码") { sendCode() }.disabled(model.busy || remaining > 0)
            }
        } else {
            SecureField("Telegram 二次验证密码", text: $model.password).textFieldStyle(.roundedBorder)
            Button("完成验证") { model.loginPassword() }.buttonStyle(.borderedProminent).disabled(model.busy)
        }
        Button("取消登录") { model.cancelLogin() }.disabled(model.busy)
    }
    func sendCode() {
        if model.state.string("auth") == "code" { model.request("resend_code"); return }
        var value = model.phone.trimmingCharacters(in: .whitespacesAndNewlines)
        for character in [" ", "-", "(", ")"] { value = value.replacingOccurrences(of: character, with: "") }
        if !value.hasPrefix("+") { value = "+" + (country.split(separator: ":").last.map(String.init) ?? "86") + value }
        model.phone = value; model.request("send_code", ["phone": value])
    }
}

struct LoginSettingsView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageTitle(title: "设置", subtitle: "普通用户直接登录 Telegram，自定义连接配置仅供高级使用。")
                Panel(title: "账号", icon: "person.crop.circle") {
                    Text(model.connection).foregroundStyle(.secondary)
                    Button("返回登录") { model.selection = .overview }
                }
                DisclosureGroup("高级设置", isExpanded: $model.showAdvancedAPI) {
                    NetworkSettingsView()
                    Button("恢复保留的账号资料…") { model.showRetainedAccounts = true }
                    Button("检查更新…") { model.showUpdates = true }
                    APISettingsView().padding(.top, 12)
                    DeviceProfileView().padding(.top, 12)
                }
            }.padding(28)
        }
    }
}

struct APISettingsView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var source: TelegramAPISource = .builtIn
    @ViewState private var apiID = ""
    @ViewState private var apiHash = ""
    @ViewState private var forget = false
    var body: some View {
        Panel(title: "Telegram 连接配置", icon: "network") {
            Text("当前使用：\(model.apiSource.title)").font(.caption).foregroundStyle(.secondary)
            Text(model.builtInAPI == nil ? "产品内置配置尚未就绪" : "产品内置配置已就绪").foregroundStyle(.secondary)
            Picker("连接配置", selection: $source) {
                ForEach(TelegramAPISource.allCases) { value in Text(value.title).tag(value) }
            }
            if source == .custom {
                Text("仅在需要使用自己的开发者配置时填写。API Hash 保存在 macOS 钥匙串中。")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("API ID", text: $apiID).textFieldStyle(.roundedBorder)
                SecureField(model.customAPI == nil ? "API Hash" : "API Hash（已保存；留空保持原值）", text: $apiHash).textFieldStyle(.roundedBorder)
                Link("获取自己的 API 配置 ↗", destination: URL(string: "https://my.telegram.org/apps")!)
            }
            HStack {
                Button("保存连接配置") {
                    if model.saveAPIConfiguration(source: source, apiID: apiID, apiHash: apiHash) { apiHash = "" }
                }.disabled(!model.canChangeAPI)
                if model.hasSavedAPI { Button("清除自定义配置", role: .destructive) { forget = true }.disabled(!model.canChangeAPI) }
            }
            if model.canUseMatchingBuiltInAPI { Button("使用相同的内置配置") { model.useMatchingBuiltInAPI() } }
            if !model.canChangeAPI {
                Text(model.demo ? "演示模式不会使用或更改真实连接配置。" : "修改配置前，请先退出账号或取消正在进行的登录，并等待操作结束。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .onAppear { load() }
        .onChange(of: model.apiSource) { _, _ in load() }
        .onChange(of: model.customAPI) { _, value in if source == .custom { apiID = value.map { String($0.apiID) } ?? ""; apiHash = "" } }
        .onChange(of: source) { _, value in if value == .custom { model.loadCustomAPI() } }
        .alert("清除自定义 API 配置", isPresented: $forget) {
            Button("取消", role: .cancel) { }
            Button("清除", role: .destructive) { model.forgetAPI(); load() }
        } message: { Text("删除保存的自定义 API ID 和钥匙串中的 API Hash，恢复产品内置配置。已有归档保留。") }
    }
    func load() {
        source = model.apiSource
        apiID = model.customAPI.map { String($0.apiID) } ?? ""
        apiHash = ""
    }
}

struct OverviewView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageTitle(title: "概览", subtitle: "\(model.accountName)，你的通讯工作状态都在这里。")
                if !model.settings.bool("setup_completed") || !model.pendingRecovery.isEmpty {
                    Panel(title: model.pendingRecovery.isEmpty ? "完成使用准备" : "有 \(model.pendingRecovery.count) 个任务等待恢复确认", icon: "list.bullet.clipboard") {
                        Text("核对通知、归档及任务状态；恢复发送前查看账号和规则。")
                        Button("打开准备与恢复向导") { model.selection = .setup }
                    }
                }
                Button("查看所有账号 · \(model.accountAttentionCount) 个账号需要关注") { model.selection = .accounts }
                Panel(title: model.connection, icon: "network") {
                    HStack {
                        Text("最近同步：\(dateText(model.state.number("last_sync")))").foregroundStyle(.secondary)
                        Spacer()
                        if model.state.string("auth") == "offline" { Button("重新连接") { model.connect() } }
                        Button("同步归档") { model.request("sync") }
                    }
                    if model.settings.bool("pause_all") { Label("全部功能已暂停，继续接收已有发送的回执", systemImage: "pause.circle").foregroundStyle(.orange) }
                }
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 16) {
                    module("消息提醒", "bell", model.settings.bool("reminders") ? "已开启" : "已关闭", "系统通知与来源语音", .reminders)
                    module("聊天归档", "archivebox", "\(model.conversations.filter { $0.bool("selected") }.count) 个会话", "已使用 \(ByteCountFormatter.string(fromByteCount: Int64(model.state.number("usage")), countStyle: .file))", .archive)
                    module("自动回复", "bolt", "\(model.enabledTaskCount) / \(model.taskRules.count) 个任务已开启", model.settings.bool("pause_sends") ? "新发送已暂停" : "发送结果不明时不会重试", .automation)
                    module("在线状态策略", "clock", model.settings.bool("online") ? "已启用实验功能" : "默认关闭", "连接状态与他人看到的在线状态不同", .settings)
                }
                if model.state.bool("quota_paused") {
                    Panel(title: "归档已暂停", icon: "exclamationmark.circle") {
                        Text("本地容量已到上限或数据无法安全写入。清理记录后再继续归档。")
                        Button("前往数据管理") { model.selection = .settings }
                    }
                }
                let attention = model.executions.filter { $0.string("status") == "send_unknown" || $0.bool("receipt_overdue") }
                if !attention.isEmpty {
                    Panel(title: "\(attention.count) 条发送需要核对", icon: "exclamationmark.bubble") {
                        Text("尚未收到登记回执或发送结果无法确认。请在原会话中核对。")
                        Button("查看任务结果") { model.selection = .results }
                    }
                }
                Panel(title: "最近活动", icon: "clock.arrow.circlepath") {
                    ActivityRows(rows: Array(model.activities.prefix(10)))
                }
            }.padding(28)
        }.background(Color(nsColor: .windowBackgroundColor))
    }
    func module(_ title: String, _ icon: String, _ status: String, _ detail: String, _ destination: Destination) -> some View {
        Panel(title: title, icon: icon) {
            Text(status).font(.title3.weight(.semibold))
            Text(detail).font(.caption).foregroundStyle(.secondary)
            Button("管理") { model.selection = destination }
        }
    }
}

struct RemindersView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageTitle(title: "消息提醒", subtitle: "只在需要的时候提醒你。语音默认只播报来源。")
                Panel(title: "提醒方式", icon: "bell.badge") {
                    settingToggle("开启消息提醒", "reminders")
                    settingToggle("提醒私聊消息", "private")
                    settingToggle("提醒群组中提及我的消息", "mentions")
                    Divider()
                    settingToggle("语音播报消息来源", "voice")
                    settingToggle("播放系统通知声音", "sound")
                    settingToggle("在系统通知中显示正文预览", "preview")
                    settingToggle("语音朗读消息正文", "voice_content")
                    Text("正文朗读由你主动开启；会话单独设置可以覆盖默认提醒方式，免打扰优先。")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Text(model.notificationStatus).foregroundStyle(.secondary)
                        Spacer()
                        Button("测试通知") { model.testNotification() }
                        Button("测试语音") { model.testVoice() }
                    }
                    Text("语音状态：\(model.voiceStatus)").font(.caption).foregroundStyle(.secondary)
                }
                Panel(title: "安静一会儿", icon: "moon") {
                    HStack {
                        Button("暂停提醒 30 分钟") { model.control("pause_reminders") }
                        Button("恢复提醒") { model.control("resume_reminders") }
                    }
                    if model.settings.number("paused_reminders_until") > Date().timeIntervalSince1970 {
                        Text("暂停至 \(dateText(model.settings.number("paused_reminders_until")))").foregroundStyle(.orange)
                    }
                    Text("只暂停通知和语音。归档与已开启的自动回复继续运行。相同会话 10 秒内的消息合并提醒。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                QuietHoursView()
                Panel(title: "免打扰会话", icon: "bell.slash") {
                    Text("勾选会话表示免打扰。单独设置仍只提醒私聊和被提及消息，普通群消息不会触发。")
                        .font(.caption).foregroundStyle(.secondary)
                    if model.conversations.isEmpty { Text("连接账号后会显示会话列表").foregroundStyle(.secondary) }
                    ForEach(model.conversations, id: \.chatID) { row in
                        HStack {
                        Toggle(row.string("title"), isOn: Binding(get: {
                            (model.settings["excluded"] as? [Int64] ?? []).contains(Int64(row.number("chat")))
                        }, set: { excluded in
                            var ids = model.settings["excluded"] as? [Int64] ?? []
                            let id = Int64(row.number("chat"))
                            ids.removeAll { $0 == id }
                            if excluded { ids.append(id) }
                            model.change("excluded", ids)
                        }))
                        Spacer()
                        Picker("提醒方式", selection: Binding(get: {
                            model.settings.object("conversation_modes").string(String(row.chatID)).isEmpty ? "default" : model.settings.object("conversation_modes").string(String(row.chatID))
                        }, set: { value in
                            var modes = model.settings.object("conversation_modes"); modes[String(row.chatID)] = value
                            model.change("conversation_modes", modes)
                        })) {
                            Text("跟随默认").tag("default"); Text("静音").tag("silent")
                            Text("仅通知").tag("notification"); Text("仅语音").tag("voice"); Text("通知和语音").tag("both")
                        }.frame(width: 150)
                        }
                    }
                }
            }.padding(28)
        }
    }
    func settingToggle(_ title: String, _ key: String) -> some View {
        Toggle(title, isOn: Binding(get: { model.settings.bool(key) }, set: { model.change(key, $0) }))
    }
}

extension Dictionary where Key == String, Value == Any {
    var countryKey: String { string("key") }
    var chatID: Int64 { Int64(number("chat")) }
    var messageKey: String { "\(chatID):\(Int64(number("id")))" }
    var activityID: Int64 { Int64(number("id")) }
    var executionKey: String { "\(Int64(number("target"))):\(string("number"))" }
    var promptKey: String { "\(Int64(number("target"))):\(Int64(number("message_id")))" }
    var archiveBody: String {
        if bool("deleted") { return "这条消息已被删除" }
        if !string("text").isEmpty { return string("text") }
        if !string("attachment").isEmpty { return "[\(string("attachment"))]" }
        if !string("media").isEmpty || ["saved", "not_requested", "failed", "too_large", "unknown_size"].contains(string("media_state")) { return "图片（无文字说明）" }
        return "无文字内容 · 类型未记录，可尝试补全"
    }
}

struct ArchiveRepairItem: Identifiable {
    let chat: Int64
    var id: Int64 { chat }
}

struct ArchiveView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var showConversations = false
    @ViewState private var mode = "recent"
    @ViewState private var archiveStart = Date().addingTimeInterval(-7 * 86400)
    @ViewState private var showImport = false
    @ViewState private var selectedMessage: String?
    @ViewState private var checkedMessages = Set<String>()
    @ViewState private var cleanup: ArchiveCleanupItem?
    @ViewState private var repair: ArchiveRepairItem?
    @FocusState private var searchFocused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            PageTitle(title: "聊天归档", subtitle: "记录你选择的会话，随时查找上下文。停止归档会保留已有记录。")
            HStack {
                TextField("搜索消息内容", text: $model.query).textFieldStyle(.roundedBorder).focused($searchFocused).onSubmit { model.search() }
                Picker("会话", selection: $model.searchChat) {
                    Text("全部会话").tag("0")
                    ForEach(model.conversations, id: \.chatID) { row in Text(row.string("title")).tag(String(row.chatID)) }
                }.frame(width: 200)
                Button("搜索") { model.search() }.keyboardShortcut(.return, modifiers: [])
                Button("选择归档会话") { showConversations = true }
            }
            HStack {
                TextField("发送者", text: $model.sender).textFieldStyle(.roundedBorder).frame(width: 160)
                Toggle("按日期筛选", isOn: $model.dateFilter)
                if model.dateFilter {
                    DatePicker("从", selection: $model.startDate, displayedComponents: .date).labelsHidden()
                    DatePicker("至", selection: $model.endDate, displayedComponents: .date).labelsHidden()
                }
                Spacer()
            }.font(.caption)
            Toggle("显示已删除消息的状态", isOn: $model.includeDeleted).font(.caption).onChange(of: model.includeDeleted) { _, _ in model.search() }
            HStack {
                Spacer()
                Menu("清理本地记录") {
                    Button("删除勾选消息（\(checkedMessages.count) 条）…") {
                        let rows = model.results.filter { checkedMessages.contains($0.messageKey) }.map { ["chat": Int64($0.number("chat")), "id": Int64($0.number("id"))] }
                        cleanup = ArchiveCleanupItem(value: ["scope": "selected", "messages": rows])
                    }.disabled(checkedMessages.isEmpty)
                    Button("清理当前筛选结果…") { cleanup = ArchiveCleanupItem(value: model.filters().merging(["scope": "filter"]) { _, new in new }) }
                    Button("清空当前会话历史…") { cleanup = ArchiveCleanupItem(value: ["scope": "conversation", "chat": Int64(model.searchChat) ?? 0]) }.disabled(model.searchChat == "0")
                }
                Menu("导出") {
                    Button("导出 JSON") { model.previewExport("json") }
                    Button("导出文本") { model.previewExport("txt") }
                }
                Toggle("包含图片", isOn: $model.exportMedia)
                Button("导入旧记录") { showImport = true }
                Button("检查旧空记录…") { repair = ArchiveRepairItem(chat: Int64(model.searchChat) ?? 0) }
                    .disabled(!model.conversations.contains { String($0.chatID) == model.searchChat && $0.string("kind") == "group" } || model.busy)
            }.font(.caption)
            Divider()
            HStack {
                Text("找到 \(model.resultCount) 条 · 已显示 \(model.results.count) 条").font(.caption).foregroundStyle(.secondary)
                Button("勾选已加载（最多500条）") { checkedMessages = Set(model.results.prefix(500).map { $0.messageKey }) }.disabled(model.results.isEmpty)
                Button("取消勾选") { checkedMessages.removeAll() }.disabled(checkedMessages.isEmpty)
                if (model.state["syncing_chats"] as? [Int64] ?? []).count > 0 { ProgressView().controlSize(.small); Text("归档补齐中").font(.caption) }
                Spacer()
                Button(model.settings.bool("archive_paused") ? "恢复归档" : "暂停归档") { model.control(model.settings.bool("archive_paused") ? "resume_archive" : "pause_archive") }
            }
            HSplitView {
                List(selection: $model.searchChat) {
                    Text("全部归档").tag("0")
                    ForEach(model.conversations.filter { $0.bool("selected") || $0.number("count") > 0 || String($0.chatID) == model.searchChat }, id: \.chatID) { row in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(row.string("title")); Text("\(Int(row.number("count"))) 条").font(.caption).foregroundStyle(.secondary)
                        }.tag(String(row.chatID))
                    }
                }.frame(minWidth: 135, idealWidth: 160, maxWidth: 210, maxHeight: .infinity)
                if model.results.isEmpty {
                    ContentUnavailableView("没有匹配记录", systemImage: "archivebox", description: Text("选择归档会话或调整条件。"))
                        .frame(minWidth: 200, maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(selection: $selectedMessage) {
                        ForEach(model.results, id: \.messageKey) { row in
                            HStack(alignment: .top) {
                                Toggle("选择消息", isOn: Binding(get: { checkedMessages.contains(row.messageKey) }, set: { value in
                                    if value { checkedMessages.insert(row.messageKey) } else { checkedMessages.remove(row.messageKey) }
                                })).labelsHidden()
                                MessageRow(message: row)
                            }.tag(row.messageKey).contextMenu {
                                Button("删除这条本地消息…", role: .destructive) { cleanup = ArchiveCleanupItem(value: ["scope": "selected", "messages": [["chat": Int64(row.number("chat")), "id": Int64(row.number("id"))]]]) }
                            }
                        }
                        if model.results.count < model.resultCount { Button("加载更多") { model.search(offset: model.results.count) } }
                    }.listStyle(.inset).frame(minWidth: 280, idealWidth: 340, maxWidth: .infinity, maxHeight: .infinity)
                }
                VStack(alignment: .leading, spacing: 10) {
                    HStack { Text("消息上下文").font(.headline); Spacer(); if model.searchChat != "0" { Button("原会话") { model.openTelegramChat(Int64(model.searchChat) ?? 0) } } }
                    if selectedMessage == nil { Text("选择一条消息查看详情").foregroundStyle(.secondary) }
                    else { ScrollView { LazyVStack(alignment: .leading) { ForEach(model.context, id: \.messageKey) { row in MessageRow(message: row, showContextAction: false) } } } }
                }.padding(12).frame(minWidth: 215, idealWidth: 270, maxWidth: 380, maxHeight: .infinity, alignment: .topLeading)
            }.frame(minHeight: 240, maxHeight: .infinity)
        }.padding(28).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .onAppear { model.search(); if model.searchFocusRequest > 0 { searchFocused = true } }
        .onChange(of: model.searchFocusRequest) { _, _ in searchFocused = true }
        .onChange(of: model.searchChat) { _, _ in selectedMessage = nil; model.context = []; model.search() }
        .sheet(item: $cleanup) { ArchiveCleanupView(options: $0.value) }
        .sheet(item: $repair) { item in
            ArchiveRepairView(chat: item.chat) { selectedMessage = nil; model.context = []; model.search() }
        }
        .onChange(of: model.results.map { $0.messageKey }) { _, keys in checkedMessages.formIntersection(Set(keys)) }
        .onChange(of: selectedMessage) { _, key in if let row = model.results.first(where: { $0.messageKey == key }) { model.openContext(row, showSheet: false) } }
        .sheet(isPresented: $showConversations) {
            VStack(alignment: .leading, spacing: 16) {
                Text("选择归档会话").font(.title2.weight(.semibold))
                Text("首版支持私聊和群组；频道暂不支持。历史记录以平台当前可访问内容为准。")
                    .foregroundStyle(.secondary)
                Picker("首次归档范围", selection: $mode) {
                    Text("最近 300 条").tag("recent"); Text("仅新消息").tag("live"); Text("从指定日期开始").tag("date")
                }
                if mode == "date" { DatePicker("开始日期", selection: $archiveStart, displayedComponents: .date) }
                List(model.conversations, id: \.chatID) { row in
                    HStack {
                        Toggle(row.string("title"), isOn: Binding(get: { row.bool("selected") }, set: {
                            model.request("archive_select", ["chat": row.chatID, "selected": $0, "mode": mode,
                                                              "start": mode == "date" ? Calendar.current.startOfDay(for: archiveStart).timeIntervalSince1970 : 0])
                        })).disabled(row.string("kind") == "channel")
                        Spacer()
                        Text("\(Int(row.number("count"))) 条").foregroundStyle(.secondary)
                    }
                }
                HStack {
                    Button("刷新会话") { model.request("dialogs") }
                    Spacer()
                    Button("完成") { showConversations = false; model.search() }.keyboardShortcut(.defaultAction)
                }
            }.padding(24).frame(width: 640, height: 520)
        }
        .sheet(isPresented: $showImport) { LegacyImportView() }
    }
}

struct LegacyImportView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    @ViewState private var path = ""
    @ViewState private var chat = "0"
    @ViewState private var zone = "Asia/Shanghai"
    @ViewState private var preview: Object = [:]
    @ViewState private var confirmed = false
    @ViewState private var includeMedia = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("导入旧项目的聊天文本").font(.title2)
            Text("旧文件不含账号与会话 ID，需由你确认归属。原文件保留不动；已有同编号消息优先。可导入文本所引用的同目录 media 图片，单张最大 10 MB。")
                .foregroundStyle(.secondary)
            HStack {
                Text(path.isEmpty ? "未选择文件" : URL(fileURLWithPath: path).lastPathComponent)
                Spacer()
                Button("选择文本文件") {
                    let panel = NSOpenPanel(); panel.allowedContentTypes = [.plainText]; panel.canChooseDirectories = false
                    if panel.runModal() == .OK, let url = panel.url { path = url.path; preview = [:]; confirmed = false }
                }
            }
            Picker("对应会话", selection: $chat) {
                Text("请选择").tag("0")
                ForEach(model.conversations, id: \.chatID) { row in Text(row.string("title")).tag(String(row.chatID)) }
            }
            TextField("旧记录使用的时区", text: $zone).textFieldStyle(.roundedBorder)
            Button("预览文件") {
                model.request("import_preview", ["path": path, "timezone": zone]) { preview = $0 ?? [:] }
            }.disabled(path.isEmpty)
            if !preview.isEmpty {
                Text("识别 \(Int(preview.number("count"))) 条 · \(dateText(preview.number("start"))) 至 \(dateText(preview.number("end")))")
                ForEach(preview.rows("first"), id: \.activityID) { row in Text("\(row.string("sender"))：\(row.string("text"))").lineLimit(2).foregroundStyle(.secondary) }
            }
            Toggle("确认这些记录属于当前账号：\(model.accountName)", isOn: $confirmed)
            Toggle("导入关联图片（预览识别 \(Int(preview.number("images"))) 张）", isOn: $includeMedia)
            Spacer()
            HStack {
                Button("取消") { dismiss() }; Spacer()
                Button("确认导入") {
                    model.request("import_legacy", ["path": path, "timezone": zone, "chat": Int64(chat) ?? 0,
                                                    "confirmed_account": model.state.number("account"), "include_media": includeMedia]) { result in
                        if result != nil { model.search(); dismiss() }
                    }
                }.disabled(!confirmed || chat == "0" || preview.isEmpty).buttonStyle(.borderedProminent)
            }
        }.padding(24).frame(width: 650, height: 480)
    }
}

struct MessageRow: View {
    @EnvironmentObject var model: AppModel
    let message: Object
    var showContextAction = true
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(message.string("sender")).font(.headline)
                Spacer()
            }
            if !message.string("title").isEmpty { Text(message.string("title")).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            Text(dateText(message.number("date"))).font(.caption).foregroundStyle(.secondary)
            if message.string("attachment") == "群组系统消息" { Label("群组系统消息", systemImage: "info.circle").font(.caption).foregroundStyle(.secondary) }
            Text(message.archiveBody)
                .textSelection(.enabled).lineLimit(showContextAction ? 8 : nil)
            if message.bool("edited") { Text("已编辑\(message.number("edited_at") > 0 ? " · " + dateText(message.number("edited_at")) : "")").font(.caption).foregroundStyle(.secondary) }
            HStack {
                if message.bool("forwarded") { Text("转发消息").font(.caption).foregroundStyle(.secondary) }
                if message.number("reply_to") > 0 { Text("回复 #\(Int64(message.number("reply_to")))").font(.caption).foregroundStyle(.secondary) }
            }
            if !message.string("attachment").isEmpty && message.string("attachment") != "群组系统消息" { Text(message.string("attachment")).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
            HStack {
                if !message.string("media").isEmpty { Button("查看图片") { model.openMedia(message) } }
                Spacer()
                if showContextAction { Button("上下文") { model.openContext(message) }.buttonStyle(.borderless) }
            }
            if ["failed", "too_large", "unknown_size", "not_requested"].contains(message.string("media_state")) {
                HStack {
                    Text(["failed": "图片下载失败", "too_large": "图片超过 10 MB，未下载", "unknown_size": "无法确认图片大小，未下载", "not_requested": "图片未归档"][message.string("media_state")] ?? "")
                        .font(.caption).foregroundStyle(.secondary)
                    if message.string("media_state") == "failed" {
                        Text("已尝试 \(Int(message.number("media_attempts"))) 次").font(.caption)
                        Button("重试图片") { model.request("retry_media", ["chat": message.chatID, "id": Int64(message.number("id"))]) { _ in model.search() } }.disabled(!model.state.bool("connected"))
                    }
                }
            }
        }.padding(.vertical, 10)
    }
}

struct ContextView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        VStack(alignment: .leading) {
            HStack { Text("会话上下文").font(.title2); Spacer(); Button("关闭") { model.showContext = false } }
            Text(model.contextNote).foregroundStyle(.secondary)
            if let row = model.context.first { Button("打开原会话") { model.openTelegramChat(row.chatID) } }
            List(model.context, id: \.messageKey) { row in MessageRow(message: row, showContextAction: false) }
        }.padding(24).frame(width: 760, height: 560)
    }
}

struct ActivityRows: View {
    let rows: [Object]
    var body: some View {
        if rows.isEmpty { Text("暂无活动").foregroundStyle(.secondary) }
        ForEach(rows, id: \.activityID) { row in
            HStack(alignment: .top) {
                Image(systemName: row.string("kind") == "attention" ? "exclamationmark.circle" : "circle.fill")
                    .font(.caption2).foregroundStyle(row.string("kind") == "attention" ? Color.orange : .secondary)
                Text(row.string("detail")).frame(maxWidth: .infinity, alignment: .leading)
                Text(dateText(row.number("date"))).font(.caption).foregroundStyle(.secondary)
            }.padding(.vertical, 5)
        }
    }
}

struct ActivityView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var onlyAttention = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageTitle(title: "活动记录", subtitle: "查看功能执行与异常。活动记录不保存消息正文或查岗关键词。")
                Toggle("仅显示需要关注的事项", isOn: $onlyAttention)
                Panel(title: "最近 200 条活动", icon: "clock.arrow.circlepath") {
                    ActivityRows(rows: onlyAttention ? model.activities.filter { $0.string("kind") == "attention" } : model.activities)
                }
                Panel(title: "归档覆盖与缺口", icon: "calendar.badge.exclamationmark") {
                    let ranges = model.state.rows("ranges")
                    let pending = ranges.filter { ["gap", "pause"].contains($0.string("kind")) }.count
                    Text("\(pending) 个中断范围待补齐 · \((model.state["syncing_chats"] as? [Int64] ?? []).count) 个会话正在同步。补齐仅覆盖平台当前可访问记录，不保证已删除消息完整。")
                        .font(.caption).foregroundStyle(pending > 0 ? Color.orange : .secondary)
                    if model.state.rows("ranges").isEmpty { Text("选择归档会话后会记录起点和中断情况").foregroundStyle(.secondary) }
                    ForEach(model.state.rows("ranges"), id: \.activityID) { row in
                        VStack(alignment: .leading, spacing: 4) {
                            Text("\(row.string("title")) · \(row.string("detail"))")
                            Text("\(dateText(row.number("start"))) → \(row.number("end") > 0 ? dateText(row.number("end")) : "仍待恢复")").font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 5)
                    }
                }
            }.padding(28)
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var weekly = Array(repeating: "", count: 7)
    @ViewState private var zone = "Asia/Shanghai"
    @ViewState private var days = 30
    @ViewState private var quota = 1024
    @ViewState private var unlimited = false
    @ViewState private var eraseText = ""
    @ViewState private var showErase = false
    @ViewState private var showLogout = false
    @ViewState private var eraseChat = "0"
    @ViewState private var showEraseChat = false
    @ViewState private var schedulePreview: [Object] = []
    @ViewState private var previewedSchedule = false
    let weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageTitle(title: "设置", subtitle: "管理账号、运行方式与本地数据。")
                if !model.changedSystemZone.isEmpty {
                    Panel(title: "系统时区已变化", icon: "globe") {
                        Text("新的系统时区：\(model.changedSystemZone)。当前规则仍使用原时区。")
                        HStack {
                            Button("采用新时区") { model.request("follow_system_timezone", ["timezone": model.changedSystemZone]) { result in if result != nil { model.changedSystemZone = ""; loadSettings() } } }
                            Button("保留原时区") { model.changedSystemZone = "" }
                        }
                        Text("采用后会调整在线、静音和发送时段，并将自动回复恢复仅提醒。").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Panel(title: "账号", icon: "person.crop.circle") {
                    AccountAvatar(identity: model.identity, root: model.root, size: 48)
                    Text("\(model.accountName) · ID \(String(Int64(model.state.number("account"))))")
                    Text("各账号同时运行。当前页面的提醒、归档、规则、设备信息和备份只属于选中的账号。").font(.caption).foregroundStyle(.secondary)
                    if !model.demo { Button("添加 Telegram 账号…") { model.addAccount() }.disabled(model.busy) }
                    Text("会话保存在本地权限受限目录，未额外加密。请保护这台 Mac 的登录账户。").font(.caption).foregroundStyle(.secondary)
                    Button("退出当前账号") { showLogout = true }.disabled(model.demo)
                    if model.demo { Button("返回真实账号登录") { model.switchMode(false) } }
                }
                Panel(title: "产品更新与账号资料", icon: "arrow.triangle.2.circlepath") {
                    Button("检查更新与安全升级…") { model.showUpdates = true }
                    Button("恢复保留的账号资料…") { model.showRetainedAccounts = true }
                }
                NetworkSettingsView()
                Panel(title: "运行方式", icon: "menubar.rectangle") {
                    Toggle("登录 Mac 后启动", isOn: Binding(get: { model.launchAtLogin }, set: { model.setLoginItem($0) }))
                    Toggle("运行时阻止空闲睡眠", isOn: Binding(get: { model.preventSleep }, set: { model.setPreventSleep($0) }))
                    Text("关闭窗口会保留菜单栏；⌘Q 会停止消息引擎。合盖、强制睡眠和断网仍会中断工作。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                DisclosureGroup("高级设置", isExpanded: $model.showAdvancedAPI) {
                    APISettingsView().padding(.top, 12)
                    DeviceProfileView().padding(.top, 12)
                }
                Panel(title: "在线状态策略 · 实验功能", icon: "clock") {
                    Toggle("按时段请求在线状态", isOn: Binding(get: { model.settings.bool("online") }, set: { model.change("online", $0) }))
                    Text("这项功能不能保证他人始终看到你在线。不会向收藏夹发送或删除消息。空白时段表示关闭。")
                        .font(.caption).foregroundStyle(.secondary)
                    TextField("IANA 时区，例如 Asia/Shanghai", text: $zone).textFieldStyle(.roundedBorder)
                    ForEach(0..<7, id: \.self) { day in
                            HStack { Text(weekdays[day]).frame(width: 48); TextField("09:00-18:00，多段用逗号分隔", text: $weekly[day]).textFieldStyle(.roundedBorder); Button("全天") { weekly[day] = "00:00-24:00" } }
                    }
                    HStack {
                        Button("预览未来七天") {
                            guard let values = parseWeekly(weekly) else { model.error = "时间段格式不正确"; return }
                            model.request("schedule_preview", ["weekly": values, "timezone": zone]) { result in if let result { schedulePreview = result.rows("intervals"); previewedSchedule = true } }
                        }
                        Button("保存时间安排") { saveSchedule() }
                    }
                    Text("最近成功状态请求：\(dateText(model.state.number("online_last_success")))。这不代表所有联系人实际看到在线。")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("跨午夜时段归属于开始当天，例如周五 23:00-07:00 会延续到周六早晨。").font(.caption).foregroundStyle(.secondary)
                    if previewedSchedule { Text(schedulePreview.isEmpty ? "预览：没有启用时段" : "以下为预览，尚未保存").font(.caption).foregroundStyle(.secondary) }
                    ForEach(Array((previewedSchedule ? schedulePreview : model.state.rows("schedule")).enumerated()), id: \.offset) { item in
                        Text("\(dateText(item.element.number("start"))) → \(dateText(item.element.number("end")))").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Panel(title: "本地数据", icon: "internaldrive") {
                    Toggle("归档图片（单张最大 10 MB）", isOn: Binding(get: { model.settings.bool("images") }, set: { model.change("images", $0) }))
                    Toggle("不设归档容量上限", isOn: $unlimited)
                    HStack {
                        Stepper("保留 \(days) 天", value: $days, in: 1...365).frame(maxWidth: 250)
                        TextField("容量上限 MB", value: $quota, format: .number).textFieldStyle(.roundedBorder).frame(width: 160).disabled(unlimited)
                        Button("保存数据策略") { model.request("settings", ["retention_days": days, "quota_mb": unlimited ? 0 : quota]) }
                    }
                    Text("已用 \(ByteCountFormatter.string(fromByteCount: Int64(model.state.number("usage")), countStyle: .file))。" + (model.settings.number("quota_mb") > 0 ? "容量上限 \(Int(model.settings.number("quota_mb"))) MB，达到上限暂停归档。" : "未设容量上限，仍会检查磁盘空间。"))
                        .font(.caption).foregroundStyle(.secondary)
                    Text("发送编号与状态长期保留以防重复。消息和关键词按保留期清理；删除聊天记录不会删除发送账本。")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("清理过期记录") { model.request("prune") }
                        Button("打开数据目录") { NSWorkspace.shared.open(model.root) }
                        Button("预览诊断报告") { model.diagnosticPreview() }
                    }
                    HStack {
                        Button("明文备份…") { model.backupData() }
                        Button("加密备份…") { model.backupPasswordForRestore = false; model.showBackupPassword = true }
                        Button("预览并恢复备份") { model.chooseBackup() }
                    }.disabled(model.busy)
                    Text("可选择明文ZIP或密码加密备份，包含当前账号的归档、图片、设置与发送账本，不包含登录会话、API或代理密码。密码不会保存，遗失无法恢复。正确恢复会合并记录、保留去重账本，并关闭自动回复与在线策略。")
                        .font(.caption).foregroundStyle(.secondary)
                    Divider()
                    HStack {
                        Picker("清理会话", selection: $eraseChat) {
                            Text("选择会话").tag("0")
                            ForEach(model.conversations, id: \.chatID) { row in Text(row.string("title")).tag(String(row.chatID)) }
                        }
                        Button("删除该会话归档", role: .destructive) { eraseText = ""; showEraseChat = true }.disabled(eraseChat == "0")
                    }
                    Button("删除当前账号的全部本地数据", role: .destructive) { eraseText = ""; showErase = true }
                }
                Text("即讯 \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "开发构建") · 内部使用版 · Apple Silicon · 数据仅保存在本机")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(28)
        }
        .onAppear { loadSettings() }
        .onChange(of: model.configurationRevision) { _, _ in loadSettings(); schedulePreview = []; previewedSchedule = false }
        .alert("退出 Telegram 账号", isPresented: $showLogout) {
            Button("取消", role: .cancel) { }
            Button("退出账号", role: .destructive) { model.logout() }
        } message: { Text("仅停止当前账号并尝试撤销它的远端会话，已有归档保留，其他账号继续运行。若撤销未确认，请在 Telegram 设备管理中核对。") }
        .alert("恢复当前账号的备份", isPresented: $model.showRestore) {
            Button("取消", role: .cancel) { model.restorePassword = "" }
            Button("确认合并并恢复") { model.restoreBackup() }
        } message: {
            Text("当前账号：\(model.accountName)。备份包含 \(Int(model.restorePreview.object("counts").number("messages"))) 条消息、\(Int(model.restorePreview.object("counts").number("executions"))) 条发送记录。合并归档与账本，保留现有消息版本；按备份恢复配置，自动回复与在线策略关闭。")
        }
        .alert("删除全部本地数据", isPresented: $showErase) {
            TextField("请输入：删除全部", text: $eraseText)
            Button("取消", role: .cancel) { }
            Button("删除全部", role: .destructive) { model.request("erase_account", ["confirm": eraseText]) }
        } message: { Text("包括聊天记录、规则和防重复账本，并退出账号。清除账本后，重新开启规则会失去历史编号的去重保护。") }
        .alert("删除会话归档", isPresented: $showEraseChat) {
            TextField("请输入：删除", text: $eraseText)
            Button("取消", role: .cancel) { }
            Button("删除", role: .destructive) { model.request("erase_chat", ["chat": Int64(eraseChat) ?? 0, "confirm": eraseText]); model.search() }
        } message: { Text("停止该会话归档并删除已有本地内容。发送账本继续保留。") }
    }
    func loadSettings() {
        days = Int(model.settings.number("retention_days")); quota = Int(model.settings.number("quota_mb"))
        unlimited = quota == 0; if unlimited { quota = 1024 }
        zone = model.settings.string("timezone")
        if let value = model.settings["weekly"] as? [[[String]]], value.count == 7 {
            weekly = value.map { $0.map { $0.joined(separator: "-") }.joined(separator: ", ") }
        }
    }
    func saveSchedule() {
        guard let result = parseWeekly(weekly) else { model.error = "时间段应使用 09:00-18:00 格式"; return }
        model.request("settings", ["weekly": result, "timezone": zone])
    }
}

func parseWeekly(_ value: [String]) -> [[[String]]]? {
    var result: [[[String]]] = []
    for day in value {
        if day.trimmingCharacters(in: .whitespaces).isEmpty { result.append([]); continue }
        let pairs = day.replacingOccurrences(of: "，", with: ",").split(separator: ",").map {
            $0.split(separator: "-").map { $0.trimmingCharacters(in: .whitespaces) }
        }
        guard pairs.allSatisfy({ $0.count == 2 }) else { return nil }
        result.append(pairs)
    }
    return result
}

struct QuietHoursView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var enabled = false
    @ViewState private var zone = "Asia/Shanghai"
    @ViewState private var weekly = Array(repeating: "", count: 7)
    var body: some View {
        Panel(title: "定时静音", icon: "moon.zzz") {
            Toggle("按时段静音通知和语音", isOn: $enabled)
            if enabled {
                TextField("静音时区", text: $zone).textFieldStyle(.roundedBorder)
                ForEach(0..<7, id: \.self) { day in
                    HStack {
                        Text(["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day]).frame(width: 48)
                        TextField("例如 12:00-13:00,22:00-07:00", text: $weekly[day]).textFieldStyle(.roundedBorder)
                    }
                }
            }
            Button("保存静音时段") {
                guard let hours = parseWeekly(weekly) else { model.error = "时间段应使用 12:00-13:00 格式"; return }
                model.request("settings", ["quiet_enabled": enabled, "quiet_timezone": zone, "quiet_weekly": hours])
            }
            Text(model.state.bool("quiet_active") ? "当前处于静音时段。归档与已授权自动回复继续运行。" : "当前不在静音时段。空白时段不静音；支持跨午夜。")
                .font(.caption).foregroundStyle(.secondary)
        }.onAppear {
            enabled = model.settings.bool("quiet_enabled"); zone = model.settings.string("quiet_timezone")
            if let values = model.settings["quiet_weekly"] as? [[[String]]], values.count == 7 {
                weekly = values.map { $0.map { $0.joined(separator: "-") }.joined(separator: ",") }
            }
        }
    }
}

struct DeviceProfileView: View {
    @EnvironmentObject var model: AppModel
    @ViewState private var device = ""
    @ViewState private var system = ""
    @ViewState private var version = ""
    @ViewState private var apply = false
    var values: Object { ["device_model": device, "system_version": system, "app_version": version] }
    var editingLogin: Bool { ["connecting", "phone", "code", "password", "identity", "logging_out"].contains(model.state.string("auth")) }
    var body: some View {
        Panel(title: "Telegram 设备信息", icon: "desktopcomputer") {
            TextField("设备名称", text: $device).textFieldStyle(.roundedBorder).accessibilityLabel("设备名称")
            TextField("系统版本", text: $system).textFieldStyle(.roundedBorder).accessibilityLabel("系统版本")
            TextField("客户端显示版本", text: $version).textFieldStyle(.roundedBorder).accessibilityLabel("客户端显示版本")
            Text("留空恢复默认值。每项最多 128 个字符；实际软件版本仍显示在关于信息中。")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("保存设备信息") { model.request("device_profile", values) { result in if result != nil { model.info = "设备信息已保存，下次连接时生效"; load() } } }
                Button("立即应用并重新连接") { apply = true }.disabled(!model.state.bool("connected"))
            }.disabled(model.demo || model.busy || editingLogin)
            let current = model.state.object("active_profile")
            if !current.isEmpty { Text("当前连接：\(current.string("device_model")) · \(current.string("system_version")) · \(current.string("app_version"))").font(.caption) }
            Button("核对 Telegram 当前会话显示") { model.request("refresh_device_info") }.disabled(model.demo || !model.state.bool("connected") || model.busy)
            let server = model.state.object("server_profile")
            if !server.isEmpty { Text("Telegram 返回：\(server.string("device_model")) · \(server.string("system_version")) · \(server.string("app_name")) \(server.string("app_version")) · \(server.string("country")) \(server.string("region"))").font(.caption) }
            Text("设备列表中的应用名称由 API 标识决定，国家/地区由网络出口 IP 决定，这两项由 Telegram 返回。")
                .font(.caption).foregroundStyle(.secondary)
            if model.effectiveAPI?.isTestOnly == true { Text("当前为官方 TEST ONLY 配置，不能以此作为正式公开发布配置。").font(.caption).foregroundStyle(.orange) }
        }
        .onAppear { load() }
        .alert("应用设备信息", isPresented: $apply) {
            Button("取消", role: .cancel) { }
            Button("保存并重新连接") { model.request("apply_device_profile", values) }
        } message: { Text("重新连接后需要核对账号并重新开启自动回复。现有归档与去重账本保留，不撤销当前登录会话。") }
    }
    func load() {
        let profile = model.state.object("device_profile")
        device = profile.string("device_model"); system = profile.string("system_version"); version = profile.string("app_version")
    }
}

struct DiagnosticsView: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("诊断报告预览").font(.title2)
            Text("仅包含版本、连接状态与数量统计。不包含账号身份、凭据、正文或关键词。").foregroundStyle(.secondary)
            ScrollView { Text(model.diagnostics).font(.body.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
            HStack { Button("关闭") { model.showDiagnostics = false }; Spacer(); Button("保存报告") { model.exportDiagnostics() } }
        }.padding(24).frame(width: 640, height: 480)
    }
}

struct MenuView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.openWindow) var openWindow
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            AccountMenu()
            if model.accounts.count > 1 { Text("其他已登录账号也在后台运行").font(.caption).foregroundStyle(.secondary) }
            if model.ready {
                Text("最近同步：\(dateText(model.state.number("last_sync")))").font(.caption).foregroundStyle(.secondary)
                Text("自动回复：\(model.enabledTaskCount) / \(model.taskRules.count) 个任务开启").font(.caption)
                let count = model.executions.filter { $0.string("status") == "send_unknown" }.count
                if count > 0 { Label("\(count) 条发送待核对", systemImage: "exclamationmark.circle").foregroundStyle(.orange) }
            }
            Divider()
            Button("所有账号 · \(model.accountAttentionCount) 个需关注") { model.selection = .accounts; openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
            Button("打开即讯") { openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
            if model.demo { Button("返回真实账号登录") { model.switchMode(false) } }
            if model.ready {
                Button("暂停提醒 30 分钟") { model.control("pause_reminders") }
                Button(model.settings.bool("pause_sends") ? "恢复新发送" : "暂停新发送") { model.control(model.settings.bool("pause_sends") ? "resume_sends" : "pause_sends") }
                Button(model.settings.bool("pause_all") ? "恢复当前账号" : "暂停当前账号") { model.control(model.settings.bool("pause_all") ? "resume_all" : "pause_all") }
            }
            Divider()
            Button("退出即讯") { NSApp.terminate(nil) }.keyboardShortcut("q")
        }.padding(18).frame(width: 310)
    }
}

struct ArchiveRepairView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) var dismiss
    let chat: Int64
    let onComplete: () -> Void
    @ViewState private var preview: Object = [:]
    @ViewState private var result: Object = [:]
    @ViewState private var confirming = false
    var title: String { model.conversations.first { $0.chatID == chat }?.string("title") ?? "当前群组" }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("补全旧空记录").font(.title2)
            Text("\(model.accountName) · \(title)").font(.headline)
            Text("只处理此群已有的空归档，每批最多100条。确认前只检查本地；确认后按原消息编号从Telegram读取，不发送消息或下载媒体，不修改任务、归档开关和发送账本。")
            Text("只有平台仍可访问的系统事件才能补全。已删除、不可访问或其他类型的记录保持原样，不猜造内容；名称使用读取时可见的资料。")
                .font(.caption).foregroundStyle(.secondary)
            if !model.error.isEmpty { Text(model.error).foregroundStyle(.orange).textSelection(.enabled) }
            if !preview.isEmpty {
                Text("发现 \(Int(preview.number("total"))) 条旧空记录，本批检查 \(Int(preview.number("count"))) 条。")
                Text("预览5分钟有效，不包括之后新增的消息。请先完成当前群组的历史补档；归档暂停或平台限流时不能补全。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !result.isEmpty {
                Text("已补全 \(Int(result.number("repaired"))) 条；平台无法确认 \(Int(result.number("unavailable"))) 条；其他类型 \(Int(result.number("other_type"))) 条；保护跳过 \(Int(result.number("protected"))) 条。剩余 \(Int(result.number("remaining"))) 条空记录。")
                    .textSelection(.enabled)
            }
            if model.demo { Text("演示模式只做本地预览，不访问Telegram或补全记录。").foregroundStyle(.secondary) }
            Spacer()
            HStack {
                Button("关闭", role: .cancel) { dismiss() }.disabled(model.busy)
                Button("从头预览") { load() }.disabled(model.busy)
                Button("下一批预览") { load(after: Int64(preview.number("next_id"))) }.disabled(model.busy || !preview.bool("has_more"))
                Spacer()
                Button("读取并补全本批…") { confirming = true }
                    .disabled(model.demo || model.busy || preview.number("count") == 0 || !result.isEmpty)
            }
        }.padding(26).frame(width: 650, height: 440)
        .onAppear { load() }
        .alert("确认补全当前群组的旧空归档", isPresented: $confirming) {
            Button("取消", role: .cancel) { }
            Button("读取并补全") {
                model.request("archive_repair_apply", ["token": preview.string("token"), "confirmed_account": Int64(model.state.number("account"))]) { value in
                    if let value { result = value; onComplete() }
                }
            }
        } message: { Text("账号：\(model.accountName)\n群组：\(title)\n本批最多\(Int(preview.number("count")))条。网络读取失败不改原记录，不自动循环或重试。") }
    }
    func load(after: Int64 = 0) {
        result = [:]; preview = [:]
        model.request("archive_repair_preview", ["chat": chat, "after_id": after]) { preview = $0 ?? [:] }
    }
}
