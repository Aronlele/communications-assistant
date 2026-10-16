"""展开实际 PKG，检查安装范围和载荷；不执行安装或修改系统。"""
import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def contents(root):
    return {str(p.relative_to(root)): ('link', os.readlink(p)) if p.is_symlink() else
            ('file', hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_symlink() or p.is_file()}


def validate_public_permissions(root):
    for path in [root, *root.rglob('*')]:
        if path.is_symlink(): continue
        mode = stat.S_IMODE(path.stat().st_mode)
        required = 0o5 if path.is_dir() or mode & stat.S_IXUSR else 0o4
        assert mode & required == required, f'安装为管理员所有后普通用户无法读取或运行：{path.relative_to(root)}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    parser.add_argument('app', type=Path)
    args = parser.parse_args()
    info = plistlib.loads((args.app / 'Contents/Info.plist').read_bytes())
    with tempfile.TemporaryDirectory(prefix='tga-pkg-check-') as folder:
        root = Path(folder) / 'expanded'
        subprocess.run(['pkgutil', '--expand-full', str(args.package), str(root)], check=True)
        assert sorted(p.name for p in root.iterdir()) == ['Distribution', 'Resources', 'assistant.pkg']
        package = ET.parse(root / 'assistant.pkg/PackageInfo').getroot()
        assert package.get('install-location') == '/Applications'
        assert package.get('identifier') == 'local.communications.assistant.pkg'
        assert package.get('version') == info['CFBundleShortVersionString']
        assert package.get('relocatable') == 'false'
        assert package.find('scripts') is None and not (root / 'assistant.pkg/Scripts').exists()
        assert package.find('upgrade-bundle/bundle').get('id') == info['CFBundleIdentifier']
        xml = ET.parse(root / 'Distribution').getroot()
        assert xml.find('options').get('hostArchitectures') == 'arm64'
        assert xml.find('.//os-version').get('min') == info['LSMinimumSystemVersion']
        assert xml.find('.//must-close/app').get('id') == info['CFBundleIdentifier']
        assert xml.find('domains').get('enable_localSystem') == 'true'
        assert xml.find('domains').get('enable_currentUserHome') == 'false'
        payload = root / 'assistant.pkg/Payload'
        assert [p.name for p in payload.iterdir()] == ['通讯助手.app']
        app = payload / '通讯助手.app'
        validate_public_permissions(app)
        assert contents(app) == contents(args.app), 'PKG 载荷与已验收的应用不一致'
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        assert subprocess.check_output(['lipo', '-archs', str(app / 'Contents/MacOS/CommunicationsAssistant')], text=True).strip() == 'arm64'
        for tag in ('welcome', 'readme', 'conclusion'):
            resource = root / 'Resources' / xml.find(tag).get('file')
            assert resource.is_file() and info['CFBundleDisplayName'] in resource.read_text()
    subprocess.run(['installer', '-showChoicesXML', '-pkg', str(args.package), '-target', '/'], check=True, stdout=subprocess.DEVNULL)
    print('PKG 检查通过：固定应用程序目录、架构与系统要求、退出提示、无安装脚本；载荷及签名与原应用一致。')
    print('本检查不执行安装，不代替签名公证或干净 Mac 的完整安装验收。')


if __name__ == '__main__': main()
