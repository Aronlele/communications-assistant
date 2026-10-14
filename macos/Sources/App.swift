import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var model: AppModel?
    private var closeObserver: NSObjectProtocol?
    func applicationDidFinishLaunching(_ notification: Notification) {
        closeObserver = NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: nil, queue: .main) { [weak self] notification in
            Task { @MainActor in
                guard let self, let window = notification.object as? NSWindow, window.title == "通讯助手",
                      let model = self.model, !model.isShuttingDown, !UserDefaults.standard.bool(forKey: "windowCloseExplained") else { return }
                UserDefaults.standard.set(true, forKey: "windowCloseExplained")
                let alert = NSAlert(); alert.messageText = "通讯助手会继续运行"
                alert.informativeText = "关闭窗口后，已开启的功能仍在菜单栏运行。点击纸飞机图标可以暂停或打开窗口；按 ⌘Q 或选择退出才会停止工作。此说明只显示一次。"
                alert.addButton(withTitle: "知道了"); alert.runModal()
            }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        model?.showWindow()
        return true
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model else { return .terminateNow }
        model.shutdown { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}

@main
struct CommunicationsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()
    var body: some Scene {
        Window("通讯助手", id: "main") {
            MainView().environmentObject(model).onAppear { delegate.model = model }
        }
        .defaultSize(width: 1120, height: 760)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(replacing: .appSettings) {
                Button("设置…") { model.selection = .settings; model.showWindow() }.keyboardShortcut(",")
            }
            CommandMenu("功能") {
                Button("搜索聊天归档") { model.selection = .archive; model.searchFocusRequest += 1; model.showWindow() }.keyboardShortcut("f", modifiers: .command)
                ForEach(Array(Destination.allCases.enumerated()), id: \.element) { item in
                    Button(item.element.rawValue) { model.selection = item.element; model.showWindow() }
                        .keyboardShortcut(KeyEquivalent(Character(String(item.offset + 1))), modifiers: .command)
                }
            }
        }
        MenuBarExtra("通讯助手", systemImage: "paperplane") { MenuView().environmentObject(model) }
            .menuBarExtraStyle(.window)
    }
}
