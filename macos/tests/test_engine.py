import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from auto_checkin import parse_checkin_prompt
from engine.automation import Automation
from engine.schedule import active, boundary, intervals, validate
from engine.service import Service
from engine.importer import read_legacy
from engine.store import Store
from engine.profile import DEFAULT_PROFILE, validate_profile
from zoneinfo import ZoneInfo


PROMPT = "🔔 查岗提醒（编号 123）\n请在 5 分钟内只回复下面的关键词：\n今日完成\n回复时限说明：\n以机器人登记为准"


def event(now, text=PROMPT, **kwargs):
    message = SimpleNamespace(raw_text=text, out=False, fwd_from=None, via_bot_id=None, date=now, id=7)
    result = SimpleNamespace(is_private=True, sender_id=42, chat_id=42, message=message)
    for key, value in kwargs.items():
        setattr(message if hasattr(message, key) else result, key, value)
    return result


def record(identifier, text="测试消息"):
    return {"id": identifier, "date": time.time(), "sender": "同事", "text": text}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(self.folder.name)
        self.store.put_conversation(1, 11, "研发群", "group")

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_message_cursor_atomic_and_accounts_isolated(self):
        self.store.put_messages(1, 11, [record(1)], cursor=1)
        self.store.put_messages(2, 11, [record(1, "其他账号")], cursor=1)
        self.assertEqual(self.store.search(1)["messages"][0]["text"], "测试消息")
        self.assertEqual(self.store.search(2)["messages"][0]["text"], "其他账号")
        with self.assertRaises(KeyError):
            self.store.put_messages(1, 11, [record(2), {"id": 3}], cursor=3)
        self.assertEqual(self.store.conversation(1, 11)["cursor"], 1)
        self.assertEqual(self.store.search(1)["count"], 1)

    def test_realtime_does_not_jump_history_cursor(self):
        self.store.put_messages(1, 11, [record(10)], cursor=10)
        self.store.put_messages(1, 11, [record(3000)])
        self.assertEqual(self.store.conversation(1, 11)["cursor"], 10)

    def test_replay_and_edit_replace_without_duplicates(self):
        self.store.put_messages(1, 11, [record(1)])
        self.store.put_messages(1, 11, [dict(record(1, "更新后的消息"), edited=True)])
        self.assertEqual(self.store.search(1)["count"], 1)
        self.assertEqual(self.store.search(1, "更新")["count"], 1)
        self.assertEqual(self.store.search(1, "测试")["count"], 0)

    def test_delete_removes_media_and_search(self):
        media = Path(self.folder.name) / "media/1/11/1.jpg"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"sample")
        self.store.put_messages(1, 11, [dict(record(1), media="media/1/11/1.jpg")])
        self.store.delete_messages(1, [1], 11)
        self.assertFalse(media.exists())
        self.assertEqual(self.store.search(1)["count"], 0)

    def test_erase_chat_preserves_execution_ledger(self):
        prompt = parse_checkin_prompt(PROMPT)
        self.store.reserve(1, 42, prompt, 1, time.time())
        self.store.put_messages(1, 11, [record(1)])
        self.store.erase_chat(1, 11)
        self.assertEqual(self.store.search(1)["count"], 0)
        self.assertFalse(self.store.reserve(1, 42, prompt, 2, time.time()))

    def test_restart_turns_sending_into_unknown(self):
        self.store.reserve(1, 42, parse_checkin_prompt(PROMPT), 1, time.time())
        self.store.close()
        self.store = Store(self.folder.name)
        self.assertEqual(self.store.executions(1)[0]["status"], "send_unknown")

    def test_unknown_confirmation_does_not_create_record(self):
        self.assertFalse(self.store.confirm(1, 42, "321"))
        self.assertEqual(self.store.executions(1), [])

    def test_receipt_attention_uses_deadline_plus_five_minutes(self):
        now = time.time()
        prompt = parse_checkin_prompt(PROMPT.replace('5 分钟', '20 分钟'))
        self.store.reserve(1, 42, prompt, 1, now)
        self.store.execution_status(1, 42, prompt.number, 'sent', 88)
        with patch('engine.store.time.time', return_value=now + 301):
            self.assertFalse(self.store.executions(1)[0]['receipt_overdue'])
        with patch('engine.store.time.time', return_value=now + 1499):
            self.assertFalse(self.store.executions(1)[0]['receipt_overdue'])
        with patch('engine.store.time.time', return_value=now + 1500):
            self.assertTrue(self.store.executions(1)[0]['receipt_overdue'])
        self.store.confirm(1, 42, prompt.number)
        with patch('engine.store.time.time', return_value=now + 1800):
            self.assertFalse(self.store.executions(1)[0]['receipt_overdue'])

    def test_unknown_legacy_deadline_is_not_guessed(self):
        self.store.reserve(1, 42, parse_checkin_prompt(PROMPT), 1, time.time())
        self.store.execution_status(1, 42, '123', 'sent', 88)
        self.store.db.execute('UPDATE executions SET deadline=NULL')
        self.store.db.commit()
        self.assertFalse(self.store.executions(1)[0]['receipt_overdue'])

    def test_retention_removes_content_but_keeps_number(self):
        self.store.put_messages(1, 11, [dict(record(1), date=time.time() - 40 * 86400)])
        self.store.reserve(1, 42, parse_checkin_prompt(PROMPT), 1, time.time() - 40 * 86400)
        self.store.db.execute("UPDATE executions SET updated=?", (time.time() - 40 * 86400,))
        self.store.db.commit()
        self.store.prune(1)
        self.assertEqual(self.store.search(1)["count"], 0)
        self.assertEqual(self.store.executions(1)[0]["keyword"], "")
        self.assertFalse(self.store.reserve(1, 42, parse_checkin_prompt(PROMPT), 2, time.time()))

    def test_erase_account_preserves_other_account(self):
        self.store.put_messages(1, 11, [record(1)])
        self.store.put_conversation(2, 11, "另一个账号", "group")
        self.store.put_messages(2, 11, [record(1)])
        self.store.reserve(1, 42, parse_checkin_prompt(PROMPT), 1, time.time())
        self.store.erase_account(1)
        self.assertEqual(self.store.search(1)["count"], 0)
        self.assertEqual(self.store.executions(1), [])
        self.assertEqual(self.store.search(2)["count"], 1)

    def test_literal_wildcard_and_injection_search(self):
        self.store.put_messages(1, 11, [record(1, "100% 完成"), record(2, "普通消息")])
        self.assertEqual(self.store.search(1, "%")["count"], 1)
        self.assertEqual(self.store.search(1, "' OR 1=1--")["count"], 0)

    def test_private_file_permissions(self):
        self.assertEqual(os.stat(self.folder.name).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(Path(self.folder.name) / "assistant.sqlite").st_mode & 0o777, 0o600)

    def test_newer_schema_fails_closed(self):
        folder = tempfile.TemporaryDirectory()
        db = sqlite3.connect(Path(folder.name) / "assistant.sqlite")
        db.execute("PRAGMA user_version=9"); db.close()
        with self.assertRaises(RuntimeError): Store(folder.name)
        folder.cleanup()

    def test_migration_backup_is_sanitized_when_erasing_account(self):
        self.store.put_messages(1, 11, [record(1, "需删除的内容")])
        self.store.put_messages(2, 11, [record(1, "其他账号内容")])
        self.store.db.execute("PRAGMA user_version=1")
        self.store.close(); self.store = Store(self.folder.name)
        backup_path = Path(self.folder.name) / "assistant.before-v4.sqlite"
        self.assertTrue(backup_path.exists())
        self.store.erase_account(1)
        backup = sqlite3.connect(backup_path)
        self.assertEqual(backup.execute("SELECT COUNT(*) FROM messages WHERE account=1").fetchone()[0], 0)
        self.assertEqual(backup.execute("SELECT COUNT(*) FROM messages WHERE account=2").fetchone()[0], 1)
        backup.close()


class ScheduleTests(unittest.TestCase):
    def test_device_profile_defaults_and_custom_fields(self):
        self.assertEqual(validate_profile({}), DEFAULT_PROFILE)
        self.assertEqual(validate_profile({'device_model': '我的 Mac', 'system_version': 'macOS 27', 'app_version': '内部版'}),
                         {'device_model': '我的 Mac', 'system_version': 'macOS 27', 'app_version': '内部版'})
        self.assertEqual(validate_profile({'device_model': '  '})['device_model'], DEFAULT_PROFILE['device_model'])
        for value in ('a' * 129, 'Mac\n换行', 1):
            with self.assertRaises(ValueError): validate_profile({'device_model': value})

    def test_empty_is_off(self):
        self.assertFalse(active([[] for _ in range(7)], "Asia/Shanghai"))

    def test_friday_carries_to_saturday_only(self):
        weekly = [[] for _ in range(7)]
        weekly[4] = [["23:00", "07:00"]]
        self.assertTrue(active(weekly, "Asia/Shanghai", datetime(2026, 10, 17, 1, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertFalse(active(weekly, "Asia/Shanghai", datetime(2026, 10, 18, 1, tzinfo=ZoneInfo("Asia/Shanghai"))))

    def test_dst_gap_and_fold(self):
        zone = ZoneInfo("America/New_York")
        spring = datetime(2026, 3, 8).date()
        self.assertEqual(boundary(spring, "02:30", zone).astimezone(zone).hour, 3)
        fall = datetime(2026, 11, 1).date()
        self.assertEqual((boundary(fall, "01:30", zone, True) - boundary(fall, "01:30", zone)).total_seconds(), 3600)

    def test_invalid_input(self):
        for value in ([[]], [[['29:00', '10:00']]] + [[]] * 6, [[['10:00', '10:00']]] + [[]] * 6):
            with self.assertRaises(ValueError): validate(value, "Asia/Shanghai")

    def test_unknown_timezone_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "无法识别时区"):
            validate([[] for _ in range(7)], "Not/AZone")


class AutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(self.folder.name)
        self.now = datetime.now(timezone.utc)
        self.cfg = self.store.settings(1)
        self.cfg["rule"] = {"target": 42, "automatic": True, "armed_at": self.now.timestamp() - 10}
        self.send = AsyncMock(return_value=SimpleNamespace(id=88))
        self.log = AsyncMock()
        self.runner = Automation(self.store, 1, lambda: self.cfg, self.send, self.log, self.now.timestamp() - 10)

    async def asyncTearDown(self):
        self.store.close(); self.folder.cleanup()

    async def test_eight_concurrent_prompts_submit_once(self):
        await asyncio.gather(*(self.runner.handle(event(self.now), self.now) for _ in range(8)))
        self.send.assert_awaited_once_with(42, "今日完成")
        self.assertEqual(self.store.executions(1)[0]["status"], "sent")

    async def test_reminder_only_does_not_reserve_or_send(self):
        self.cfg["rule"]["automatic"] = False
        await self.runner.handle(event(self.now), self.now)
        self.send.assert_not_awaited(); self.assertEqual(self.store.executions(1), [])

    async def test_source_guards(self):
        for changes in ({"sender_id": 43}, {"chat_id": 43}, {"is_private": False}, {"out": True}, {"fwd_from": object()}, {"via_bot_id": 50}):
            await self.runner.handle(event(self.now, **changes), self.now)
        self.send.assert_not_awaited()

    async def test_expired_future_and_before_start(self):
        for date in (self.now - timedelta(minutes=6), self.now + timedelta(seconds=61), self.now - timedelta(seconds=11)):
            await self.runner.handle(event(date), self.now)
        self.send.assert_not_awaited()

    async def test_pauses_prevent_send(self):
        for key in ("pause_sends", "pause_all"):
            self.cfg[key] = True
            await self.runner.handle(event(self.now), self.now)
            self.cfg[key] = False
        self.send.assert_not_awaited()

    async def test_limited_sending_empty_schedule_never_sends(self):
        self.cfg["rule"].update(sending_limited=True, sending_weekly=[[] for _ in range(7)], sending_timezone="Asia/Shanghai")
        await self.runner.handle(event(self.now), self.now)
        self.send.assert_not_awaited()

    async def test_unknown_is_not_retried_across_rule_replacement(self):
        self.send.side_effect = TimeoutError()
        await self.runner.handle(event(self.now), self.now)
        self.cfg["rule"] = dict(self.cfg["rule"], title="改名")
        await self.runner.handle(event(self.now), self.now)
        self.assertEqual(self.send.await_count, 1)
        self.assertEqual(self.store.executions(1)[0]["status"], "send_unknown")

    async def test_db_failure_prevents_network_submission(self):
        with patch.object(self.store, "reserve", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.runner.handle(event(self.now), self.now)
        self.send.assert_not_awaited()

    async def test_deadline_rechecked_after_reservation(self):
        with patch("engine.automation.time.monotonic", side_effect=[100, 401]):
            await self.runner.handle(event(self.now), self.now)
        self.send.assert_not_awaited()
        self.assertEqual(self.store.executions(1)[0]["status"], "expired")

    async def test_receipt_during_send_is_not_overwritten(self):
        async def send(*args):
            self.store.confirm(1, 42, "123")
            return SimpleNamespace(id=88)
        self.runner.send = send
        await self.runner.handle(event(self.now), self.now)
        self.assertEqual(self.store.executions(1)[0]["status"], "confirmed")

    async def test_confirmation_only_for_target_and_existing_ledger(self):
        text = "✅ 按时响应\n编号 123 · 已记录"
        await self.runner.handle(event(self.now, text), self.now)
        self.assertEqual(self.store.executions(1), [])
        await self.runner.handle(event(self.now), self.now)
        await self.runner.handle(event(self.now, text, sender_id=43), self.now)
        self.assertEqual(self.store.executions(1)[0]["status"], "sent")
        self.cfg["pause_all"] = True
        await self.runner.handle(event(self.now, text), self.now)
        self.assertEqual(self.store.executions(1)[0]["status"], "confirmed")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.service = Service(self.folder.name)
        self.service.account = 1; self.service.auth = "ready"
        self.service.publish = AsyncMock()
        self.service.client = SimpleNamespace(is_connected=lambda: True)
        self.service.store.put_conversation(1, 11, "工作群", "group")

    async def asyncTearDown(self):
        for task in list(self.service.tasks): task.cancel()
        await asyncio.gather(*self.service.tasks, return_exceptions=True)
        self.service.store.close(); self.folder.cleanup()

    async def test_cancel_login_clears_challenge_without_deleting_session(self):
        session = Path(self.folder.name) / "telegram.session"
        session.write_bytes(b"session placeholder")
        for phase in ("phone", "code", "password"):
            with self.subTest(phase=phase):
                self.service.auth = phase
                self.service.phone = "+8613800000000"
                self.service.phone_code_hash = "temporary challenge"
                client = SimpleNamespace(disconnect=AsyncMock())
                self.service.client = client
                await self.service.command("cancel_login", {})
                client.disconnect.assert_awaited_once()
                self.assertEqual(self.service.auth, "disconnected")
                self.assertEqual(self.service.phone, "")
                self.assertIsNone(self.service.phone_code_hash)
                self.assertIsNone(self.service.client)
                self.assertTrue(session.exists())

    async def test_cancel_login_cannot_discard_confirmed_or_cached_identity(self):
        for phase in ("ready", "offline", "identity", "disconnected"):
            with self.subTest(phase=phase):
                self.service.auth = phase
                with self.assertRaises(ValueError):
                    await self.service.command("cancel_login", {})
                self.assertEqual(self.service.auth, phase)
                self.assertEqual(self.service.account, 1)

    async def test_api_connection_enters_phone_step_and_can_be_cancelled(self):
        self.service.auth = "disconnected"
        self.service.client = None
        client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(),
                                 is_user_authorized=AsyncMock(return_value=False),
                                 is_connected=lambda: True,
                                 add_event_handler=lambda *args: None)
        with patch("engine.service.TelegramClient", return_value=client) as constructor:
            result = await self.service.command("connect", {"api_id": 12345, "api_hash": "a" * 32})
        self.assertEqual(constructor.call_args.args[1:3], (12345, "a" * 32))
        self.assertEqual(constructor.call_args.kwargs['device_model'], DEFAULT_PROFILE['device_model'])
        self.assertEqual(result["auth"], "phone")
        client.connect.assert_awaited_once()
        await self.service.command("cancel_login", {})
        self.assertEqual(self.service.auth, "disconnected")
        client.disconnect.assert_awaited_once()

    async def test_gap_2500_messages_not_capped_or_lost(self):
        self.service.store.select(1, 11, True)
        self.service.store.put_messages(1, 11, [record(1)], cursor=1, initialized=True)
        async def iterator(*args, **kwargs):
            for i in range(kwargs["min_id"] + 1, 2502):
                yield SimpleNamespace(id=i)
        self.service.client.iter_messages = iterator
        async def archive(chat, batch, cursor=None, **kwargs):
            self.service.store.put_messages(1, chat, [record(m.id) for m in batch], cursor=cursor)
        self.service.archive_messages = archive
        await self.service.sync_chat(11)
        self.assertEqual(self.service.store.search(1)["count"], 2501)
        self.assertEqual(self.service.store.conversation(1, 11)["cursor"], 2501)

    async def test_interruption_cursor_resumes_remaining_records(self):
        self.service.store.select(1, 11, True)
        self.service.store.put_messages(1, 11, [record(1)], cursor=1, initialized=True)
        first = True
        async def iterator(*args, **kwargs):
            nonlocal first
            for i in range(kwargs["min_id"] + 1, 252):
                if first and i == 152:
                    first = False
                    raise ConnectionError()
                yield SimpleNamespace(id=i)
        self.service.client.iter_messages = iterator
        async def archive(chat, batch, cursor=None, **kwargs):
            self.service.store.put_messages(1, chat, [record(m.id) for m in batch], cursor=cursor)
        self.service.archive_messages = archive
        await self.service.sync_chat(11)
        self.assertEqual(self.service.store.conversation(1, 11)["cursor"], 101)
        await self.service.sync_chat(11)
        self.assertEqual(self.service.store.search(1)["count"], 251)

    async def test_export_respects_filters_and_contains_no_execution_keywords(self):
        self.service.store.put_messages(1, 11, [record(1, "项目进度"), record(2, "其他内容")])
        self.service.store.reserve(1, 42, parse_checkin_prompt(PROMPT), 1, time.time())
        with tempfile.TemporaryDirectory() as output:
            path = Path(output) / "export.json"
            result = self.service.export({"path": str(path), "query": "项目", "format": "json"})
            self.assertEqual(result["count"], 1)
            data = path.read_text()
            self.assertNotIn("今日完成", data)
            self.assertNotIn("api_hash", data)
            self.assertEqual(json.loads(data)["messages"][0]["text"], "项目进度")
        with self.assertRaises(ValueError):
            self.service.export({"path": str(Path(self.folder.name) / "overwrite.json")})

    async def test_context_returns_adjacent_chat_messages_with_sparse_ids(self):
        self.service.store.put_messages(1, 11, [record(1), record(1000), record(20000)])
        result = await self.service.command("context", {"chat": 11, "id": 1000})
        self.assertEqual([r["id"] for r in result["messages"]], [1, 1000, 20000])

    async def test_duplicate_updates_do_not_duplicate_reminders(self):
        value = event(datetime.now(timezone.utc))
        await self.service.new_message(value)
        await self.service.new_message(value)
        self.assertEqual(self.service.notice_counts[42], 1)

    async def test_paused_messages_are_not_replayed_after_resume(self):
        cfg = self.service.settings(); cfg["paused_reminders_until"] = time.time() + 30
        self.service.store.save_settings(1, cfg)
        value = event(datetime.now(timezone.utc))
        await self.service.new_message(value)
        cfg["paused_reminders_until"] = 0; self.service.store.save_settings(1, cfg)
        await self.service.new_message(value)
        self.assertEqual(self.service.notice_counts[42], 0)

    async def test_diagnostics_contains_no_identity_or_content(self):
        self.service.identity = {"name": "私密姓名"}
        self.service.store.put_messages(1, 11, [record(1, "私密内容")])
        value = json.dumps(self.service.diagnostics(), ensure_ascii=False)
        self.assertNotIn("私密", value)
        self.assertNotIn("keyword", value)

    async def test_shutdown_gates_new_work(self):
        await self.service.command("shutdown", {})
        self.assertTrue(self.service.settings()["pause_sends"])
        self.assertTrue(self.service.settings()["pause_all"])
        self.assertFalse(self.service.store.settings(1)["pause_all"])

    async def test_pause_all_preserves_independent_pauses(self):
        await self.service.control("pause_sends")
        await self.service.control("pause_archive")
        await self.service.control("pause_all")
        await self.service.control("resume_all")
        cfg = self.service.settings()
        self.assertTrue(cfg["pause_sends"]); self.assertTrue(cfg["archive_paused"])
        self.assertFalse(cfg["pause_all"])

    async def test_legacy_import_requires_identity_and_preserves_existing(self):
        path = Path(self.folder.name) / "work.txt"
        path.write_text("[#1 2026-10-13 23:15] 同事: 旧记录\n    多行内容\n[#2 2026-10-13 23:16] 同事: 新记录\n", encoding="utf-8")
        self.service.store.put_messages(1, 11, [record(1, "现有记录优先")])
        args = {"path": str(path), "timezone": "Asia/Shanghai", "chat": 11}
        with self.assertRaises(ValueError): await self.service.command("import_legacy", args)
        await self.service.command("import_legacy", dict(args, confirmed_account=1))
        self.assertEqual(self.service.store.search(1)["count"], 2)
        self.assertEqual(self.service.store.search(1, "现有记录")["count"], 1)

    async def test_media_export_copies_only_account_scoped_files(self):
        media = Path(self.folder.name) / "media/1/11/1.jpg"
        media.parent.mkdir(parents=True); media.write_bytes(b"sample image")
        self.service.store.put_messages(1, 11, [dict(record(1), media="media/1/11/1.jpg")])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "messages.json"
            self.service.export({"path": str(path), "include_media": True})
            value = json.loads(path.read_text())
            image = Path(folder) / value["messages"][0]["image"]
            self.assertEqual(image.read_bytes(), b"sample image")

    async def test_offline_cached_account_has_automatic_sends_disabled(self):
        self.service.store.db.execute("INSERT INTO accounts VALUES(?,?)", (77, "缓存账号"))
        self.service.store.db.commit()
        self.service.store.preference("last_account", 77)
        cfg = self.service.store.settings(77); cfg["rule"]["automatic"] = True
        self.service.store.save_settings(77, cfg)
        another = Service(self.folder.name)
        try:
            self.assertEqual(another.auth, "offline")
            self.assertEqual(another.account, 77)
            self.assertFalse(another.settings()["rule"]["automatic"])
        finally: another.store.close()

    async def test_quota_does_not_advance_cursor(self):
        self.service.store.select(1, 11, True)
        self.service.store.put_messages(1, 11, [], cursor=5, initialized=True)
        cfg = self.service.settings(); cfg["quota_mb"] = 100; self.service.store.save_settings(1, cfg)
        self.service.store.usage = lambda account: 100 * 1024 * 1024
        message = SimpleNamespace(id=6, ttl_period=None, media=None, raw_text="message", sender_id=2,
                                  file=None, date=datetime.now(timezone.utc), out=False, photo=None)
        message.get_sender = AsyncMock(return_value=SimpleNamespace(first_name="成员", last_name=""))
        await self.service.archive_messages(11, [message], cursor=6)
        self.assertTrue(self.service.quota_paused)
        self.assertEqual(self.service.store.conversation(1, 11)["cursor"], 5)

    async def test_expiring_messages_do_not_become_permanent_records(self):
        self.service.store.put_conversation(1, 11, "群", "group")
        message = SimpleNamespace(id=6, ttl_period=20, media=None)
        await self.service.archive_messages(11, [message], cursor=6)
        self.assertEqual(self.service.store.search(1)["count"], 0)
        self.assertEqual(self.service.store.conversation(1, 11)["cursor"], 6)

    async def test_allowable_hours_change_disarms_rule(self):
        cfg = self.service.settings(); cfg["rule"].update(target=42, automatic=True, armed_at=time.time())
        self.service.store.save_settings(1, cfg)
        await self.service.command("rule_hours", {"limited": True, "weekly": [[] for _ in range(7)], "timezone": "Asia/Shanghai"})
        self.assertFalse(self.service.settings()["rule"]["automatic"])


if __name__ == "__main__":
    unittest.main()
