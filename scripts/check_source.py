"""只输出违规路径，不输出匹配的敏感值；检查 Git 跟踪边界。"""
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

BLOCKED_DIRS = {'.git', '.venv', 'venv', '__pycache__', 'Private', 'chatlog', 'data', 'output', 'build', '.local-source-import'}
SECRET = re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|\d{6,}:[A-Za-z0-9_-]{30,})\b')


def violations(name, content, known_secrets=(), scan_text=True):
    path = Path(name)
    wrong = (any(part in BLOCKED_DIRS for part in path.parts) or path.name == 'TelegramAPI.json' or
             (path.name.startswith('.env') and path.name != '.env.example') or
             re.search(r'\.(session|sqlite|db|tgabackup)(\.|-|$)', path.name) or
             path.suffix in ('.pyc', '.log') or path.as_posix() == 'macos/ACCEPTANCE.md')
    return bool(wrong or (scan_text and SECRET.search(content)) or any(value and value in content for value in known_secrets))


def main():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True, check=True)
    names = [x.decode() for x in result.stdout.split(b'\0') if x]
    if not names:
        raise SystemExit('请先将计划提交的源码加入暂存区再检查')
    known = []
    config = root / 'macos/Private/TelegramAPI.json'
    if config.exists():
        value = json.loads(config.read_text()).get('api_hash', '')
        if value: known.append(value.encode())
    bad = []
    for name in names:
        file = root / name
        if file.is_symlink() or not file.is_file():
            bad.append(name); continue
        binary = file.suffix.lower() in ('.jpg', '.png', '.pdf', '.docx', '.icns')
        contents = [(file.read_bytes(), not binary)]
        if file.suffix == '.docx':
            with zipfile.ZipFile(file) as archive:
                contents.extend((archive.read(part), True) for part in archive.namelist() if part.endswith('.xml'))
        if any(violations(name, content, known, scan) for content, scan in contents): bad.append(name)
    if bad:
        print('拒绝提交的文件：' + ', '.join(bad))
        raise SystemExit(1)
    print(f'源码边界检查通过：{len(names)} 个文件，无会话、资料或已知凭据')


if __name__ == '__main__': main()
