"""用户主动生成的单账号本地备份，不包含 Telegram 登录会话或 API 凭据。"""
import json
import hashlib
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from engine.profile import validate_profile
from engine.schedule import validate
from engine.store import DEFAULTS, Store
from engine.reply_rule import validate_template
from engine.tasks import disarm_all
from engine.task_templates import validate_cases

TABLES = ('accounts', 'settings', 'conversations', 'messages', 'executions', 'activities', 'ranges', 'prompts', 'archive_deletions')


def safe_settings(value):
    if not isinstance(value, dict): raise ValueError('备份设置必须为对象')
    value = json.loads(json.dumps(value))
    result = json.loads(json.dumps(DEFAULTS))
    for key in DEFAULTS:
        if key in value: result[key] = value[key]
    if not isinstance(result['rule'], dict): raise ValueError('备份规则无效')
    result['rule'] = {key: result['rule'].get(key, default) for key, default in DEFAULTS['rule'].items()}
    result['rule']['sample_cases'] = validate_cases(result['rule'].get('sample_cases', []))
    result['rule'].update(automatic=False, armed_at=0)
    result['rule'].update(validate_template(result['rule']))
    if not isinstance(result['tasks'], list) or len(result['tasks']) > 50:
        raise ValueError('备份中的任务列表无效')
    identifiers = set()
    for rule in result['tasks']:
        if not isinstance(rule, dict) or not isinstance(rule.get('id'), str) or not rule['id'] or rule['id'] == 'legacy' or rule['id'] in identifiers:
            raise ValueError('备份中的任务标识无效或重复')
        identifiers.add(rule['id'])
        for key, default in DEFAULTS['rule'].items():
            if key not in rule: rule[key] = json.loads(json.dumps(default))
        rule.update(validate_template(rule), sample_cases=validate_cases(rule.get('sample_cases', [])), automatic=False, armed_at=0)
        if type(rule['target']) is not int or rule['target'] <= 0:
            raise ValueError('备份中的任务对象无效')
        validate(rule['sending_weekly'], rule['sending_timezone'])
    disarm_all(result)
    for rule in [result['rule'], *result['tasks']]:
        if not isinstance(rule.get('name'), str) or not 1 <= len(rule['name']) <= 60:
            raise ValueError('备份中的任务名称无效')
        if type(rule.get('priority')) is not int or not 0 <= rule['priority'] <= 100:
            raise ValueError('备份中的任务优先级无效')
        for key in ('preview_validated', 'allow_user_test', 'sending_limited'):
            if type(rule[key]) is not bool: raise ValueError('备份中的任务开关无效')
        rule['preview_validated'] = False
    result['online'] = False
    for prefix, timezone in (('weekly', 'timezone'), ('quiet_weekly', 'quiet_timezone')):
        validate(result[prefix], result[timezone])
    validate(result['rule']['sending_weekly'], result['rule']['sending_timezone'])
    for key, default in DEFAULTS.items():
        if isinstance(default, bool) and not isinstance(result[key], bool): raise ValueError('备份中的设置格式无效')
    if not isinstance(result['retention_days'], int) or not 1 <= result['retention_days'] <= 365:
        raise ValueError('备份中的保留期无效')
    if not isinstance(result['quota_mb'], int) or not 0 <= result['quota_mb'] <= 10240:
        raise ValueError('备份中的容量设置无效')
    if not isinstance(result['rule']['target'], int) or result['rule']['target'] < 0:
        raise ValueError('备份中的机器人身份无效')
    return result


def media_path(root, account, value):
    relative = PurePosixPath(value)
    if relative.is_absolute() or '..' in relative.parts or not value.startswith(f'media/{account}/'):
        raise ValueError('备份媒体路径无效')
    path = (Path(root) / value).resolve()
    if not path.is_relative_to((Path(root) / 'media' / str(account)).resolve()):
        raise ValueError('备份媒体路径越界')
    return path


def create_backup(root, account, destination):
    root, destination = Path(root), Path(destination)
    if destination.resolve().is_relative_to(root.resolve()):
        raise ValueError('备份请保存到应用数据目录之外')
    with tempfile.TemporaryDirectory(prefix='tga-backup-') as folder:
        snapshot = Store(folder)
        source = sqlite3.connect((root / 'assistant.sqlite').as_uri() + '?mode=ro', uri=True)
        source.row_factory = sqlite3.Row
        counts = {}
        try:
            source.execute('BEGIN')
            with snapshot.db:
                for table in TABLES:
                    field = 'id' if table == 'accounts' else 'account'
                    cursor = source.execute(f'SELECT * FROM {table} WHERE {field}=?', (account,))
                    columns = [item[0] for item in cursor.description]
                    statement = f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})"
                    counts[table] = 0
                    for batch in iter(lambda: cursor.fetchmany(500), []):
                        snapshot.db.executemany(statement, [tuple(row) for row in batch])
                        counts[table] += len(batch)
                snapshot.db.execute("UPDATE executions SET status='send_unknown' WHERE status='sending'")
                settings = safe_settings(snapshot.settings(account))
                snapshot.save_settings(account, settings)
            saved_profile = source.execute("SELECT value FROM preferences WHERE key='device_profile'").fetchone()
            profile = validate_profile(json.loads(saved_profile[0]) if saved_profile else {})
            media = [row[0] for row in snapshot.db.execute("SELECT DISTINCT media FROM messages WHERE account=? AND media<>'' AND deleted=0", (account,))]
            snapshot.db.execute('VACUUM')
        finally:
            source.close(); snapshot.close()
        descriptor, temporary = tempfile.mkstemp(prefix='.tga-backup-', dir=destination.parent)
        os.close(descriptor)
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json', json.dumps({'format': 'communications-assistant-backup', 'version': 1, 'schema': 5, 'account': account, 'counts': counts, 'device_profile': profile}, ensure_ascii=False))
                archive.write(Path(folder) / 'assistant.sqlite', 'account.sqlite')
                for value in media:
                    path = media_path(root, account, value)
                    if path.is_file(): archive.write(path, value)
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return counts


def inspect_backup(path, account, folder=None, metadata_only=False):
    if folder is None:
        with tempfile.TemporaryDirectory(prefix='tga-backup-preview-') as temporary:
            return inspect_backup(path, account, temporary, metadata_only=True)
    with zipfile.ZipFile(path) as archive:
        archive.fp.seek(0)
        digest = hashlib.file_digest(archive.fp, 'sha256').hexdigest()
        infos = archive.infolist()
        if len(infos) > 100000 or len({item.filename for item in infos}) != len(infos):
            raise ValueError('备份文件项目过多或包含重复路径')
        for item in infos:
            name = PurePosixPath(item.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in item.filename or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('备份包含不安全的文件路径')
            if item.filename not in ('manifest.json', 'account.sqlite') and not item.filename.startswith(f'media/{account}/'):
                raise ValueError('备份包含其他账号或不支持的文件')
        if archive.getinfo('manifest.json').file_size > 65536:
            raise ValueError('备份说明文件过大')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != 'communications-assistant-backup' or manifest.get('version') != 1 or manifest.get('schema') not in (3, 4, 5):
            raise ValueError('不支持此备份格式或版本')
        if manifest.get('account') != account:
            raise ValueError('备份属于其他 Telegram 账号，请先登录对应账号')
        size = sum(item.file_size for item in infos)
        if folder:
            if size + 50 * 1024 * 1024 > shutil.disk_usage(folder).free:
                raise ValueError('磁盘空间不足，不能安全恢复此备份')
            for item in infos:
                if metadata_only and item.filename not in ('manifest.json', 'account.sqlite'): continue
                destination = Path(folder) / item.filename
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(item) as source, destination.open('wb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                os.chmod(destination, 0o600)
    if manifest['schema'] in (3, 4):
        # 只迁移解包后的业务备份副本，旧文件与当前账号数据都不改写。
        with tempfile.TemporaryDirectory(prefix='tga-backup-migrate-') as migrated:
            shutil.copyfile(Path(folder) / 'account.sqlite', Path(migrated) / 'assistant.sqlite')
            upgraded = Store(migrated); upgraded.close()
            shutil.copyfile(Path(migrated) / 'assistant.sqlite', Path(folder) / 'account.sqlite')
        manifest['schema'] = 5
    database = sqlite3.connect((Path(folder) / 'account.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        database.execute('PRAGMA trusted_schema=OFF')
        tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(TABLES).issubset(tables): raise ValueError('备份数据库结构不完整')
        counts = {}
        for table in TABLES:
            field = 'id' if table == 'accounts' else 'account'
            if database.execute(f'SELECT 1 FROM {table} WHERE {field}<>? LIMIT 1', (account,)).fetchone():
                raise ValueError('备份数据库含其他账号的数据')
            counts[table] = database.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        cfg = database.execute('SELECT value FROM settings WHERE account=?', (account,)).fetchone()
        safe_settings(json.loads(cfg[0]) if cfg else {})
        validate_profile(manifest.get('device_profile', {}))
        manifest['counts'] = counts
    finally:
        database.close()
    return dict(manifest, estimated_bytes=size, digest=digest)


def restore_backup(store, account, folder, manifest):
    source = sqlite3.connect((Path(folder) / 'account.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
    source.row_factory = sqlite3.Row
    source.execute('PRAGMA trusted_schema=OFF')
    try:
        tables = {row['name'] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(TABLES).issubset(tables): raise ValueError('备份数据库结构不完整')
        for table in TABLES:
            field = 'id' if table == 'accounts' else 'account'
            if source.execute(f'SELECT 1 FROM {table} WHERE {field}<>? LIMIT 1', (account,)).fetchone():
                raise ValueError('备份数据库含其他账号的数据')
            expected = [row[1] for row in store.db.execute(f'PRAGMA table_info({table})')]
            actual = [row[1] for row in source.execute(f'PRAGMA table_info({table})')]
            if expected != actual: raise ValueError('备份数据库字段不兼容')
        cfg_row = source.execute('SELECT value FROM settings WHERE account=?', (account,)).fetchone()
        settings = safe_settings(json.loads(cfg_row[0]) if cfg_row else {})
        profile = validate_profile(manifest.get('device_profile', {}))
        media = source.execute("SELECT chat,id,media,media_uid FROM messages WHERE account=? AND media<>'' AND deleted=0", (account,)).fetchall()
        if manifest['estimated_bytes'] + 50 * 1024 * 1024 > shutil.disk_usage(store.root).free:
            raise ValueError('恢复空间不足')
        for row in media:
            media_path(folder, account, row['media']); media_path(store.root, account, row['media'])
        store.harden()
        copied = []
        try:
            for row in media:
                old = store.db.execute('SELECT media_uid FROM messages WHERE account=? AND chat=? AND id=?', (account, row['chat'], row['id'])).fetchone()
                if old and old['media_uid'] != row['media_uid']: continue
                destination = media_path(store.root, account, row['media'])
                origin = media_path(folder, account, row['media'])
                if destination.exists() or not origin.is_file(): continue
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(origin, destination); os.chmod(destination, 0o600); copied.append(destination)
            with store.db:
                for table in ('conversations', 'messages', 'executions', 'prompts', 'archive_deletions'):
                    cursor = source.execute(f'SELECT * FROM {table} WHERE account=?', (account,))
                    columns = [item[0] for item in cursor.description]
                    statement = f"INSERT OR IGNORE INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})"
                    for batch in iter(lambda: cursor.fetchmany(500), []):
                        store.db.executemany(statement, [tuple(row) for row in batch])
                for row in source.execute('SELECT * FROM archive_deletions WHERE account=?', (account,)):
                    store.db.execute('INSERT INTO archive_deletions VALUES(?,?,?,?,?) ON CONFLICT(account,chat,id) DO UPDATE SET date=MAX(date,excluded.date),media_only=MIN(media_only,excluded.media_only)', tuple(row))
                for row in source.execute("SELECT target,number FROM executions WHERE account=? AND status='confirmed'", (account,)):
                    store.db.execute("UPDATE executions SET status='confirmed' WHERE account=? AND target=? AND number=?", (account, row['target'], row['number']))
                store.db.execute("UPDATE executions SET status='send_unknown' WHERE account=? AND status='sending'", (account,))
                for row in media:
                    if not media_path(store.root, account, row['media']).is_file():
                        store.db.execute("UPDATE messages SET media='',media_state='failed',media_attempts=3,media_retry_at=0,media_error='MissingBackupMedia' WHERE account=? AND chat=? AND id=? AND media=?", (account, row['chat'], row['id'], row['media']))
                for table, columns in (('activities', ('account', 'date', 'kind', 'detail')), ('ranges', ('account', 'chat', 'start', 'end', 'kind', 'detail'))):
                    names = ','.join(columns)
                    for row in source.execute(f'SELECT {names} FROM {table} WHERE account=?', (account,)):
                        if not store.db.execute(f"SELECT 1 FROM {table} WHERE " + ' AND '.join(f'{key}=?' for key in columns) + ' LIMIT 1', tuple(row)).fetchone():
                            store.db.execute(f"INSERT INTO {table}({names}) VALUES({','.join('?' for _ in columns)})", tuple(row))
                for row in list(store.db.execute('SELECT * FROM messages WHERE account=?', (account,))):
                    if store.archive_excluded(account, row['chat'], row['id'], row['date']):
                        store.remove_media(row['media'])
                        store.db.execute('DELETE FROM messages WHERE account=? AND chat=? AND id=?', (account,row['chat'],row['id']))
                    elif store.archive_excluded(account, row['chat'], row['id'], row['date'], media=True):
                        store.remove_media(row['media'])
                        store.db.execute("UPDATE messages SET media='',media_state='cleared',media_retry_at=0 WHERE account=? AND chat=? AND id=?", (account,row['chat'],row['id']))
                store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (account, json.dumps(settings, ensure_ascii=False)))
                store.db.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)', ('device_profile', json.dumps(profile, ensure_ascii=False)))
                store.db.execute("INSERT INTO activities(account,date,kind,detail) VALUES(?,strftime('%s','now'),'restore',?)", (account, '用户确认恢复单账号备份，消息与账本合并；自动回复和在线策略已关闭'))
        except Exception:
            for path in copied: path.unlink(missing_ok=True)
            raise
        return manifest['counts']
    finally:
        source.close()
