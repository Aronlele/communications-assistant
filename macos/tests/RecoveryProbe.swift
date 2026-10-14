import AppKit
import Foundation

@main
struct RecoveryProbe {
    @MainActor static func main() {
        NSApplication.shared.setActivationPolicy(.prohibited)
        let model = AppModel()
        let recovering = "消息引擎意外退出，正在恢复；自动回复需重新启用。"
        func state(_ auth: String, error: String = "") -> Object {
            ["event": "state", "data": ["auth": auth, "account": 999001, "settings": [:], "error": error]]
        }
        model.error = recovering
        model.receive(state("starting"))
        guard model.error == recovering else { print("FAIL: 恢复完成前不应清除提示"); exit(1) }
        for auth in ["ready", "identity", "phone", "offline", "disconnected"] {
            model.error = recovering
            model.receive(state(auth))
            guard model.error.isEmpty, model.info.contains("已恢复") else {
                print("FAIL: 引擎恢复完成后仍显示正在恢复"); exit(1)
            }
        }
        model.error = "其他未处理错误"
        model.receive(state("ready"))
        guard model.error == "其他未处理错误" else { print("FAIL: 不应清除无关错误"); exit(1) }
        model.error = recovering
        model.receive(state("offline", error: "无法连接 Telegram"))
        guard model.error == "无法连接 Telegram" else { print("FAIL: 新的连接错误应保留"); exit(1) }
        model.info = "消息引擎已恢复，自动回复仍需手动开启。"
        model.engineExited()
        guard model.info.isEmpty else { print("FAIL: 再次故障时不应保留已恢复提示"); exit(1) }
        print("Native recovery state: 9 checks passed")
        exit(0)
    }
}
