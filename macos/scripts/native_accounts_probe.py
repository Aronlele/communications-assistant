"""隔离原生状态测试：不启动引擎，不读真实数据，不提交系统通知。"""
import plistlib
from pathlib import Path
import subprocess
import tempfile


def main():
    project = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='tga-native-accounts-') as folder:
        root = Path(folder)
        app = root / 'AccountsProbe.app'
        binary = app / 'Contents/MacOS/AccountsProbe'
        binary.parent.mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.communications.assistant.accountsprobe',
            'CFBundleExecutable': 'AccountsProbe', 'CFBundlePackageType': 'APPL'}))
        model = (project / 'macos/Sources/Model.swift').read_text()
        constructor = '        startEngine()\n        updateNotificationStatus()'
        if model.count(constructor) != 1:
            raise RuntimeError('请核对原生构造器的隔离方式')
        model = model.replace(constructor, '        updateNotificationStatus()', 1)
        model = model.replace('    @Published var state: Object = [:]', '    var probeRequests: [Object] = []\n    var probeNotices: [Object] = []\n    @Published var state: Object = [:]', 1)
        send = 'try link.send(["protocol": 1, "id": id, "slot": targetSlot, "method": method, "args": args])'
        if model.count(send) != 1:
            raise RuntimeError('请核对原生请求的隔离方式')
        model = model.replace(send, 'probeRequests.append(["protocol": 1, "id": id, "slot": targetSlot, "method": method, "args": args])', 1)
        start = model.index('        let content = UNMutableNotificationContent(); content.title = title; content.body = body; content.sound = sound ? .default : nil; content.userInfo = userInfo', model.index('    func notify('))
        end = model.index('\n    func deliverReminder(', start)
        model = model[:start] + '        probeNotices.append(["title": title, "body": body, "userInfo": userInfo])\n    }\n' + model[end:]
        fixture = root / 'Model.swift'; fixture.write_text(model)
        subprocess.run(['xcrun', 'swiftc', '-swift-version', '5',
                        str(project / 'macos/Sources/APIConfiguration.swift'), str(project / 'macos/Sources/ProductServices.swift'), str(project / 'macos/Sources/ProductReadinessViews.swift'), str(fixture),
                        str(project / 'macos/Sources/Views.swift'), str(project / 'macos/Sources/WorkbenchViews.swift'), str(project / 'macos/Sources/TaskViews.swift'), str(project / 'macos/tests/AccountsProbe.swift'), '-o', str(binary)], check=True)
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
        subprocess.run([str(binary), '--demo'], check=True)


if __name__ == '__main__':
    main()
