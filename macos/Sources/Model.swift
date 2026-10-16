import AppKit
import UniformTypeIdentifiers
import AVFoundation
import Foundation
import Security
import ServiceManagement
import SwiftUI
import UserNotifications
import Darwin
import IOKit.pwr_mgt

typealias Object = [String: Any]

extension Dictionary where Key == String, Value == Any {
    func bool(_ key: String) -> Bool { self[key] as? Bool ?? false }
    func string(_ key: String) -> String { self[key] as? String ?? "" }
    func number(_ key: String) -> Double { (self[key] as? NSNumber)?.doubleValue ?? 0 }
    func object(_ key: String) -> Object { self[key] as? Object ?? [:] }
    func rows(_ key: String) -> [Object] { self[key] as? [Object] ?? [] }
}

func sameJSON(_ lhs: Any, _ rhs: Any) -> Bool {
    if let a = lhs as? Object, let b = rhs as? Object { return NSDictionary(dictionary: a).isEqual(NSDictionary(dictionary: b)) }
    if let a = lhs as? [Object], let b = rhs as? [Object] { return NSArray(array: a).isEqual(NSArray(array: b)) }
    return false
}

enum Destination: String, CaseIterable, Identifiable {
    case overview = "概览", reminders = "消息提醒", archive = "聊天归档"
    case automation = "任务管理", results = "任务结果", accounts = "所有账号", setup = "准备与恢复"
    case activity = "活动记录", settings = "设置"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .reminders: return "bell"
        case .archive: return "archivebox"
        case .automation: return "bolt"
        case .results: return "checklist"
        case .accounts: return "person.2"
        case .setup: return "list.bullet.clipboard"
        case .activity: return "clock.arrow.circlepath"
        case .settings: return "gearshape"
        }
    }
}

enum Keychain {
    static let service = "local.communications.assistant.telegram"
    static func read(slot: String = "legacy", kind: String = "api_hash") -> String {
        let query: Object = [kSecClass as String: kSecClassGenericPassword,
                             kSecAttrService as String: service, kSecAttrAccount as String: slot == "legacy" ? kind : kind + "_" + slot,
                             kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
    static func save(_ value: String, slot: String = "legacy", kind: String = "api_hash") throws {
        let query: Object = [kSecClass as String: kSecClassGenericPassword,
                             kSecAttrService as String: service, kSecAttrAccount as String: slot == "legacy" ? kind : kind + "_" + slot]
        let updateStatus = SecItemUpdate(query as CFDictionary, [kSecValueData as String: Data(value.utf8)] as CFDictionary)
        if updateStatus == errSecSuccess { return }
        guard updateStatus == errSecItemNotFound else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(updateStatus)) }
        var attributes = query
        attributes[kSecValueData as String] = Data(value.utf8)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(attributes as CFDictionary, nil)
        if status != errSecSuccess { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
    }
    static func clear(slot: String = "legacy", kind: String = "api_hash") throws {
        let status = SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                                   kSecAttrService as String: service, kSecAttrAccount as String: slot == "legacy" ? kind : kind + "_" + slot] as CFDictionary)
        if status != errSecSuccess && status != errSecItemNotFound {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }
}

/// 引擎只通过权限受限的本地 Unix socket 通信，不监听网络端口。
final class EngineLink: @unchecked Sendable {
    var process: Process?
    var handle: FileHandle?
    var socketPath = ""
    let writing = NSLock()
    var onObject: (@Sendable (Object) -> Void)?
    var onExit: (@Sendable () -> Void)?
    var intentional = false

    func start(root: URL, demo: Bool) throws {
        intentional = false
        let executable = Bundle.main.resourceURL!.appendingPathComponent("Engine/tgengine")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw NSError(domain: "即讯", code: 1, userInfo: [NSLocalizedDescriptionKey: "应用内的消息引擎缺失，请重新构建应用"])
        }
        socketPath = "/tmp/communications-\(getuid())-\(UUID().uuidString.prefix(8)).sock"
        let child = Process()
        child.executableURL = executable
        child.arguments = ["--root", root.path, "--socket", socketPath, "--timezone", TimeZone.current.identifier] + (demo ? ["--demo"] : [])
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice // 不记录平台异常中的凭据或正文。
        child.terminationHandler = { [weak self] _ in
            guard let self else { return }
            self.handle?.closeFile()
            self.handle = nil
            try? FileManager.default.removeItem(atPath: self.socketPath)
            if !self.intentional { self.onExit?() }
        }
        try child.run()
        process = child
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            for _ in 0..<100 {
                if !child.isRunning { return }
                let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
                var address = sockaddr_un()
                address.sun_family = sa_family_t(AF_UNIX)
                let bytes = Array(self.socketPath.utf8) + [0]
                withUnsafeMutableBytes(of: &address.sun_path) { buffer in
                    buffer.copyBytes(from: bytes)
                }
                let connected = withUnsafePointer(to: &address) { pointer in
                    pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                        Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                    }
                }
                if connected == 0 {
                    var noSignal: Int32 = 1
                    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
                    let stream = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
                    self.handle = stream
                    self.read(stream)
                    return
                }
                Darwin.close(fd)
                Thread.sleep(forTimeInterval: 0.1)
            }
            if child.isRunning { child.terminate() }
        }
    }

    func read(_ stream: FileHandle) {
        var pending = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)
        let descriptor = stream.fileDescriptor
        while true {
            // FileHandle.read(upToCount:) 在 macOS 上会等待填满长度，socket 必须读当前可用字节。
            let count = buffer.withUnsafeMutableBytes { Darwin.read(descriptor, $0.baseAddress, $0.count) }
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { break }
            pending.append(contentsOf: buffer.prefix(count))
            guard pending.count <= 16 * 1024 * 1024 else { break }
            while let end = pending.firstIndex(of: 10) {
                let line = Data(pending.prefix(upTo: end))
                pending.removeSubrange(...end)
                if let object = (try? JSONSerialization.jsonObject(with: line)) as? Object {
                    onObject?(object)
                }
            }
        }
    }

    func send(_ object: Object) throws {
        writing.lock()
        defer { writing.unlock() }
        guard let handle else {
            throw NSError(domain: "即讯", code: 2, userInfo: [NSLocalizedDescriptionKey: "消息引擎正在启动，请稍后再试"])
        }
        var data = try JSONSerialization.data(withJSONObject: object)
        data.append(10)
        try handle.write(contentsOf: data)
    }

    func stop() {
        intentional = true
        handle?.closeFile()
        handle = nil
        if let process, process.isRunning { process.terminate() }
    }
}

@MainActor
final class AppModel: NSObject, ObservableObject, AVSpeechSynthesizerDelegate, UNUserNotificationCenterDelegate {
    @Published var state: Object = [:]
    @Published var accounts: [Object] = []
    @Published private(set) var selectedSlot = "legacy"
    private var accountStates: [String: Object] = [:]
    private var restoredSlots = Set<String>()
    @Published var selection: Destination? = .overview
    @Published var error = ""
    @Published var info = ""
    @Published var busy = false
    @Published var results: [Object] = []
    @Published var resultCount = 0
    @Published var query = ""
    @Published var messageKind = "all"
    @Published private(set) var searching = false
    @Published private(set) var archiveQueryKey = ""
    @Published var selectedMessage: String?
    private var searchGeneration = 0
    private var searchInFlight = Set<String>()
    @Published var showHelp = false
    @Published var searchChat = "0"
    @Published var sender = ""
    @Published var startDate = Date().addingTimeInterval(-30 * 86400)
    @Published var endDate = Date()
    @Published var dateFilter = false
    @Published var includeDeleted = false
    @Published var searchFocusRequest = 0
    @Published var context: [Object] = []
    @Published var showContext = false
    @Published var contextNote = "显示所选消息附近的本地记录；未归档部分不会出现在这里。"
    @Published private(set) var apiSource: TelegramAPISource
    @Published private(set) var customAPI: TelegramAPIConfiguration?
    @Published var showAdvancedAPI = false
    let builtInAPI: TelegramAPIConfiguration?
    @Published var phone = ""
    @Published var code = ""
    @Published var password = ""
    @Published var notificationStatus = "尚未检查"
    @Published var voiceStatus = "待命"
    @Published var preview: Object = [:]
    @Published var diagnostics = ""
    @Published var showDiagnostics = false
    @Published var showIssues = false
    @Published var showUpdates = false
    @Published var preparingUpgrade = false
    @Published var showRetainedAccounts = false
    @Published var showBackupPassword = false
    @Published var backupPasswordForRestore = false
    var restorePassword = ""
    var verifyBackupOnly = false
    @Published var resultsAttention = false
    @Published var exportSummary = ""
    @Published var showExport = false
    @Published var restorePreview: Object = [:]
    @Published var restorePath = ""
    @Published var showRestore = false
    @Published var ledgerPreview: Object = [:]
    @Published var ledgerPath = ""
    @Published var showLedger = false
    @Published var changedSystemZone = ""
    @Published var exportFormat = "json"
    @Published var exportMedia = false
    @Published var launchAtLogin = SMAppService.mainApp.status == .enabled
    @Published var preventSleep = false
    private var powerAssertion: IOPMAssertionID = 0
    private var pending: [String: (Object?) -> Void] = [:]
    private var requestValidity: [String: () -> Bool] = [:]
    private var activeRequests = Set<String>()
    private var outstandingRequests = Set<String>()
    private var contextGeneration = 0
    private var link = EngineLink()
    private var linkGeneration = 0
    private var restartDates: [Date] = []
    @Published var engineUnavailable = false
    @Published var configurationRevision = 0
    private let speech = AVSpeechSynthesizer()
    private var voiceQueue: [(String, Date, Int64, String)] = []
    private var voiceAccounts: [ObjectIdentifier: (Int64, String)] = [:]
    private var skippedVoice: [String: Int] = [:]
    private var pendingNotification: [AnyHashable: Any]?
    private var quitting = false
    var isShuttingDown: Bool { quitting }
    private var observers: [NSObjectProtocol] = []
    var openMain: (() -> Void)?
    @Published var demo: Bool
    var storageRoot: URL {
        if demo, let path = ProcessInfo.processInfo.environment["TGA_DEMO_ROOT"] { return URL(fileURLWithPath: path, isDirectory: true) }
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(demo ? "通讯助手演示" : "通讯助手", isDirectory: true)
    }
    var root: URL { selectedSlot == "legacy" ? storageRoot : storageRoot.appendingPathComponent("accounts/" + selectedSlot, isDirectory: true) }
    func dataRoot(for slot: String) -> URL { slot == "legacy" ? storageRoot : storageRoot.appendingPathComponent("accounts/" + slot, isDirectory: true) }
    func preferenceKey(_ key: String, slot: String? = nil) -> String {
        let value = slot ?? selectedSlot
        return value == "legacy" ? key : key + "." + value
    }

    var settings: Object { state.object("settings") }
    var ready: Bool { ["ready", "offline"].contains(state.string("auth")) }
    var hasSavedAPI: Bool { !(UserDefaults.standard.string(forKey: preferenceKey("apiID")) ?? "").isEmpty }
    var effectiveAPI: TelegramAPIConfiguration? { apiSource.resolve(builtIn: builtInAPI, custom: customAPI) }
    var canChangeAPI: Bool { !demo && !busy && state.string("auth") == "disconnected" }
    var canRemoveAccount: Bool { !demo && !busy && accounts.count > 1 && state.string("auth") == "disconnected" }
    var canUseMatchingBuiltInAPI: Bool { !demo && !busy && apiSource == .custom && builtInAPI != nil && customAPI == builtInAPI }
    var missingAPIMessage: String {
        apiSource == .builtIn ? "此版本尚未配置 Telegram 服务，暂时无法登录。可以先体验本地演示。" : "自定义 API 尚未配置或无法读取，请在高级设置中重新保存。"
    }
    var conversations: [Object] { state.rows("conversations") }
    var activities: [Object] { state.rows("activities") }
    var executions: [Object] { state.rows("executions") }
    var taskRules: [Object] { state.rows("tasks") }
    var enabledTaskCount: Int { taskRules.filter { $0.bool("automatic") }.count }
    var pendingRecovery: [Object] { taskRules.filter { $0.bool("resume_pending") } }
    var accountAttentionCount: Int {
        accounts.filter { $0.bool("error_present") || $0.bool("quota_paused") || $0.number("attention_count") > 0 || $0.number("resume_count") > 0 || $0.string("auth") == "identity" || ($0.number("account") > 0 && !$0.bool("connected")) }.count
    }
    var identity: Object { state.object("identity") }
    var accountName: String { identity.string("name").isEmpty ? "尚未登录" : identity.string("name") }
    var connection: String {
        if demo { return "本地演示 · 不连接 Telegram" }
        if state.number("cooldown") > Date().timeIntervalSince1970 { return "平台限流，等待至 " + dateText(state.number("cooldown")) }
        if state.string("auth") == "error" { return "消息引擎异常，请检查诊断或重新连接" }
        if state.string("auth") == "offline" { return "离线模式 · 可浏览本地归档" }
        if ready { return state.bool("connected") ? "Telegram 已连接" : "连接中断，等待恢复" }
        return ["connecting": "正在连接", "identity": "等待确认账号", "phone": "等待输入手机号", "code": "等待验证码", "password": "等待二次验证密码"][state.string("auth")] ?? "尚未连接"
    }

    override init() {
        let useDemo = CommandLine.arguments.contains("--demo")
        demo = useDemo
        let savedID = UserDefaults.standard.string(forKey: "apiID") ?? ""
        let savedSource = TelegramAPISource.restore(savedValue: UserDefaults.standard.string(forKey: "apiSource"), legacyID: savedID)
        apiSource = savedSource
        customAPI = !useDemo && savedSource == .custom && !savedID.isEmpty ? try? TelegramAPIConfiguration(apiID: savedID, apiHash: Keychain.read()) : nil
        builtInAPI = Bundle.main.url(forResource: "TelegramAPI", withExtension: "json").flatMap { try? TelegramAPIConfiguration.load(from: $0) }
        super.init()
        speech.delegate = self
        UNUserNotificationCenter.current().delegate = self
        startEngine()
        updateNotificationStatus()
        if UserDefaults.standard.bool(forKey: "preventIdleSleep") { setPreventSleep(true, persist: false) }
        observers.append(NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                for account in self.accounts where account.string("auth") == "ready" {
                    self.request("sync", slot: account.string("slot"), background: true)
                }
            }
        })
        observers.append(NotificationCenter.default.addObserver(forName: .NSSystemTimeZoneDidChange, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.changedSystemZone = TimeZone.current.identifier }
        })
    }

    func startEngine() {
        engineUnavailable = false
        pending.removeAll(); requestValidity.removeAll(); activeRequests.removeAll(); outstandingRequests.removeAll(); busy = false; resetSearch()
        linkGeneration += 1
        let generation = linkGeneration
        state = ["auth": "starting"]
        link.onObject = { [weak self] object in Task { @MainActor in
            guard let self, self.linkGeneration == generation else { return }
            self.receive(object)
        } }
        link.onExit = { [weak self] in Task { @MainActor in
            guard let self, self.linkGeneration == generation else { return }
            self.engineExited()
        } }
        do {
            try FileManager.default.createDirectory(at: storageRoot, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try link.start(root: storageRoot, demo: demo)
        } catch { self.error = error.localizedDescription }
    }

    func switchMode(_ useDemo: Bool) {
        guard demo || !accounts.contains(where: { ["ready", "offline", "identity", "phone", "code", "password", "connecting"].contains($0.string("auth")) }) else { error = "请先退出所有真实账号再进入演示"; return }
        link.stop()
        pending.removeAll()
        busy = false
        link = EngineLink()
        demo = useDemo
        let savedID = UserDefaults.standard.string(forKey: "apiID") ?? ""
        customAPI = !demo && apiSource == .custom && !savedID.isEmpty ? try? TelegramAPIConfiguration(apiID: savedID, apiHash: Keychain.read()) : nil
        restoredSlots.removeAll(); accountStates.removeAll(); accounts = []; selectedSlot = "legacy"
        results = []; context = []; preview = [:]
        error = ""; info = ""; selection = .overview
        phone = ""; code = ""; password = ""
        startEngine()
    }

    func engineExited() {
        guard !quitting else { return }
        info = ""
        voiceQueue.removeAll(); skippedVoice.removeAll(); speech.stopSpeaking(at: .immediate)
        accounts = accounts.map { var row = $0; row["connected"] = false; return row }
        busy = false
        for callback in pending.values { callback(nil) }
        pending.removeAll()
        restartDates = restartDates.filter { Date().timeIntervalSince($0) < 60 }
        if restartDates.count >= 3 {
            error = "消息引擎一分钟内多次退出，已停止自动重启。请重新打开应用或导出诊断。"
            state["connected"] = false
            engineUnavailable = true
            return
        }
        restartDates.append(Date())
        restoredSlots.removeAll()
        error = "消息引擎意外退出，正在恢复；自动回复需重新启用。"
        Task {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            link = EngineLink()
            startEngine()
        }
    }

    func retryEngine() {
        guard engineUnavailable, !quitting else { return }
        restartDates.removeAll(); restoredSlots.removeAll(); error = ""
        link.stop(); link = EngineLink(); startEngine()
    }

    func receive(_ object: Object) {
        if let id = object["id"] as? String {
            outstandingRequests.remove(id); activeRequests.remove(id); updateBusy()
            let current = requestValidity.removeValue(forKey: id)?() ?? true
            if let message = object["error"] as? String, current {
                error = object.string("slot").isEmpty || object.string("slot") == selectedSlot ? message : "另一个账号操作未完成，请切换账号查看连接状态。"
            }
            pending.removeValue(forKey: id)?(object["result"] as? Object)
            flushPendingNotification()
            return
        }
        switch object.string("event") {
        case "state":
            let next = object.object("data")
            let slot = next.string("slot").isEmpty ? selectedSlot : next.string("slot")
            let previousSlotSettings = accountStates[slot]?.object("settings") ?? [:]
            accountStates[slot] = next
            if next["accounts"] != nil {
                if !sameJSON(accounts, next.rows("accounts")) { accounts = next.rows("accounts") }
                let slots = Set(accounts.map { $0.string("slot") })
                accountStates = accountStates.filter { slots.contains($0.key) }
                restoredSlots.formIntersection(slots)
            }
            let activeSlot = next.string("active_slot")
            if !activeSlot.isEmpty && activeSlot != selectedSlot {
                selectLocalSlot(activeSlot)
            }
            if !demo && !restoredSlots.contains(slot) && ["disconnected", "offline"].contains(next.string("auth")) {
                restoredSlots.insert(slot)
                if next.string("auth") == "offline", let configuration = apiConfiguration(for: slot) {
                    request("connect", connectionArguments(configuration, slot: slot), slot: slot, background: true)
                }
            }
            if next.string("auth") != "ready" || next.object("settings").bool("pause_all") || !next.object("settings").bool("reminders") || next.bool("quiet_active") || next.object("settings").number("paused_reminders_until") > Date().timeIntervalSince1970 || (previousSlotSettings.bool("voice") && !next.object("settings").bool("voice")) {
                cancelVoice(for: slot)
            }
            guard slot == selectedSlot else { return }
            let previous = settings
            if state.number("account") != next.number("account") {
                resetSearch()
                contextGeneration += 1
                results = []; resultCount = 0; context = []; preview = [:]; diagnostics = ""
                showHelp = false; showIssues = false; resultsAttention = false
        showContext = false; showDiagnostics = false; showExport = false
                searchChat = "0"; query = ""; sender = ""; dateFilter = false
            }
            let previousAuth = state.string("auth")
            if !sameJSON(state, next) { state = next }
            if previousAuth != "ready", next.string("auth") == "ready",
               !settings.bool("setup_completed") || !pendingRecovery.isEmpty { selection = .setup }
            if error == "消息引擎意外退出，正在恢复；自动回复需重新启用。",
               ["ready", "identity", "phone", "offline", "disconnected"].contains(next.string("auth")) {
                error = ""
                info = "消息引擎已恢复，自动回复仍需手动开启。"
            }
            if previous.object("rule").number("target") != settings.object("rule").number("target") { preview = [:] }
            if !state.string("error").isEmpty { error = state.string("error") }
            if settings.bool("pause_all") || !settings.bool("reminders") || state.bool("quiet_active") || settings.number("paused_reminders_until") > Date().timeIntervalSince1970 {
                cancelVoice(for: slot)
            }
            if previous.bool("voice") && !settings.bool("voice") { cancelVoice(for: slot); voiceStatus = "语音已关闭" }
            flushPendingNotification()
        case "reminder": deliverReminder(object.object("data"), slot: object.string("slot"))
        case "error": if object.string("slot").isEmpty || object.string("slot") == selectedSlot { error = object.object("data").string("message") }
        case "progress": if object.string("slot").isEmpty || object.string("slot") == selectedSlot { info = object.object("data").string("detail") }
        default: break
        }
    }

    private func updateBusy() {
        let value = !activeRequests.isEmpty
        if busy != value { busy = value }
    }

    func request(_ method: String, _ args: Object = [:], slot: String? = nil, background: Bool = false, valid: (() -> Bool)? = nil, completion: ((Object?) -> Void)? = nil) {
        let id = UUID().uuidString
        let targetSlot = slot ?? selectedSlot
        let quiet = background || ["search", "context", "task_templates", "task_results", "issues", "diagnostics", "retained_accounts", "archive_overview"].contains(method)
        if let valid { requestValidity[id] = valid }
        outstandingRequests.insert(id)
        if !quiet { activeRequests.insert(id) }
        if let completion { pending[id] = { [weak self] result in
            guard self?.selectedSlot == targetSlot || ["select_account", "add_account", "remove_account", "restore_account_entry"].contains(method) else { return }
            completion(result)
        } }
        updateBusy()
        do { try link.send(["protocol": 1, "id": id, "slot": targetSlot, "method": method, "args": args]) }
        catch {
            pending.removeValue(forKey: id)?(nil)
            outstandingRequests.remove(id); activeRequests.remove(id); requestValidity.removeValue(forKey: id); updateBusy()
            self.error = error.localizedDescription
        }
        Task {
            try? await Task.sleep(nanoseconds: ["backup", "backup_preview", "restore_backup", "upgrade_prepare"].contains(method) ? 905_000_000_000 : 95_000_000_000)
            if outstandingRequests.remove(id) != nil {
                activeRequests.remove(id); updateBusy()
                let current = requestValidity.removeValue(forKey: id)?() ?? true
                if selectedSlot == targetSlot && current { error = "操作等待超时，请核对状态后再试" }
                pending.removeValue(forKey: id)?(nil)
            }
        }
    }

    func apiConfiguration(for slot: String) -> TelegramAPIConfiguration? {
        let savedID = UserDefaults.standard.string(forKey: preferenceKey("apiID", slot: slot)) ?? ""
        let source = TelegramAPISource.restore(savedValue: UserDefaults.standard.string(forKey: preferenceKey("apiSource", slot: slot)), legacyID: savedID)
        if source == .builtIn { return builtInAPI }
        return try? TelegramAPIConfiguration(apiID: savedID, apiHash: Keychain.read(slot: slot))
    }

    func selectLocalSlot(_ slot: String) {
        guard slot == "legacy" || UUID(uuidString: slot) != nil else { return }
        resetSearch()
        selectedSlot = slot
        state = accountStates[slot] ?? ["auth": "starting"]
        let savedID = UserDefaults.standard.string(forKey: preferenceKey("apiID")) ?? ""
        apiSource = TelegramAPISource.restore(savedValue: UserDefaults.standard.string(forKey: preferenceKey("apiSource")), legacyID: savedID)
        customAPI = !demo && apiSource == .custom ? apiConfiguration(for: slot) : nil
        contextGeneration += 1; configurationRevision += 1
        results = []; resultCount = 0; context = []; preview = [:]; diagnostics = ""
        restorePreview = [:]; restorePath = ""; restorePassword = ""; verifyBackupOnly = false; showBackupPassword = false; showRetainedAccounts = false; ledgerPreview = [:]; ledgerPath = ""
        showHelp = false; showIssues = false; resultsAttention = false
        showContext = false; showDiagnostics = false; showExport = false; showRestore = false; showLedger = false
        searchChat = "0"; query = ""; sender = ""; dateFilter = false
        phone = ""; code = ""; password = ""; error = ""; info = ""
    }

    func selectAccount(_ slot: String) {
        guard !busy, slot != selectedSlot else { return }
        request("select_account", ["slot": slot])
    }

    func addAccount() {
        guard !busy, !demo else { return }
        request("add_account") { [weak self] result in
            guard result != nil else { return }
            self?.selection = .overview
        }
    }

    func removeAccount(_ slot: String) {
        guard canRemoveAccount, slot == selectedSlot else { error = "账号状态已变化，请重新核对后移除"; return }
        request("remove_account", ["confirm": slot], slot: slot) { [weak self] result in
            guard result?.string("removed_slot") == slot else { return }
            self?.cancelVoice(for: slot)
            self?.selection = .overview
            self?.info = "已移除账号登录入口，本地归档、规则和账本仍保留；其他账号继续运行"
        }
    }

    func connect() {
        guard !demo, !busy else { return }
        guard let configuration = effectiveAPI else { error = missingAPIMessage; return }
        request("connect", connectionArguments(configuration, slot: selectedSlot))
    }

    func saveAPIConfiguration(source: TelegramAPISource, apiID: String, apiHash: String) -> Bool {
        guard canChangeAPI else { error = "请先退出当前账号，并等待连接操作结束后再修改配置"; return false }
        if source == .custom {
            let configuration: TelegramAPIConfiguration
            do { configuration = try TelegramAPIConfiguration(apiID: apiID, apiHash: apiHash, saved: customAPI) }
            catch { self.error = error.localizedDescription; return false }
            do { try Keychain.save(configuration.apiHash, slot: selectedSlot) }
            catch { self.error = "API Hash 无法保存到钥匙串，原配置未更改"; return false }
            UserDefaults.standard.set(String(configuration.apiID), forKey: preferenceKey("apiID"))
            customAPI = configuration
        }
        apiSource = source
        UserDefaults.standard.set(source.rawValue, forKey: preferenceKey("apiSource"))
        phone = ""; code = ""; password = ""
        error = ""
        info = source == .custom ? "已保存自定义 API，返回登录页后使用" : "已切换为产品内置配置"
        return true
    }

    func useMatchingBuiltInAPI() {
        guard canUseMatchingBuiltInAPI else { return }
        apiSource = .builtIn
        UserDefaults.standard.set(apiSource.rawValue, forKey: preferenceKey("apiSource"))
        info = "已改用完全相同的内置连接配置，现有登录保持"
    }

    func loadCustomAPI() {
        guard !demo, customAPI == nil, let savedID = UserDefaults.standard.string(forKey: preferenceKey("apiID")), !savedID.isEmpty else { return }
        do { customAPI = try TelegramAPIConfiguration(apiID: savedID, apiHash: Keychain.read(slot: selectedSlot)) }
        catch { self.error = "无法读取原有自定义配置，请重新填写或允许访问钥匙串" }
    }

    func loginCode() { let value = code; code = ""; request("sign_in", ["code": value]) }
    func loginPassword() { let value = password; password = ""; request("password", ["password": value]) }
    func cancelLogin() {
        request("cancel_login") { [weak self] result in
            guard result != nil else { return }
            self?.phone = ""; self?.code = ""; self?.password = ""
            self?.error = result?.string("error") ?? ""
        }
    }
    func change(_ key: String, _ value: Any) { request("settings", [key: value]) }
    func control(_ action: String) { request("control", ["action": action]) }

    func filters() -> Object {
        var args: Object = ["query": query, "chat": Int64(searchChat) ?? 0, "sender": sender]
        args["kind"] = messageKind
        args["include_deleted"] = includeDeleted
        args["include_media"] = exportMedia
        if dateFilter {
            let calendar = Calendar.current
            args["start"] = calendar.startOfDay(for: startDate).timeIntervalSince1970
            args["end"] = (calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: endDate)) ?? endDate).timeIntervalSince1970 - 0.001
        }
        return args
    }
    func resetSearch() {
        searchGeneration += 1; searchInFlight.removeAll()
        searching = false; archiveQueryKey = ""; selectedMessage = nil; messageKind = "all"
    }
    func search(offset: Int = 0) {
        let data = (try? JSONSerialization.data(withJSONObject: filters(), options: [.sortedKeys])) ?? Data()
        let key = selectedSlot + ":" + String(Int64(state.number("account"))) + ":" + String(decoding: data, as: UTF8.self)
        let requestKey = key + ":" + String(offset)
        guard !searchInFlight.contains(requestKey) else { return }
        if offset > 0 {
            guard key == archiveQueryKey, offset == results.count, !searching else { return }
        } else {
            searchGeneration += 1; searchInFlight.removeAll()
            if archiveQueryKey != key {
                contextGeneration += 1; context = []; selectedMessage = nil
                archiveQueryKey = key
            }
        }
        let generation = searchGeneration
        searchInFlight.insert(requestKey); searching = true
        var args = filters(); args["offset"] = offset
        request("search", args, valid: { [weak self] in self?.searchGeneration == generation && self?.archiveQueryKey == key }) { [weak self] result in
            guard let self, generation == searchGeneration, key == archiveQueryKey else { return }
            searchInFlight.remove(requestKey); searching = false
            guard let result else { return }
            let rows = offset == 0 ? result.rows("messages") : results + result.rows("messages")
            if !sameJSON(results, rows) { results = rows }
            let count = Int(result.number("count")); if resultCount != count { resultCount = count }
            if let selectedMessage, !rows.contains(where: { $0.messageKey == selectedMessage }) {
                self.selectedMessage = nil; contextGeneration += 1; context = []
            }
        }
    }
    func clearContext() { contextGeneration += 1; context = [] }

    func openContext(_ message: Object, showSheet: Bool = true) {
        contextGeneration += 1
        let generation = contextGeneration
        request("context", ["chat": message.number("chat"), "id": message.number("id")], valid: { [weak self] in self?.contextGeneration == generation }) { [weak self] result in
            guard let self, generation == contextGeneration else { return }
            context = result?.rows("messages") ?? []
            contextNote = "显示所选消息附近的本地记录；未归档部分不会出现在这里。"
            showContext = showSheet
        }
    }
    func openTelegramChat(_ chat: Int64) {
        let row = conversations.first { $0.chatID == chat }
        var components = URLComponents(); components.scheme = "tg"
        if let username = row?.string("username"), !username.isEmpty {
            components.host = "resolve"; components.queryItems = [URLQueryItem(name: "domain", value: username)]
        } else if chat > 0 {
            components.host = "user"; components.queryItems = [URLQueryItem(name: "id", value: String(chat))]
        } else { info = "此会话没有可验证的公开跳转链接，请在 Telegram 原客户端选择对应账号查看"; return }
        if let url = components.url, !NSWorkspace.shared.open(url) { error = "未找到可以打开此会话的 Telegram 客户端" }
    }
    func showTaskMessage(_ row: Object) {
        contextGeneration += 1
        let generation = contextGeneration
        request("notification_context", ["chat": Int64(row.number("target")), "id": Int64(row.number("message_id"))]) { [weak self] result in
            guard let self, let result, generation == contextGeneration else { return }
            context = result.rows("messages")
            contextNote = result.string("source") == "remote" ? "只读查看任务来源；未归档内容不会保存到本机。" : "任务对应的本地归档。"
            showContext = true
        }
    }
    func openMedia(_ message: Object) {
        let relative = message.string("media")
        guard !relative.isEmpty, !relative.contains("..") else { return }
        NSWorkspace.shared.open(root.appendingPathComponent(relative))
    }
    func previewExport(_ format: String) {
        exportFormat = format
        request("export_preview", filters()) { [weak self] result in
            guard let self, let result else { return }
            let bytes = ByteCountFormatter.string(fromByteCount: Int64(result.number("estimated_bytes")), countStyle: .file)
            exportSummary = "将按当前筛选导出 \(Int(result.number("count"))) 条消息，约 \(bytes)，格式为 \(format.uppercased())。范围：\(dateText(result.number("start"))) 至 \(dateText(result.number("end")))。包含正文和发送者\(exportMedia ? "及本地图片" : "")，不包含会话凭据。"
            showExport = true
        }
    }
    func export() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "聊天归档.\(exportFormat)"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        var args = filters(); args["path"] = url.path; args["format"] = exportFormat
        request("export", args) { [weak self] result in
            if result != nil { self?.info = "导出已保存"; NSWorkspace.shared.activateFileViewerSelecting([url]) }
        }
    }
    func backupData(password: String = "") {
        let panel = NSSavePanel(); panel.allowedContentTypes = password.isEmpty ? [.zip] : [UTType(filenameExtension: "jixunbackup") ?? .data]; panel.nameFieldStringValue = password.isEmpty ? "即讯本地备份.zip" : "即讯加密备份.jixunbackup"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        request("backup", ["path": url.path, "password": password]) { [weak self] result in if result != nil { self?.info = password.isEmpty ? "本地明文备份已保存，不包含登录会话或API凭据" : "加密备份已保存；密码未保存，请妥善保管" } }
    }
    func chooseBackup(verifyOnly: Bool = false) {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.zip, UTType(filenameExtension: "jixunbackup") ?? .data]; panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        verifyBackupOnly = verifyOnly
        restorePath = url.path; restorePreview = [:]; restorePassword = ""
        do {
            let input = try FileHandle(forReadingFrom: url); defer { try? input.close() }
            if try input.read(upToCount: 8) == Data("JIXUNB1\n".utf8) {
                backupPasswordForRestore = true; showBackupPassword = true
            } else { previewBackup() }
        } catch { self.error = "无法读取备份文件" }
    }
    func previewBackup() {
        request("backup_preview", ["path": restorePath, "password": restorePassword]) { [weak self] result in
            guard let self else { return }
            guard let result else { restorePassword = ""; return }
            if verifyBackupOnly {
                restorePassword = ""; restorePreview = [:]; restorePath = ""
                info = "所选备份完整性及账号归属校验通过；未恢复资料，原任务保持运行"
            } else { restorePreview = result; showRestore = true }
        }
    }
    func chooseLegacyLedger() {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.json]; panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        ledgerPath = url.path; ledgerPreview = [:]
        request("ledger_preview", ["path": url.path]) { [weak self] result in
            guard let self, let result else { return }
            ledgerPreview = result; showLedger = true
        }
    }
    func importLegacyLedger() {
        request("import_ledger", ["path": ledgerPath, "digest": ledgerPreview.string("digest"), "confirmed_account": Int64(state.number("account"))]) { [weak self] result in
            if result != nil { self?.info = "旧发送账本已合并，去重记录保留；自动回复恢复仅提醒" }
        }
    }
    func restoreBackup() {
        request("restore_backup", ["path": restorePath, "confirmed_account": Int64(state.number("account")), "digest": restorePreview.string("digest"), "password": restorePassword]) { [weak self] result in
            guard let self else { return }
            restorePassword = ""
            guard result != nil else { return }
            configurationRevision += 1
            info = "恢复完成，记录和发送账本已合并；自动回复与在线策略已关闭，请核对设置"; search()
        }
    }
    func diagnosticPreview() {
        request("diagnostics") { [weak self] result in
            guard let result, let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) else { return }
            self?.diagnostics = String(decoding: data, as: UTF8.self)
            self?.showDiagnostics = true
        }
    }
    func exportDiagnostics() {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "即讯诊断.json"
        if panel.runModal() == .OK, let url = panel.url {
            do { try diagnostics.write(to: url, atomically: true, encoding: .utf8); info = "诊断已保存" }
            catch { self.error = error.localizedDescription }
        }
    }

    func updateNotificationStatus() {
        UNUserNotificationCenter.current().getNotificationSettings { [weak self] result in
            Task { @MainActor in
                self?.notificationStatus = result.authorizationStatus == .authorized ? "已允许系统通知" : result.authorizationStatus == .denied ? "系统通知已拒绝" : "尚未授权系统通知"
            }
        }
    }
    func testNotification() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { [weak self] allowed, _ in
            Task { @MainActor in
                self?.updateNotificationStatus()
                if allowed { self?.notify(title: "即讯", body: "测试通知：系统已接收请求", identifier: "test") }
                else { self?.error = "通知权限未开启，可前往系统设置 → 通知 → 即讯调整" }
            }
        }
    }
    func notify(title: String, body: String, identifier: String, sound: Bool = true, userInfo: [AnyHashable: Any] = [:]) {
        let content = UNMutableNotificationContent(); content.title = title; content.body = body; content.sound = sound ? .default : nil; content.userInfo = userInfo
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil)) { [weak self] error in
            Task { @MainActor in self?.info = error == nil ? "已提交系统通知，是否显示由系统决定" : "系统未接受通知请求" }
        }
    }
    func deliverReminder(_ reminder: Object, slot: String = "") {
        let targetSlot = slot.isEmpty ? selectedSlot : slot
        let accountState = accountStates[targetSlot] ?? (targetSlot == selectedSlot ? state : [:])
        let account = Int64(accountState.number("account"))
        guard account > 0, account == Int64(reminder.number("account")) else { return }
        let name = accountState.object("identity").string("name")
        if reminder["notify"] as? Bool ?? true {
            notify(title: name + " · " + reminder.string("title"), body: "\(Int(reminder.number("count"))) 条新消息 · \(reminder.string("body"))", identifier: UUID().uuidString,
                   sound: reminder["sound"] as? Bool ?? true,
                   userInfo: ["slot": targetSlot, "account": String(account), "chat": String(Int64(reminder.number("chat"))), "message_id": String(Int64(reminder.number("message_id")))])
        }
        if reminder.bool("voice") {
            let text = reminder.string("voice_text").isEmpty ? "\(reminder.string("title"))收到\(Int(reminder.number("count")))条新消息" : reminder.string("voice_text")
            enqueueVoice(name + "，" + text, slot: targetSlot, account: account)
        }
    }
    func testVoice() { enqueueVoice("即讯语音测试，收到新的消息", slot: selectedSlot, account: Int64(state.number("account"))) }
    func enqueueVoice(_ text: String, slot: String, account: Int64) {
        if voiceQueue.count >= 50 { let dropped = voiceQueue.removeFirst(); skippedVoice[dropped.3, default: 0] += 1 }
        voiceQueue.append((text, Date(), account, slot))
        request("voice_result", ["result": "queued", "account": account], slot: slot, background: true)
        nextVoice()
    }
    func cancelVoice(for slot: String) {
        voiceQueue.removeAll { $0.3 == slot }
        skippedVoice.removeValue(forKey: slot)
        if voiceAccounts.values.contains(where: { $0.1 == slot }) { speech.stopSpeaking(at: .immediate) }
    }
    func startVoice(_ utterance: AVSpeechUtterance, account: Int64, slot: String) {
        let identifier = ObjectIdentifier(utterance)
        voiceAccounts[identifier] = (account, slot)
        voiceStatus = "正在播报"
        utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
        speech.speak(utterance)
        request("voice_result", ["result": "playing", "account": account], slot: slot, background: true)
        Task {
            try? await Task.sleep(nanoseconds: 180_000_000_000)
            guard voiceAccounts.removeValue(forKey: identifier) != nil else { return }
            speech.stopSpeaking(at: .immediate)
            voiceStatus = "播报未完成，请测试系统语音"
            request("voice_result", ["result": "failed", "account": account], slot: slot, background: true)
            nextVoice()
        }
    }
    func nextVoice() {
        guard !speech.isSpeaking else { return }
        voiceQueue = voiceQueue.filter { item in
            let accountState = accountStates[item.3] ?? (item.3 == selectedSlot ? state : [:])
            guard Int64(accountState.number("account")) == item.2 && accountState.string("auth") == "ready" else { return false }
            if Date().timeIntervalSince(item.1) > 60 { skippedVoice[item.3, default: 0] += 1; return false }
            return true
        }
        if let slot = skippedVoice.keys.sorted().first {
            let count = skippedVoice.removeValue(forKey: slot) ?? 0
            let accountState = accountStates[slot] ?? (slot == selectedSlot ? state : [:])
            guard count > 0, accountState.string("auth") == "ready" else { nextVoice(); return }
            let account = Int64(accountState.number("account"))
            request("voice_result", ["result": "skipped", "account": account], slot: slot, background: true)
            startVoice(AVSpeechUtterance(string: accountState.object("identity").string("name") + "，\(count)条较早的消息提醒已合并，请查看活动记录"), account: account, slot: slot)
            return
        }
        guard !voiceQueue.isEmpty else { return }
        let item = voiceQueue.removeFirst()
        startVoice(AVSpeechUtterance(string: item.0), account: item.2, slot: item.3)
    }
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            if let (account, slot) = voiceAccounts.removeValue(forKey: ObjectIdentifier(utterance)) {
                voiceStatus = "播报完成"
                request("voice_result", ["result": "completed", "account": account], slot: slot, background: true)
            }
            nextVoice()
        }
    }
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in
            if let (account, slot) = voiceAccounts.removeValue(forKey: ObjectIdentifier(utterance)) {
                voiceStatus = "播报已取消"
                request("voice_result", ["result": "cancelled", "account": account], slot: slot, background: true)
            }
            nextVoice()
        }
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        let data = response.notification.request.content.userInfo
        Task { @MainActor in pendingNotification = data; showWindow(); flushPendingNotification() }
        completionHandler()
    }
    func openNotification(_ data: [AnyHashable: Any]) {
        guard let accountText = data["account"] as? String, let account = Int64(accountText), let chatText = data["chat"] as? String, let chat = Int64(chatText) else { selection = .overview; showWindow(); return }
        guard account == Int64(state.number("account")) else { error = "这条通知属于其他账号，请切换到对应账号后查看"; showWindow(); return }
        selection = .archive; searchChat = String(chat); query = ""; sender = ""; dateFilter = false
        showWindow(); search()
        contextGeneration += 1
        let generation = contextGeneration
        request("notification_context", ["chat": chat, "id": Int64(data["message_id"] as? String ?? "0") ?? 0]) { [weak self] result in
            guard let self, let result, generation == contextGeneration else { return }
            context = result.rows("messages")
            contextNote = result.string("source") == "remote" ? "只读查看通知对应的消息；未归档内容不会保存到本机。" : "通知对应的本地归档。未保存的内容需在原客户端查看。"
            showContext = true
        }
    }
    func flushPendingNotification() {
        guard !busy, let data = pendingNotification else { return }
        let slot = data["slot"] as? String ?? accounts.first(where: { String(Int64($0.number("account"))) == data["account"] as? String })?.string("slot") ?? selectedSlot
        guard accounts.isEmpty || accounts.contains(where: { $0.string("slot") == slot }) else {
            pendingNotification = nil; error = "通知对应的账号已不在账号列表中"; return
        }
        if slot != selectedSlot { selectAccount(slot); return }
        guard state.string("auth") == "ready" else { info = "请先恢复并确认通知对应账号的登录，然后自动打开消息"; return }
        pendingNotification = nil; openNotification(data)
    }

    func setLoginItem(_ enabled: Bool) {
        do {
            if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            launchAtLogin = SMAppService.mainApp.status == .enabled
        } catch { self.error = "无法修改登录启动项，请检查系统设置 → 通用 → 登录项" }
    }
    func setPreventSleep(_ enabled: Bool, persist: Bool = true) {
        if enabled {
            let result = IOPMAssertionCreateWithName(kIOPMAssertionTypeNoIdleSleep as CFString, IOPMAssertionLevel(kIOPMAssertionLevelOn), "即讯在运行" as CFString, &powerAssertion)
            preventSleep = result == kIOReturnSuccess
        } else {
            if powerAssertion != 0 { IOPMAssertionRelease(powerAssertion); powerAssertion = 0 }
            preventSleep = false
        }
        if persist { UserDefaults.standard.set(preventSleep, forKey: "preventIdleSleep") }
    }
    func logout() {
        request("logout") { [weak self] result in
            self?.phone = ""; self?.code = ""; self?.password = ""
            self?.results = []
            if result?.bool("revoked") == true { self?.info = "已退出账号，归档仍保留在本地" }
        }
    }
    func forgetAPI() {
        guard canChangeAPI else { error = "请先退出当前账号，并等待连接操作结束后再清除配置"; return }
        do { try Keychain.clear(slot: selectedSlot) }
        catch { self.error = "钥匙串中的自定义配置无法清除，原配置未更改"; return }
        UserDefaults.standard.removeObject(forKey: preferenceKey("apiID"))
        UserDefaults.standard.set(TelegramAPISource.builtIn.rawValue, forKey: preferenceKey("apiSource"))
        customAPI = nil; apiSource = .builtIn
        phone = ""; code = ""; password = ""
        error = ""
        info = "已清除自定义 API，恢复产品内置配置；已有归档保留"
    }
    func showWindow() {
        openMain?()
        NSApp.activate(ignoringOtherApps: true)
        if let window = NSApp.windows.first(where: { $0.identifier?.rawValue.contains("main") == true || $0.title == "即讯" }) {
            window.makeKeyAndOrderFront(nil)
        }
    }
    func shutdown(_ completion: @escaping () -> Void) {
        quitting = true
        speech.stopSpeaking(at: .immediate)
        setPreventSleep(false, persist: false)
        request("shutdown")
        let process = link.process
        Task {
            for _ in 0..<50 {
                if process?.isRunning != true { break }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
            link.stop()
            completion()
        }
    }
}
