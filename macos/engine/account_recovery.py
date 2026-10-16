"""只读发现移除入口保留的资料；确认后挂回原目录，不合并身份或开启发送。"""
import hashlib
from pathlib import Path
import sqlite3
import uuid


def location(root, slot):
    root = Path(root).resolve()
    if slot == 'legacy': return root
    if not isinstance(slot, str) or str(uuid.UUID(slot)) != slot: raise ValueError('资料入口标识无效')
    path = root / 'accounts' / slot
    if path.is_symlink() or (root/'accounts').is_symlink() or path.resolve().parent != root/'accounts':
        raise ValueError('资料目录越界，未执行恢复')
    return path


def inspect(root, slot):
    try: return _inspect(root, slot)
    except (sqlite3.Error, OSError):
        raise ValueError("保留资料无法读取或已损坏，现有账号未改动") from None


def _inspect(root, slot):
    path = location(root, slot); database = path/'assistant.sqlite'
    if database.is_symlink() or not database.is_file(): raise ValueError('没有可恢复的数据库')
    connection = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
    try:
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('本地资料损坏，请先保留副本')
        if connection.execute('PRAGMA user_version').fetchone()[0] not in (1,2,3,4,5): raise ValueError('资料版本未知或较新，请核对应用版本')
        accounts = list(connection.execute('SELECT id,name FROM accounts'))
        if len(accounts) != 1: raise ValueError('此入口为空或包含多个身份，不能直接恢复')
        account, name = accounts[0]
        counts = {table: connection.execute(f'SELECT count(*) FROM {table} WHERE account=?', (account,)).fetchone()[0]
                  for table in ('messages', 'executions')}
    finally: connection.close()
    digest = hashlib.sha256()
    for file in (database, Path(str(database)+'-wal')):
        if file.exists():
            if file.is_symlink(): raise ValueError('资料文件路径无效')
            with file.open('rb') as stream: digest.update(hashlib.file_digest(stream, 'sha256').digest())
    return {'slot': slot, 'account': account, 'name': name, 'counts': counts, 'digest': digest.hexdigest()}


def retained(manager):
    slots = ['legacy']
    parent = manager.root/'accounts'
    if parent.exists() and not parent.is_symlink():
        for path in sorted(parent.iterdir()):
            try:
                if str(uuid.UUID(path.name)) == path.name: slots.append(path.name)
            except ValueError: pass
    rows = []
    for slot in slots:
        if slot in manager.services: continue
        try:
            item = inspect(manager.root, slot)
            item['duplicate'] = any(item['account'] in (s.account, s.identity.get('id')) for s in manager.services.values())
            rows.append(item)
        except (ValueError, OSError, sqlite3.Error):
            candidate = manager.root if slot == 'legacy' else manager.root/'accounts'/slot
            if candidate.is_symlink() or (candidate/'assistant.sqlite').is_file():
                rows.append({'slot': slot, 'name': '不可直接恢复的保留资料', 'unavailable': True, 'detail': '入口为空、文件损坏或版本不兼容；原资料未改动。'})
    return rows
