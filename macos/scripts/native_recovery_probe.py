"""在临时原生应用中测试恢复状态；跳过引擎启动，不触及真实账号或演示数据库。"""
import plistlib
from pathlib import Path
import subprocess
import tempfile


def main():
    project = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='tga-native-recovery-') as folder:
        root = Path(folder)
        app = root / 'RecoveryProbe.app'
        binary = app / 'Contents/MacOS/RecoveryProbe'
        binary.parent.mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.communications.assistant.recoveryprobe',
            'CFBundleExecutable': 'RecoveryProbe', 'CFBundlePackageType': 'APPL'}))
        model = (project / 'macos/Sources/Model.swift').read_text()
        constructor = '        startEngine()\n        updateNotificationStatus()'
        if model.count(constructor) != 1:
            raise RuntimeError('原生构造器已变化，请核对探针的隔离方式')
        fixture = root / 'Model.swift'
        fixture.write_text(model.replace(constructor, '        updateNotificationStatus()', 1))
        subprocess.run(['xcrun', 'swiftc', '-swift-version', '5',
                        str(project / 'macos/Sources/APIConfiguration.swift'), str(project / 'macos/Sources/ProductServices.swift'), str(project / 'macos/Sources/ProductReadinessViews.swift'), str(fixture),
                        str(project / 'macos/Sources/Views.swift'), str(project / 'macos/Sources/WorkbenchViews.swift'), str(project / 'macos/Sources/TaskViews.swift'),
                        str(project / 'macos/tests/RecoveryProbe.swift'), '-o', str(binary)], check=True)
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
        subprocess.run([str(binary), '--demo'], check=True)


if __name__ == '__main__': main()
