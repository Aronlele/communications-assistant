"""生成同版本发布附件和校验清单，不连接 GitHub。"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import zipfile


parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--verification-report', type=Path)
parser.add_argument('--observation-summary', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
expected = plistlib.loads((root / 'macos/Info.plist').read_bytes())
info = plistlib.loads((args.source / '通讯助手.app/Contents/Info.plist').read_bytes())
for key in ('CFBundleShortVersionString', 'CFBundleVersion'): assert info[key] == expected[key], '包与源码版本不一致'
version = info['CFBundleShortVersionString']
out = root / 'output/releases' / ('v' + version)
out.mkdir(parents=True, exist_ok=True)
names = []
for kind in ('pkg', 'dmg', 'zip'):
    name = f'CommunicationsAssistant-{version}-macOS-arm64.{kind}'
    origin = args.source / (name if kind == 'pkg' else f'通讯助手-{version}-arm64.{kind}')
    assert origin.is_file()
    if kind == 'zip':
        with zipfile.ZipFile(origin) as archive: assert archive.testzip() is None
    shutil.copy2(origin, out / name); names.append(name)
name = 'site-preview.zip'
with zipfile.ZipFile(out / name, 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in sorted((root / 'docs').rglob('*')):
        if file.is_file(): archive.write(file, str(file.relative_to(root / 'docs')))
names.append(name)
name = f'UserGuide-{version}.zh-CN.md'
shutil.copy2(root / 'macos/README.md', out / name)
names.append(name)
(out / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256((out / name).read_bytes()).hexdigest() + '  ' + name + '\n' for name in names))
metadata = {'version': version, 'build': info['CFBundleVersion'], 'architecture': 'arm64',
            'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
            'built_in_configuration_present': (args.source / '通讯助手.app/Contents/Resources/TelegramAPI.json').is_file(),
            'signature': 'ad-hoc internal testing; not notarized',
            'installer_signature': 'unsigned test installer; not notarized', 'assets': names,
            'bundled_help': 'build-time snapshot; current guide is included as a release attachment',
            'stability_72h': {'status': 'not_verified'}}
if args.verification_report:
    report = json.loads(args.verification_report.read_text())
    assert report['version'] == version and report['build'] == info['CFBundleVersion']
    metadata['verification'] = {key: report['checks'][key] for key in (
        'python_tests', 'native_state_checks', 'isolated_ui', 'read_only_migration_copy',
        'packaged_engine', 'pkg_payload', 'dmg_integrity', 'zip_integrity')}
    metadata['real_account_upgrade'] = report.get('real_account_upgrade', 'not_verified')
if args.observation_summary:
    report = json.loads(args.observation_summary.read_text())
    metadata['stability_72h'] = {
        'status': 'awaiting_review' if report['complete'] else 'under_observation',
        'started': report['started'], 'planned_end': report['planned_end'],
        'note': 'Elapsed time alone does not establish acceptance; interrupted older versions are excluded.'}
(out / 'release-metadata.json').write_text(json.dumps(metadata, indent=2))
print(out)
