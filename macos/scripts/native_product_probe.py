"""隔离模拟更新准备：无工作引擎、无钥匙串/偏好读取、不打开真实安装器或退出应用。"""
from pathlib import Path
import plistlib
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='jixun-product-probe-') as temporary:
        folder = Path(temporary);app=folder/'ProductProbe.app';binary=app/'Contents/MacOS/ProductProbe';binary.parent.mkdir(parents=True)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.communications.assistant.productprobe','CFBundleExecutable':'ProductProbe','CFBundlePackageType':'APPL'}))
        source=(root/'macos/Sources/Model.swift').read_text()
        source=source.replace('        startEngine()\n        updateNotificationStatus()','        updateNotificationStatus()',1)
        source=source.replace('    private var link = EngineLink()', '    var probeRequests: [Object] = []\n    var probeInstallers: [URL] = []\n    var probeShouldOpen = false\n    var probeTerminations = 0\n    func probeOpen(_ url: URL) -> Bool { probeInstallers.append(url); return probeShouldOpen }\n    private var link = EngineLink()',1)
        needle='try link.send(["protocol": 1, "id": id, "slot": targetSlot, "method": method, "args": args])'
        assert needle in source
        source=source.replace(needle,'probeRequests.append(["id": id, "slot": targetSlot, "method": method, "args": args])',1)
        model=folder/'Model.swift';model.write_text(source)
        services=(root/'macos/Sources/ProductServices.swift').read_text()
        services=services.replace('Bundle.main.bundleIdentifier == "local.communications.assistant"','true')
        services=services.replace('UserDefaults.standard.persistentDomain(forName: "local.communications.assistant") ?? [:]','[String: Any]()')
        services=services.replace('NSWorkspace.shared.open(file)','self.probeOpen(file)').replace('NSApp.terminate(nil)','self.probeTerminations += 1')
        fixture=folder/'ProductServices.swift';fixture.write_text(services)
        names=['APIConfiguration.swift','ProductReadinessViews.swift','Views.swift','WorkbenchViews.swift','TaskViews.swift']
        subprocess.run(['xcrun','swiftc','-swift-version','5',str(model),str(fixture),*[str(root/'macos/Sources'/n) for n in names],str(root/'macos/tests/ProductProbe.swift'),'-o',str(binary)],check=True)
        subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
        subprocess.run([str(binary),'--demo'],check=True)


if __name__=='__main__':main()
