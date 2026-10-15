"""只读快照现有目录，在临时副本验证迁移；不复制会话、不连接网络或引擎。"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from engine.accounts import AccountManager
from engine.tasks import rules

TABLES = ('accounts', 'settings', 'conversations', 'messages', 'executions', 'prompts')


def verify(root):
    registry_path = root / 'accounts.json'
    registry_bytes = registry_path.read_bytes() if registry_path.exists() else None
    registry = json.loads(registry_bytes) if registry_bytes else {'slots': ['legacy'], 'active': 'legacy'}
    with tempfile.TemporaryDirectory(prefix='tga-upgrade-check-') as folder:
        target = Path(folder)
        if registry_bytes:
            (target / 'accounts.json').write_bytes(registry_bytes); os.chmod(target / 'accounts.json', 0o600)
        before = {}
        for slot in registry['slots']:
            relative = Path('.') if slot == 'legacy' else Path('accounts') / slot
            source_path = (root / relative / 'assistant.sqlite').resolve()
            if not source_path.is_relative_to(root.resolve()): raise ValueError('账号路径越界')
            destination = target / relative
            destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
            source.row_factory = sqlite3.Row
            snapshot = sqlite3.connect(destination / 'assistant.sqlite')
            try:
                source.backup(snapshot)
            finally:
                snapshot.close(); source.close()
            snapshot = sqlite3.connect(destination / 'assistant.sqlite')
            try:
                assert snapshot.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                before[slot] = {
                    'counts': {table: snapshot.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in TABLES},
                    'settings': {row[0]: json.loads(row[1]) for row in snapshot.execute('SELECT account,value FROM settings')},
                }
            finally: snapshot.close()
        manager = AccountManager(target)
        checks = []
        try:
            assert list(manager.services) == registry['slots'] and manager.active == registry['active']
            for slot, service in manager.services.items():
                previous = before[slot]
                assert service.store.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                for table, count in previous['counts'].items():
                    assert service.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == count
                pending = []
                for account, old in previous['settings'].items():
                    new = service.store.settings(account)
                    for key, value in old.items():
                        expected = json.loads(json.dumps(value))
                        if account == service.account and key == 'rule': expected.update(automatic=False, armed_at=0)
                        if account == service.account and key == 'tasks':
                            for rule in expected: rule.update(automatic=False, armed_at=0)
                        if key == 'resume_tasks': continue
                        assert new[key] == expected if key != 'rule' else all(new[key].get(field) == val for field, val in expected.items())
                    if account == service.account:
                        expected_ids = {rule['id'] for rule in rules(dict(new, rule=old['rule'], tasks=old.get('tasks', []))) if rule.get('automatic')}
                        assert expected_ids.issubset(set(new['resume_tasks']))
                        pending.extend(expected_ids)
                checks.append({'database_integrity': 'passed', 'all_table_counts_preserved': True,
                               'existing_settings_preserved': True, 'enabled_task_intents_retained': len(pending),
                               'new_engine_sends_disabled_until_confirmation': True})
        finally:
            for service in manager.services.values(): service.store.close()
        if registry_path.exists() and registry_path.read_bytes() != registry_bytes:
            raise RuntimeError('检查期间账号目录变化，请重新验证')
    return {'scope': '只读快照及临时副本，不接触 Telegram 会话、不连接第二个引擎',
            'account_entries': len(checks), 'registry_and_active_entry_preserved': True, 'checks': checks}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); report = verify(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__': main()
