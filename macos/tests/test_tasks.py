import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from engine.automation import Automation
from engine.reply_rule import parse_reply, template_from_sample, validate_template
from engine.service import Service
from engine.store import Store
from engine.tasks import rules, task_rule

SAMPLE = '🔔 查岗提醒（编号 00176）\n\n嗨，天枢 👋\n\n查岗小助手来啦～\n\n请在20分钟内只回复下面的关键词：\n\n香蕉\n\n回复时限说明：\n20分钟内回复：正常响应。\n20-30分钟内回复：超时回复。\n超过30分钟未正确回复：未响应。\n\n祝工作顺利 🌷'


def event(target, text, message_id=1):
    return SimpleNamespace(is_private=True, sender_id=target, chat_id=target,
                           message=SimpleNamespace(raw_text=text, id=message_id, date=datetime.now(timezone.utc),
                                                   out=False, fwd_from=None, via_bot_id=None))


class TemplateTests(unittest.TestCase):
    def test_user_sample_and_saved_custom_template(self):
        builtin = parse_reply(SAMPLE, {}, 1)
        self.assertEqual((builtin.number, builtin.keyword, builtin.window_minutes), ('00176', '香蕉', 20))
        template = template_from_sample(SAMPLE)
        self.assertIn('{number}', template); self.assertIn('{keyword}', template); self.assertIn('{minutes}', template)
        rule = validate_template({'prompt_template': template, 'sample_text': SAMPLE})
        changed = SAMPLE.replace('00176', '00177').replace('香蕉', '苹果')
        result = parse_reply(changed, rule, 2)
        self.assertEqual((result.number, result.keyword), ('00177', '苹果'))
        self.assertIsNone(parse_reply(changed.replace('查岗小助手来啦', '其他内容'), rule, 2))

    def test_custom_format_and_invalid_placeholders(self):
        template = '抽查 {number}\n请在{minutes}分钟内回复\n{keyword}'
        result = parse_reply('抽查 0003\n请在2分钟内回复\n在岗', {'prompt_template': template}, 1)
        self.assertEqual((result.number, result.keyword, result.window_minutes), ('0003', '在岗', 2))
        for value in ('{keyword}', '{number} {minutes} {keyword} {unknown}', '{number}\n{minutes}\n{keyword}\n{keyword}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_template({'prompt_template': value})
        self.assertIsNone(parse_reply('抽查 0003\n请在0分钟内回复\n在岗', {'prompt_template': template}, 1))


class MultiTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.service = Service(self.folder.name, demo=True)
        self.service.account = 1; self.service.auth = 'ready'
        self.send = AsyncMock(return_value=SimpleNamespace(id=88))
        self.runner = Automation(self.service.store, 1, self.service.settings, self.send, AsyncMock(), time.time()-10)

    async def asyncTearDown(self):
        self.service.store.close(); self.folder.cleanup()

    def seed(self, targets=(42, 43), overlap=False):
        cfg = self.service.settings()
        cfg['rule'].update(target=targets[0], name='原有任务', automatic=True, preview_validated=True,
                           armed_at=time.time()-10)
        cfg['tasks'] = [dict(cfg['rule'], id='task-two', target=targets[1], name='第二任务')]
        if overlap: cfg['tasks'][0].update(mode='fixed', match_text='查岗提醒', match_mode='contains', reply_text='1')
        self.service.store.save_settings(1, cfg)

    async def test_two_targets_and_receipts_independent(self):
        self.seed()
        await asyncio.gather(self.runner.handle(event(42, SAMPLE)), self.runner.handle(event(43, SAMPLE)))
        self.assertEqual(self.send.await_count, 2)
        await self.runner.handle(event(43, '✅ 按时响应\n编号 00176 · 已记录 · 感谢配合！', 2))
        rows = {r['target']: r for r in self.service.store.executions(1)}
        self.assertEqual(rows[43]['status'], 'confirmed'); self.assertEqual(rows[42]['status'], 'sent')
        self.assertEqual(rows[43]['task_id'], 'task-two')

    async def test_overlapping_rules_and_mode_change_never_repeat_source(self):
        self.seed((42, 42), overlap=True)
        await asyncio.gather(*(self.runner.handle(event(42, SAMPLE)) for _ in range(5)))
        self.send.assert_awaited_once_with(42, '香蕉')
        cfg = self.service.settings(); cfg['rule']['automatic'] = False
        self.service.store.save_settings(1, cfg)
        await self.runner.handle(event(42, SAMPLE))
        self.assertEqual(self.send.await_count, 1)
        self.assertEqual(self.service.store.results(1)['count'], 1)

    async def test_edit_one_preserves_other_and_sample_only_save_keeps_enabled(self):
        self.seed()
        await self.service.command('task_save', {'id': 'task-two', 'name': '第二任务', 'target': '43', 'mode': 'fixed',
                                                'match_text': '抽查', 'reply_text': '1', 'sample_text': '抽查'})
        cfg = self.service.settings()
        self.assertTrue(cfg['rule']['automatic']); self.assertFalse(task_rule(cfg, 'task-two')['automatic'])
        await self.service.command('task_save', dict(cfg['rule'], id='legacy', sample_text=SAMPLE))
        self.assertTrue(self.service.settings()['rule']['automatic'])
        await self.service.command('task_preview', {'id': 'task-two', 'text': '抽查'})
        self.assertEqual(task_rule(self.service.settings(), 'task-two')['sample_text'], '抽查')
        self.assertTrue(task_rule(self.service.settings(), 'task-two')['preview_validated'])

    async def test_delete_keeps_ledger_and_late_receipt(self):
        self.seed()
        await self.runner.handle(event(43, SAMPLE))
        await self.service.command('task_delete', {'id': 'task-two', 'confirmed_id': 'task-two'})
        await self.runner.handle(event(43, '✅ 按时响应\n编号 00176 · 已记录 · 感谢配合！', 2))
        self.assertEqual(self.service.store.executions(1)[0]['status'], 'confirmed')
        self.assertEqual(len(rules(self.service.settings())), 1)

    async def test_preview_is_not_send_and_invalid_save_atomic(self):
        self.seed()
        before = self.service.settings()
        with self.assertRaises(ValueError):
            await self.service.command('task_save', {'id': 'task-two', 'target': '43', 'mode': 'fixed', 'match_text': ''})
        self.assertEqual(before, self.service.settings())
        result = await self.service.command('task_preview', {'id': 'legacy', 'text': SAMPLE})
        self.assertEqual(result['keyword'], '香蕉'); self.send.assert_not_awaited()

    async def test_results_filter_isolated_and_overdue(self):
        self.seed()
        await self.runner.handle(event(42, SAMPLE))
        await self.runner.handle(event(43, SAMPLE))
        result = await self.service.command('task_results', {'task_id': 'task-two'})
        self.assertEqual(result['count'], 1); self.assertEqual(result['rows'][0]['target'], 43)
        self.assertEqual(self.service.store.results(2)['count'], 0)

    async def test_restart_retains_intent_but_does_not_send(self):
        self.seed()
        with self.service.store.db:
            self.service.store.db.execute('INSERT INTO accounts VALUES(1,?)', ('本地测试',))
        self.service.store.preference('last_account', 1)
        self.service.store.close()
        self.service = Service(self.folder.name, demo=True)
        # demo deliberately bypasses live login restoration; exercise the same preparation explicitly.
        self.service.account = 1
        self.service.prepare_recovery()
        cfg = self.service.settings()
        self.assertEqual(set(cfg['resume_tasks']), {'legacy', 'task-two'})
        self.assertTrue(all(not r['automatic'] for r in rules(cfg)))
        self.service.prepare_recovery()
        self.assertEqual(set(self.service.settings()['resume_tasks']), {'legacy', 'task-two'})
        self.send.assert_not_awaited()

    async def test_recovery_confirmation_atomic_and_preserves_pause(self):
        from engine.task_commands import revision
        self.seed()
        self.service.prepare_recovery()
        cfg = self.service.settings(); cfg['pause_sends'] = True
        self.service.store.save_settings(1, cfg)
        self.service.demo = False
        from telethon import types
        self.service.client = SimpleNamespace(is_connected=lambda: True,
                                              get_entity=AsyncMock(side_effect=lambda target: types.User(id=target, first_name='测试对象', bot=True)))
        args = {'ids': ['legacy', 'task-two'], 'confirmed_account': 1,
                'revisions': {rule['id']: revision(rule) for rule in rules(cfg)}}
        with self.assertRaises(ValueError):
            await self.service.command('task_resume', dict(args, confirmed_account=2))
        with self.assertRaises(ValueError):
            await self.service.command('task_resume', dict(args, revisions={'legacy': revision(cfg['rule'])}))
        self.assertTrue(all(not rule['automatic'] for rule in rules(self.service.settings())))
        await self.service.command('task_resume', args)
        self.assertTrue(all(rule['automatic'] for rule in rules(self.service.settings())))
        self.assertTrue(self.service.settings()['pause_sends'])
        self.assertEqual(self.service.settings()['resume_tasks'], [])
        self.send.assert_not_awaited()

    async def test_sample_saved_after_reopen_and_backup_restore(self):
        from engine.backup import create_backup, inspect_backup, restore_backup
        from pathlib import Path
        self.seed()
        await self.service.command('task_preview', {'id': 'legacy', 'text': SAMPLE})
        self.service.store.db.execute('INSERT INTO accounts VALUES(1,?)', ('演示',))
        self.service.store.db.commit()
        with tempfile.TemporaryDirectory() as destination:
            path = Path(destination) / 'backup.zip'
            create_backup(self.service.root, 1, path)
            folder = Path(destination) / 'unpacked'
            folder.mkdir()
            manifest = inspect_backup(path, 1, folder)
            restored = Store(Path(destination) / 'restored')
            restore_backup(restored, 1, folder, manifest)
            cfg = restored.settings(1)
            self.assertEqual(len(rules(cfg)), 2)
            self.assertEqual(cfg['rule']['sample_text'], SAMPLE)
            self.assertTrue(all(not rule['automatic'] for rule in rules(cfg)))
            self.assertEqual(cfg['resume_tasks'], [])
            restored.close()

    async def test_priority_and_edited_task_during_queue_never_misroutes(self):
        self.seed((42, 42), overlap=True)
        cfg = self.service.settings(); cfg['tasks'][0]['priority'] = 10
        self.service.store.save_settings(1, cfg)
        await self.runner.handle(event(42, SAMPLE))
        self.send.assert_awaited_once_with(42, '1')
        self.assertEqual(self.service.store.executions(1)[0]['task_id'], 'task-two')
        await self.runner.lock.acquire()
        job = asyncio.create_task(self.runner.handle(event(42, SAMPLE, 2)))
        await asyncio.sleep(0)
        cfg = self.service.settings(); cfg['tasks'][0]['reply_text'] = '2'
        self.service.store.save_settings(1, cfg)
        self.runner.lock.release(); await job
        self.assertEqual(self.send.await_count, 1)

    async def test_stale_edit_cannot_replace_changed_task(self):
        self.seed()
        from engine.task_commands import revision
        stale = revision(task_rule(self.service.settings(), 'task-two'))
        await self.service.command('task_save', {'id': 'task-two', 'mode': 'fixed', 'match_text': '抽查'})
        with self.assertRaises(ValueError):
            await self.service.command('task_save', {'id': 'task-two', 'mode': 'keyword', 'base_revision': stale})
        self.assertEqual(task_rule(self.service.settings(), 'task-two')['mode'], 'fixed')

    async def test_create_two_local_demo_tasks_and_save_sample(self):
        await self.service.seed_demo(1, '演示')
        first = await self.service.command('task_save', {'id': 'new', 'name': '关键词任务', 'target': '1003', 'sample_text': SAMPLE})
        second = await self.service.command('task_save', {'id': 'new', 'name': '固定回复任务', 'target': '1002', 'mode': 'fixed',
                                                       'match_text': '抽查', 'reply_text': '1', 'sample_text': '抽查'})
        self.assertNotEqual(first['saved_task_id'], second['saved_task_id'])
        self.assertEqual(len(self.service.state()['tasks']), 2)
        self.assertTrue(all(not rule['automatic'] for rule in rules(self.service.settings())))
        with self.assertRaises(ValueError):
            await self.service.command('task_save', {'id': 'new', 'name': '禁止真实对象', 'target': '1234567'})
        self.send.assert_not_awaited()

    async def test_v3_backup_migrates_without_touching_original_zip(self):
        from engine.backup import TABLES, inspect_backup, restore_backup
        from pathlib import Path
        import hashlib
        import sqlite3
        import zipfile
        with self.service.store.db:
            self.service.store.db.execute('INSERT INTO accounts VALUES(1,?)', ('测试账号',))
        self.seed()
        for table in ('executions', 'prompts'):
            with self.service.store.db:
                self.service.store.db.execute(f'ALTER TABLE {table} DROP COLUMN task_id')
                self.service.store.db.execute(f'ALTER TABLE {table} DROP COLUMN task_name')
        self.service.store.db.execute('PRAGMA user_version=3'); self.service.store.db.commit()
        self.service.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with tempfile.TemporaryDirectory() as destination:
            path = Path(destination) / 'old.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('manifest.json', json.dumps({'format': 'communications-assistant-backup', 'version': 1, 'schema': 3, 'account': 1}))
                archive.write(self.service.root / 'assistant.sqlite', 'account.sqlite')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            folder = Path(destination) / 'unpacked'; folder.mkdir()
            manifest = inspect_backup(path, 1, folder)
            self.assertEqual(manifest['schema'], 5)
            restored = Store(Path(destination) / 'restored')
            restore_backup(restored, 1, folder, manifest)
            self.assertEqual(restored.db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            restored.close()

    async def test_metadata_only_edit_does_not_drop_queued_prompt(self):
        self.seed()
        await self.runner.lock.acquire()
        job = asyncio.create_task(self.runner.handle(event(42, SAMPLE)))
        await asyncio.sleep(0)
        await self.service.command('task_save', dict(self.service.settings()['rule'], id='legacy', name='改名', sample_text=SAMPLE))
        self.runner.lock.release(); await job
        self.send.assert_awaited_once_with(42, '香蕉')
        self.assertEqual(self.service.store.prompts(1)[0]['mode'], 'sent')

    async def test_duplicate_number_on_another_message_shows_skip_reason(self):
        self.seed()
        await self.runner.handle(event(42, SAMPLE, 1))
        await self.runner.handle(event(42, SAMPLE, 2))
        self.send.assert_awaited_once()
        result = self.service.store.results(1)
        self.assertEqual(result['count'], 2)
        skipped = next(row for row in result['rows'] if row['status'] == 'skipped')
        self.assertEqual(skipped['message_id'], 2)
        self.assertIn('重复', skipped['reason'])

    async def test_results_pagination_and_deleted_task_filter(self):
        from auto_checkin import CheckinPrompt
        self.seed()
        for index in range(60):
            self.service.store.reserve(1, 42, CheckinPrompt(str(index), '1', 5), index+1, time.time(), 'legacy', '原有任务')
            self.service.store.execution_status(1, 42, str(index), 'sent_unverified')
        first = await self.service.command('task_results', {'task_id': 'legacy'})
        following = await self.service.command('task_results', {'task_id': 'legacy', 'offset': 50})
        self.assertEqual((first['count'], len(first['rows']), len(following['rows'])), (60, 50, 10))
        self.assertFalse({row['message_id'] for row in first['rows']} & {row['message_id'] for row in following['rows']})
        await self.service.command('task_delete', {'id': 'legacy', 'confirmed_id': 'legacy'})
        self.assertEqual(self.service.store.results(1, task_id='legacy')['count'], 60)
        self.assertEqual(self.service.store.results(1, task_id='task-two')['count'], 0)

    async def test_edited_template_and_sample_survive_database_reopen(self):
        self.seed()
        template = '在线核对 {number}\n{name}，请在{minutes}分钟内回复\n{keyword}\n请保持在线'
        sample = '在线核对 00177\n验收同事，请在20分钟内回复\n苹果\n请保持在线'
        await self.service.command('task_save', {'id': 'task-two', 'prompt_template': template, 'sample_text': sample})
        preview = await self.service.command('task_preview', {'id': 'task-two', 'text': sample})
        self.assertEqual((preview['number'], preview['keyword']), ('00177', '苹果'))
        self.service.store.close()
        self.service = Service(self.folder.name, demo=True)
        self.service.account = 1; self.service.auth = 'ready'
        saved = task_rule(self.service.settings(), 'task-two')
        self.assertEqual((saved['prompt_template'], saved['sample_text']), (template, sample))
        changed = sample.replace('00177', '00178').replace('验收同事', '另一同事').replace('苹果', '梨子')
        preview = await self.service.command('task_draft_preview', {'draft': saved, 'text': changed})
        self.assertEqual((preview['number'], preview['keyword']), ('00178', '梨子'))
        self.send.assert_not_awaited()

    async def test_delete_other_task_preserves_queued_send_and_active_rule(self):
        self.seed()
        before = self.service.settings()['rule']
        await self.runner.lock.acquire()
        job = asyncio.create_task(self.runner.handle(event(42, SAMPLE)))
        await asyncio.sleep(0)
        await self.service.command('task_delete', {'id': 'task-two', 'confirmed_id': 'task-two'})
        self.assertEqual(before, self.service.settings()['rule'])
        self.runner.lock.release(); await job
        self.send.assert_awaited_once_with(42, '香蕉')

    async def test_recover_selected_only_keeps_closed_task_and_rejects_old_prompt(self):
        from engine.tasks import revision
        from telethon import types
        self.seed()
        cfg = self.service.settings()
        cfg['tasks'].append(dict(cfg['tasks'][0], id='closed-task', target=44, automatic=False))
        self.service.store.save_settings(1, cfg)
        self.service.prepare_recovery()
        cfg = self.service.settings()
        self.assertEqual(set(cfg['resume_tasks']), {'legacy', 'task-two'})
        self.service.demo = False
        self.service.client = SimpleNamespace(is_connected=lambda: True,
            get_entity=AsyncMock(side_effect=lambda target: types.User(id=target, first_name='本地替身', bot=True)))
        await self.service.command('task_resume', {'ids': ['legacy'], 'confirmed_account': 1,
                                                 'revisions': {'legacy': revision(cfg['rule'])}})
        cfg = self.service.settings()
        self.assertTrue(cfg['rule']['automatic'])
        self.assertFalse(task_rule(cfg, 'task-two')['automatic'])
        self.assertFalse(task_rule(cfg, 'closed-task')['automatic'])
        self.assertEqual(cfg['resume_tasks'], ['task-two'])
        self.send.assert_not_awaited()
        old = event(42, SAMPLE)
        old.message.date = datetime.fromtimestamp(cfg['rule']['armed_at']-1, timezone.utc)
        await self.runner.handle(old)
        self.send.assert_not_awaited()
        await self.runner.handle(event(43, SAMPLE, 2))
        self.send.assert_not_awaited()
        await self.runner.handle(event(42, SAMPLE.replace('00176', '00178'), 3))
        self.send.assert_awaited_once_with(42, '香蕉')

    async def test_recovery_target_failure_does_not_partially_enable_batch(self):
        from engine.tasks import revision
        from telethon import types
        self.seed()
        self.service.prepare_recovery()
        cfg = self.service.settings()
        self.service.demo = False
        def resolve(target):
            if target == 43: raise ValueError('本地模拟目标失效')
            return types.User(id=target, first_name='本地替身', bot=True)
        self.service.client = SimpleNamespace(is_connected=lambda: True, get_entity=AsyncMock(side_effect=resolve))
        with self.assertRaises(ValueError):
            await self.service.command('task_resume', {'ids': ['legacy', 'task-two'], 'confirmed_account': 1,
                'revisions': {rule['id']: revision(rule) for rule in rules(cfg)}})
        self.assertEqual(cfg, self.service.settings())
        self.send.assert_not_awaited()
