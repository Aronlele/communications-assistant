"""生成同版本发布附件和校验清单，不连接 GitHub。"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import zipfile


parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
expected = plistlib.loads((root / 'macos/Info.plist').read_bytes())
info = plistlib.loads((args.source / '通讯助手.app/Contents/Info.plist').read_bytes())
for key in ('CFBundleShortVersionString', 'CFBundleVersion'): assert info[key] == expected[key], '包与源码版本不一致'
version = info['CFBundleShortVersionString']
out = root / 'output/releases' / ('v' + version)
out.mkdir(parents=True, exist_ok=True)
names = []
for kind in ('dmg', 'zip'):
    name = f'CommunicationsAssistant-{version}-macOS-arm64.{kind}'
    origin = args.source / f'通讯助手-{version}-arm64.{kind}'
    assert origin.is_file()
    if kind == 'zip':
        with zipfile.ZipFile(origin) as archive: assert archive.testzip() is None
    shutil.copy2(origin, out / name); names.append(name)
name = 'site-preview.zip'
with zipfile.ZipFile(out / name, 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in sorted((root / 'docs').rglob('*')):
        if file.is_file(): archive.write(file, str(file.relative_to(root / 'docs')))
names.append(name)
(out / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256((out / name).read_bytes()).hexdigest() + '  ' + name + '\n' for name in names))
metadata = {'version': version, 'build': info['CFBundleVersion'], 'architecture': 'arm64',
            'built_in_configuration_present': (args.source / '通讯助手.app/Contents/Resources/TelegramAPI.json').is_file(),
            'signature': 'ad-hoc internal testing; not notarized', 'assets': names}
(out / 'release-metadata.json').write_text(json.dumps(metadata, indent=2))
print(out)
