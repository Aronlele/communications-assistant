import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from engine.automation import Automation
from engine.backup import safe_settings
from engine.reply_rule import parse_reply, validate_template
from engine.service import Service
from engine.store import Store


NOTICE = '📣 管理员正在进行在线抽查，请回复任意内容确认在岗。'
TEMPLATE = {'mode': 'fixed', 'match_text': NOTICE, 'match_mode': 'exact',
            'reply_text': '1', 'valid_minutes': 5}


class ReplyTemplateTests(unittest.TestCase):
    def test_exact_preview_and_message_identity(self):
        prompt = parse_reply(NOTICE, TEMPLATE, 71)
        self.assertEqual((prompt.number, prompt.keyword, prompt.window_minutes), ('message:71', '1', 5))
        self.assertIsNone(parse_reply(NOTICE + ' 已结束', TEMPLATE, 71))
        self.assertIsNone(parse_reply('普通聊天', TEMPLATE, 71))

    def test_contains_is_explicit_and_line_endings_normalized(self):
        self.assertIsNone(parse_reply('前言\n' + NOTICE, TEMPLATE, 71))
        self.assertIsNotNone(parse_reply('前言\n' + NOTICE, dict(TEMPLATE, match_mode='contains'), 71))
        cfg = dict(TEMPLATE, match_text='开始\n请回复')
        self.assertIsNotNone(parse_reply('开始\r\n请回复\r\n', cfg, 71))

    def test_invalid_settings_rejected(self):
        for values in ({'match_text': ''}, {'reply_text': '\n'}, {'reply_text': '1\n2'},
                       {'reply_text': 'x' * 101}, {'match_text': 'x' * 2001},
                       {'match_text': '\u200b'}, {'valid_minutes': 0}, {'valid_minutes': True},
                       {'valid_minutes': 1441}, {'match_mode': 'regex'}, {'mode': 'unknown'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_template(dict(TEMPLATE, **values))

    def test_legacy_keyword_mode_unchanged(self):
        prompt = parse_reply('查岗提醒（编号 12）\n请在 5 分钟内只回复下面的关键词：\n小猫\n回复时限说明：', {}, 71)
        self.assertEqual((prompt.number, prompt.keyword), ('12', '小猫'))

    def test_backup_preserves_custom_settings_disarms_and_validates(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            value = store.settings(1)
            value['rule'].update(TEMPLATE, target=42, automatic=True)
            restored = safe_settings(value)
            self.assertEqual(restored['rule']['reply_text'], '1')
            self.assertFalse(restored['rule']['automatic'])
            value['rule']['reply_text'] = '\n'
            with self.assertRaises(ValueError): safe_settings(value)
            store.close()


class FixedReplyAutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(self.folder.name)
        self.now = datetime.now(timezone.utc)
        self.cfg = self.store.settings(1)
        self.cfg['rule'].update(TEMPLATE, target=42, automatic=True, armed_at=self.now.timestamp()-10)
        self.send = AsyncMock(return_value=SimpleNamespace(id=88))
        self.runner = Automation(self.store, 1, lambda: self.cfg, self.send, AsyncMock(), self.now.timestamp()-10)

    async def asyncTearDown(self):
        self.store.close(); self.folder.cleanup()

    def event(self, identifier=71, text=NOTICE, **changes):
        message = SimpleNamespace(raw_text=text, out=False, fwd_from=None, via_bot_id=None,
                                  date=self.now, id=identifier)
        result = SimpleNamespace(is_private=True, sender_id=42, chat_id=42, message=message)
        for key, value in changes.items(): setattr(message if hasattr(message, key) else result, key, value)
        return result

    async def test_concurrent_duplicate_once_new_message_can_reply(self):
        await asyncio.gather(*(self.runner.handle(self.event(), self.now) for _ in range(8)))
        self.send.assert_awaited_once_with(42, '1')
        row = self.store.executions(1)[0]
        self.assertEqual(row['status'], 'sent_unverified')
        self.assertFalse(row['receipt_overdue'])
        await self.runner.handle(self.event(72), self.now)
        self.assertEqual(self.send.await_count, 2)

    async def test_source_and_stale_guards(self):
        for changes in ({'sender_id': 43}, {'chat_id': 43}, {'is_private': False}, {'out': True},
                        {'fwd_from': object()}, {'via_bot_id': 50}, {'date': self.now-timedelta(seconds=11)},
                        {'date': self.now-timedelta(minutes=6)}, {'date': self.now+timedelta(seconds=61)}):
            await self.runner.handle(self.event(**changes), self.now)
        self.send.assert_not_awaited()

    async def test_reminder_pause_and_unrelated_message(self):
        self.cfg['rule']['automatic'] = False
        await self.runner.handle(self.event(), self.now)
        self.assertEqual(self.store.prompts(1)[0]['mode'], 'reminder')
        self.cfg['rule']['automatic'] = True
        for key in ('pause_all', 'pause_sends'):
            self.cfg[key] = True
            await self.runner.handle(self.event(), self.now)
            self.cfg[key] = False
        await self.runner.handle(self.event(text='✅ 已确认'), self.now)
        self.send.assert_not_awaited()

    async def test_unknown_result_not_retried_after_reply_changes(self):
        self.send.side_effect = TimeoutError()
        await self.runner.handle(self.event(), self.now)
        self.cfg['rule']['reply_text'] = '在岗'
        await self.runner.handle(self.event(), self.now)
        self.assertEqual(self.send.await_count, 1)
        self.assertEqual(self.store.executions(1)[0]['status'], 'send_unknown')

    async def test_two_accounts_same_message_id_independent(self):
        other = Automation(self.store, 2, lambda: self.cfg, self.send, AsyncMock(), self.now.timestamp()-10)
        await asyncio.gather(self.runner.handle(self.event(), self.now), other.handle(self.event(), self.now))
        self.assertEqual(self.send.await_count, 2)
        self.assertEqual(len(self.store.executions(1)), 1)
        self.assertEqual(len(self.store.executions(2)), 1)

    async def test_old_numbered_receipt_processed_after_mode_changes(self):
        from auto_checkin import CheckinPrompt
        prompt = CheckinPrompt('123', '小猫', 20)
        self.store.reserve(1, 42, prompt, 70, self.now.timestamp())
        self.store.execution_status(1, 42, '123', 'sent')
        await self.runner.handle(self.event(text='✅ 按时响应\n编号 123 · 已记录 · 感谢配合！'), self.now)
        self.assertEqual(self.store.executions(1)[0]['status'], 'confirmed')
        self.send.assert_not_awaited()


class ReplyTemplateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_disarms_and_preview_uses_saved_template(self):
        with tempfile.TemporaryDirectory() as folder:
            service = Service(folder, demo=True)
            service.account = 1; service.auth = 'ready'
            cfg = service.settings(); cfg['rule'].update(target=42, automatic=True, preview_validated=True)
            service.store.save_settings(1, cfg)
            await service.command('reply_template', TEMPLATE)
            self.assertFalse(service.settings()['rule']['automatic'])
            self.assertFalse(service.settings()['rule']['preview_validated'])
            result = await service.command('preview', {'text': NOTICE})
            self.assertEqual(result['keyword'], '1')
            self.assertTrue(service.settings()['rule']['preview_validated'])
            with self.assertRaises(ValueError):
                await service.command('reply_template', dict(TEMPLATE, reply_text=''))
            self.assertEqual(service.settings()['rule']['reply_text'], '1')
            service.store.close()
