"""升级回退快照：独立只读连接复制SQLite，不改变工作账号和发送意图。"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time


def create(root, destination, expected_slots):
    root, destination = Path(root).resolve(), Path(destination)
    if destination.exists() or destination.resolve().is_relative_to(root): raise ValueError('升级备份应为资料目录之外的新目录')
    registry = (root/'accounts.json').read_bytes() if (root/'accounts.json').exists() else b'{"slots":["legacy"],"active":"legacy"}'
    if json.loads(registry)['slots'] != expected_slots: raise ValueError('账号入口已变化，请重新核对升级清单')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix='.jixun-upgrade-', dir=destination.parent))
    try:
        files = list(root.rglob('*'))
        required = sum(f.stat().st_size for f in files if f.is_file() and not f.is_symlink())
        if required*2 + 100*1024*1024 > shutil.disk_usage(temporary).free: raise ValueError('空间不足，未准备升级')
        for file in files:
            if file.is_symlink(): raise ValueError('资料目录含链接，请先核对，升级未执行')
            if not file.is_file() or file.name in ('runtime-status.json', 'engine.lock') or file.name.endswith(('-wal','-shm','-journal')): continue
            target = temporary/'data'/file.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if file.suffix == '.sqlite' or file.name == 'telegram.session':
                source = sqlite3.connect(file.as_uri()+'?mode=ro',uri=True);backup=sqlite3.connect(target)
                try:
                    source.backup(backup)
                    if backup.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('资料完整性检查未通过，升级未执行')
                finally: backup.close();source.close()
            else: shutil.copyfile(file,target)
            os.chmod(target,0o600)
        if (root/'accounts.json').exists() and (root/'accounts.json').read_bytes()!=registry: raise ValueError('备份期间入口变化，请重新准备')
        (temporary/'data').mkdir(exist_ok=True,mode=0o700)
        (temporary/'data/accounts.json').write_bytes(registry);os.chmod(temporary/'data/accounts.json',0o600)
        (temporary/'upgrade.json').write_text(json.dumps({'created':time.time(),'entries':len(expected_slots),'contains_login_sessions':True,'note':'本机完整回退副本；不用于分享，自动回复需在恢复后确认'},ensure_ascii=False))
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary,ignore_errors=True)
        raise
    return {'backup_root':str(destination),'account_entries':len(expected_slots),'contains_login_sessions':True}
