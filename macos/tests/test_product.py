import asyncio
import json
import tempfile
import sqlite3
import zipfile
from pathlib import Path
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine.service import Service
from engine.store import Store
from engine.backup import create_backup, inspect_backup, restore_backup
from auto_checkin import parse_checkin_prompt
from engine.schedule import active, intervals, validate
from telethon import types
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, PasswordHashInvalidError


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.output = tempfile.TemporaryDirectory()
        self.store = Store(self.folder.name)
        self.store.db.execute("INSERT INTO accounts VALUES(1,'自己')")
        self.store.db.execute("INSERT INTO accounts VALUES(2,'其他账号')")
        self.store.db.commit()
        self.store.put_conversation(1, 42, '测试机器人', 'private')
        self.store.put_messages(1, 42, [{'id': 9, 'date': 1, 'sender': '自己', 'text': '当前账号消息'}])
        self.store.put_messages(2, 42, [{'id': 9, 'date': 1, 'sender': '其他', 'text': '其他账号私密消息'}])
        self.path = Path(self.output.name) / 'backup.tgabackup'

    def tearDown(self):
        self.store.close(); self.folder.cleanup(); self.output.cleanup()

    def test_backup_excludes_credentials_and_other_accounts(self):
        (Path(self.folder.name) / 'telegram.session').write_text('fake_auth_key_sentinel')
        self.store.preference('api_hash', 'fake_api_hash_sentinel')
        create_backup(self.folder.name, 1, self.path)
        result = inspect_backup(self.path, 1)
        self.assertEqual(result['counts']['messages'], 1)
        with zipfile.ZipFile(self.path) as archive:
            self.assertEqual(set(archive.namelist()), {'manifest.json', 'account.sqlite'})
            data = archive.read('account.sqlite')
            for value in ('fake_auth_key_sentinel', 'fake_api_hash_sentinel', '其他账号私密消息'):
                self.assertNotIn(value.encode(), data)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_restore_merges_ledger_without_reenabling_automation(self):
        prompt = parse_checkin_prompt('🔔 查岗提醒（编号 123）\n请在 20 分钟内只回复下面的关键词：\n完成\n回复时限说明：')
        self.store.reserve(1, 42, prompt, 9, 1)
        self.store.execution_status(1, 42, '123', 'sent', 88)
        cfg = self.store.settings(1); cfg['rule']['automatic'] = True; cfg['online'] = True
        self.store.save_settings(1, cfg)
        create_backup(self.folder.name, 1, self.path)
        self.store.confirm(1, 42, '123')
        self.store.put_messages(1, 42, [{'id': 10, 'date': 2, 'sender': '自己', 'text': '备份后新消息'}])
        with tempfile.TemporaryDirectory() as extracted:
            manifest = inspect_backup(self.path, 1, extracted)
            restore_backup(self.store, 1, extracted, manifest)
        self.assertEqual(self.store.executions(1)[0]['status'], 'confirmed')
        self.assertFalse(self.store.settings(1)['rule']['automatic'])
        self.assertFalse(self.store.settings(1)['online'])
        self.assertEqual(self.store.search(1)['count'], 2)
        self.assertEqual(self.store.search(2)['count'], 1)
        self.assertFalse(self.store.reserve(1, 42, prompt, 10, 2))

    def test_wrong_account_and_traversal_rejected(self):
        create_backup(self.folder.name, 1, self.path)
        with self.assertRaises(ValueError): inspect_backup(self.path, 2)
        with zipfile.ZipFile(self.path, 'a') as archive: archive.writestr('../telegram.session', 'fake')
        with self.assertRaises(ValueError): inspect_backup(self.path, 1)

    def test_restore_rejects_invalid_schema_before_mutation(self):
        create_backup(self.folder.name, 1, self.path)
        with tempfile.TemporaryDirectory() as extracted:
            manifest = inspect_backup(self.path, 1, extracted)
            db = sqlite3.connect(Path(extracted) / 'account.sqlite')
            db.execute('ALTER TABLE messages ADD COLUMN unknown TEXT'); db.close()
            with self.assertRaises(ValueError): restore_backup(self.store, 1, extracted, manifest)
        self.assertEqual(self.store.search(1)['count'], 1)

    def test_media_roundtrip(self):
        media = Path(self.folder.name) / 'media/1/42/9.jpg'
        media.parent.mkdir(parents=True); media.write_bytes(b'test image')
        self.store.put_messages(1, 42, [{'id': 9, 'date': 1, 'sender': '自己', 'text': '图片', 'media': 'media/1/42/9.jpg', 'media_uid': '77', 'media_state': 'saved'}])
        create_backup(self.folder.name, 1, self.path)
        with tempfile.TemporaryDirectory() as restored_root, tempfile.TemporaryDirectory() as extracted:
            target = Store(restored_root)
            try:
                manifest = inspect_backup(self.path, 1, extracted)
                restore_backup(target, 1, extracted, manifest)
                self.assertEqual((Path(restored_root) / 'media/1/42/9.jpg').read_bytes(), b'test image')
            finally: target.close()

    def test_missing_backup_image_is_not_reported_saved(self):
        self.store.put_messages(1, 42, [{'id': 9, 'date': 1, 'sender': '自己', 'text': '图片', 'media': 'media/1/42/9.jpg', 'media_state': 'saved'}])
        create_backup(self.folder.name, 1, self.path)
        with tempfile.TemporaryDirectory() as target_root, tempfile.TemporaryDirectory() as extracted:
            target = Store(target_root)
            try:
                manifest = inspect_backup(self.path, 1, extracted); restore_backup(target, 1, extracted, manifest)
                row = target.search(1)['messages'][0]
                self.assertEqual(row['media_state'], 'failed'); self.assertEqual(row['media'], '')
            finally: target.close()


class ProductServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.service = Service(self.folder.name)
        self.service.account = 1
        self.service.auth = 'ready'
        self.service.client = SimpleNamespace(is_connected=lambda: True)
        self.service.publish = AsyncMock()
        self.service.emit = AsyncMock()
        self.service.store.put_conversation(1, 42, '测试机器人', 'private')

    async def asyncTearDown(self):
        for task in self.service.tasks: task.cancel()
        await asyncio.gather(*self.service.tasks, return_exceptions=True)
        self.service.store.close()
        self.folder.cleanup()

    async def test_invalid_profile_preserves_previous_settings(self):
        previous = dict(self.service.device_profile)
        with self.assertRaises(ValueError):
            await self.service.command('device_profile', {'device_model': '设备\n换行'})
        self.assertEqual(self.service.device_profile, previous)
        self.assertEqual(self.service.store.preference('device_profile'), '')

    async def test_reconnect_cancels_previous_account_pending_notices(self):
        self.service.auth = 'offline'
        self.service.client = None
        pending = asyncio.create_task(asyncio.sleep(60))
        self.service.notice_tasks[42] = pending
        self.service.notice_counts[42] = 2
        self.service.notice_message_ids[42] = 9
        client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(), is_user_authorized=AsyncMock(return_value=False),
                                 is_connected=lambda: True, add_event_handler=lambda *a: None)
        try:
            with patch('engine.service.TelegramClient', return_value=client):
                await self.service.connect({'api_id': 123, 'api_hash': 'a' * 32})
            await asyncio.sleep(0)
            self.assertTrue(pending.cancelled())
            self.assertEqual(self.service.notice_tasks, {})
            self.assertEqual(self.service.notice_counts, {})
            self.assertEqual(self.service.notice_message_ids, {})
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_device_profile_saved_and_used_on_next_connection(self):
        await self.service.command('device_profile', {'device_model': '开发机', 'system_version': 'macOS 自定义', 'app_version': '内部测试'})
        self.service.auth = 'disconnected'; self.service.client = None
        client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(), is_user_authorized=AsyncMock(return_value=False),
                                 is_connected=lambda: True, add_event_handler=lambda *a: None)
        with patch('engine.service.TelegramClient', return_value=client) as constructor:
            await self.service.command('connect', {'api_id': 123, 'api_hash': 'a' * 32})
        self.assertEqual(constructor.call_args.kwargs['device_model'], '开发机')
        self.assertEqual(json.loads(self.service.store.preference('device_profile'))['app_version'], '内部测试')

    async def test_apply_profile_disarms_sends_and_retains_ledger(self):
        self.service.client.api_id = 123; self.service.client.api_hash = 'a' * 32
        cfg = self.service.settings(); cfg['rule']['automatic'] = True
        self.service.store.save_settings(1, cfg)
        self.service.connect = AsyncMock(return_value={'auth': 'identity'})
        result = await self.service.command('apply_device_profile', {'device_model': '新名称'})
        self.assertEqual(result['auth'], 'identity')
        self.assertFalse(self.service.settings()['rule']['automatic'])
        self.service.connect.assert_awaited_once()

    async def test_quiet_hours_do_not_stop_archiving(self):
        cfg = self.service.settings(); cfg['quiet_enabled'] = True
        self.service.store.save_settings(1, cfg)
        self.service.store.select(1, 42, True)
        self.service.archive_messages = AsyncMock()
        message = SimpleNamespace(id=8, out=False, mentioned=False, raw_text='私聊测试')
        value = SimpleNamespace(chat_id=42, is_private=True, message=message)
        with patch('engine.service.active', return_value=True):
            await self.service.new_message(value)
        self.service.archive_messages.assert_awaited_once()
        self.assertEqual(dict(self.service.notice_counts), {})

    async def test_conversation_voice_mode_overrides_default_and_does_not_notify(self):
        cfg = self.service.settings(); cfg['conversation_modes'] = {'42': 'voice'}
        cfg['voice_content'] = True; cfg['sound'] = False
        self.service.store.save_settings(1, cfg)
        self.service.notice_counts[42] = 2; self.service.notice_message_ids[42] = 8
        with patch('engine.service.asyncio.sleep', new=AsyncMock()):
            await self.service.delayed_notice(42, '测试机器人', '正文样例')
        data = self.service.emit.call_args.args[1]
        self.assertTrue(data['voice']); self.assertFalse(data['notify']); self.assertFalse(data['sound'])
        self.assertIn('正文样例', data['voice_text'])
        self.assertEqual(data['message_id'], 8)

    async def test_quiet_hours_rechecked_before_pending_notification(self):
        cfg = self.service.settings(); cfg['quiet_enabled'] = True
        self.service.store.save_settings(1, cfg)
        self.service.notice_counts[42] = 1
        with patch('engine.service.asyncio.sleep', new=AsyncMock()), patch('engine.service.active', return_value=True):
            await self.service.delayed_notice(42, '测试机器人', '正文')
        self.service.emit.assert_not_awaited()

    async def test_invalid_quiet_schedule_does_not_change_settings(self):
        with self.assertRaises(ValueError):
            await self.service.command('settings', {'quiet_enabled': True, 'quiet_weekly': [[['25:00', '26:00']]] * 7})
        self.assertFalse(self.service.settings()['quiet_enabled'])

    async def test_notification_context_is_scoped_to_current_account(self):
        self.service.store.put_conversation(2, 43, '其他账号', 'private')
        with self.assertRaises(ValueError):
            await self.service.command('notification_context', {'chat': 43, 'id': 8})

    async def test_notification_context_reads_one_message_without_archiving(self):
        message = SimpleNamespace(id=8, date=datetime.now(timezone.utc), out=False, raw_text='只读消息', ttl_period=None,
                                  get_sender=AsyncMock(return_value=SimpleNamespace(first_name='测试', last_name='用户')))
        self.service.client.get_messages = AsyncMock(return_value=message)
        result = await self.service.command('notification_context', {'chat': 42, 'id': 8})
        self.assertEqual(result['source'], 'remote')
        self.assertEqual(result['messages'][0]['text'], '只读消息')
        self.assertEqual(self.service.store.search(1)['count'], 0)

    async def test_voice_activity_does_not_include_body_or_other_account(self):
        await self.service.command('voice_result', {'result': 'completed', 'account': 2})
        self.assertEqual(self.service.store.activities(1), [])
        await self.service.command('voice_result', {'result': 'completed', 'account': 1})
        self.assertEqual(self.service.store.activities(1)[0]['detail'], '语音播报完成')

    async def test_metadata_and_delete_marker_are_preserved_without_content(self):
        now = datetime.now(timezone.utc)
        message = SimpleNamespace(id=9, date=now, out=False, raw_text='编辑后的消息', file=None, photo=None, document=None,
                                  reply_to=SimpleNamespace(reply_to_msg_id=8), fwd_from=object(), edit_date=now,
                                  get_sender=AsyncMock(return_value=SimpleNamespace(first_name='同事', last_name='')))
        await self.service.archive_messages(42, [message], edited=True)
        row = self.service.store.search(1)['messages'][0]
        self.assertEqual(row['reply_to'], 8); self.assertTrue(row['forwarded']); self.assertGreater(row['edited_at'], 0)
        self.service.store.delete_messages(1, [9], 42)
        self.assertEqual(self.service.store.search(1)['count'], 0)
        deleted = self.service.store.search(1, include_deleted=True)['messages'][0]
        self.assertTrue(deleted['deleted']); self.assertEqual(deleted['text'], '')
        await self.service.archive_messages(42, [message])
        self.assertEqual(self.service.store.search(1)['count'], 0)

    async def test_unlocated_deletion_does_not_delete_other_chats(self):
        self.service.store.put_messages(1, 42, [{'id': 9, 'date': 1, 'sender': '同事', 'text': '保留'}])
        self.service.store.put_conversation(1, 43, '另一会话', 'private')
        self.service.store.put_messages(1, 43, [{'id': 9, 'date': 1, 'sender': '同事', 'text': '保留'}])
        await self.service.deleted_message(SimpleNamespace(chat_id=None, deleted_ids=[9]))
        self.assertEqual(self.service.store.search(1)['count'], 2)
        self.assertEqual(self.service.store.activities(1)[0]['kind'], 'attention')

    async def test_unique_private_message_delete_can_be_located_from_saved_id(self):
        self.service.store.put_messages(1, 42, [{'id': 9, 'date': 1, 'sender': '同事', 'text': '需删除'}])
        self.service.store.put_messages(2, 42, [{'id': 9, 'date': 1, 'sender': '其他账号', 'text': '保留'}])
        await self.service.deleted_message(SimpleNamespace(chat_id=None, deleted_ids=[9]))
        self.assertEqual(self.service.store.search(1)['count'], 0)
        self.assertTrue(self.service.store.search(1, include_deleted=True)['messages'][0]['deleted'])
        self.assertEqual(self.service.store.search(2)['count'], 1)

    async def test_image_failure_stops_automatic_retry_after_three_attempts(self):
        cfg = self.service.settings(); cfg['images'] = True
        self.service.store.save_settings(1, cfg)
        message = SimpleNamespace(id=9, date=datetime.now(timezone.utc), out=False, raw_text='',
                                  file=SimpleNamespace(name='照片.jpg', mime_type='image/jpeg', size=100, ext='.jpg'),
                                  photo=SimpleNamespace(id=77), document=None, sticker=False,
                                  get_sender=AsyncMock(return_value=SimpleNamespace(first_name='同事', last_name='')))
        self.service.client.download_media = AsyncMock(side_effect=TimeoutError())
        for _ in range(5): await self.service.archive_messages(42, [message])
        self.assertEqual(self.service.client.download_media.await_count, 3)
        row = self.service.store.search(1)['messages'][0]
        self.assertEqual(row['media_state'], 'failed'); self.assertEqual(row['media_attempts'], 3)

    async def test_edited_photo_in_history_does_not_reuse_previous_image(self):
        media = Path(self.folder.name) / 'media/1/42/9.jpg'
        media.parent.mkdir(parents=True); media.write_bytes(b'previous image')
        self.service.store.put_messages(1, 42, [{'id': 9, 'date': 1, 'sender': '同事', 'text': '旧图片', 'media': 'media/1/42/9.jpg', 'media_uid': '77', 'media_state': 'saved'}])
        cfg = self.service.settings(); cfg['images'] = True; self.service.store.save_settings(1, cfg)
        now = datetime.now(timezone.utc)
        message = SimpleNamespace(id=9, date=now, edit_date=now, out=False, raw_text='已换图片',
                                  file=SimpleNamespace(name='照片.jpg', mime_type='image/jpeg', size=100, ext='.jpg'),
                                  photo=SimpleNamespace(id=78), document=None, sticker=False,
                                  get_sender=AsyncMock(return_value=SimpleNamespace(first_name='同事', last_name='')))
        self.service.client.download_media = AsyncMock(side_effect=TimeoutError())
        await self.service.archive_messages(42, [message])
        row = self.service.store.search(1)['messages'][0]
        self.assertTrue(row['edited']); self.assertEqual(row['media'], '')
        self.assertEqual(row['media_state'], 'failed'); self.assertFalse(media.exists())

    async def test_old_ledger_import_keeps_numbers_and_disarms_sends(self):
        path = Path(self.folder.name) / 'auto_checkin_1_42.json'
        path.write_text(json.dumps({'version': 1, 'bot_id': 42, 'records': {'123': {'status': 'sending', 'keyword': '完成'}, '124': {'status': 'confirmed'}}}))
        cfg = self.service.settings(); cfg['rule'].update(target=42, automatic=True)
        self.service.store.save_settings(1, cfg)
        preview = await self.service.command('ledger_preview', {'path': str(path)})
        await self.service.command('import_ledger', {'path': str(path), 'confirmed_account': 1, 'digest': preview['digest']})
        self.assertFalse(self.service.settings()['rule']['automatic'])
        rows = {row['number']: row['status'] for row in self.service.store.executions(1)}
        self.assertEqual(rows, {'123': 'send_unknown', '124': 'confirmed'})
        with self.assertRaises(ValueError):
            await self.service.command('import_ledger', {'path': str(path), 'confirmed_account': 2, 'digest': preview['digest']})

    async def test_old_images_import_only_referenced_safe_files(self):
        with tempfile.TemporaryDirectory() as old:
            text = Path(old) / '群.txt'; image = Path(old) / 'media/群/9.jpg'
            image.parent.mkdir(parents=True); image.write_bytes(b'\xff\xd8\xfftest')
            text.write_text('[#9 2026-10-14 10:00] 同事: (回复 #8) 图片\n    多行说明 〔图片〕 → media/群/9.jpg\n')
            await self.service.command('import_legacy', {'path': str(text), 'timezone': 'Asia/Shanghai', 'chat': 42, 'confirmed_account': 1, 'include_media': True})
        row = self.service.store.search(1)['messages'][0]
        self.assertEqual(row['reply_to'], 8); self.assertEqual(row['media_state'], 'saved')
        self.assertTrue((Path(self.folder.name) / row['media']).is_file())

    async def test_resend_cooldown_and_login_error_recovery(self):
        self.service.auth = 'code'; self.service.phone = '+8610000000000'
        self.service.client.sign_in = AsyncMock(side_effect=PhoneCodeInvalidError(None))
        with self.assertRaisesRegex(ValueError, '不正确'): await self.service.command('sign_in', {'code': 'fake'})
        self.assertEqual(self.service.auth, 'code')
        self.service.client.sign_in.side_effect = PhoneCodeExpiredError(None)
        self.service.code_resend_at = 9999999999
        with self.assertRaisesRegex(ValueError, '已过期'): await self.service.command('sign_in', {'code': 'fake'})
        self.assertEqual(self.service.code_resend_at, 0)
        self.service.client.send_code_request = AsyncMock(return_value=SimpleNamespace(phone_code_hash='fake', timeout=45))
        await self.service.command('send_code', {'phone': self.service.phone})
        with self.assertRaisesRegex(ValueError, '倒计时'): await self.service.command('send_code', {'phone': self.service.phone})
        self.service.client.send_code_request.assert_awaited_once()
        self.service.auth = 'password'; self.service.client.sign_in.side_effect = PasswordHashInvalidError(None)
        with self.assertRaisesRegex(ValueError, '不正确'): await self.service.command('password', {'password': 'fake'})
        self.assertEqual(self.service.auth, 'password')

    async def test_cancel_invalid_session_keeps_cached_archive_available_offline(self):
        self.service.store.db.execute("INSERT INTO accounts VALUES(1,'缓存本人')"); self.service.store.db.commit()
        self.service.store.preference('last_account', 1)
        self.service.auth = 'phone'; self.service.account = 0
        self.service.client.disconnect = AsyncMock()
        result = await self.service.command('cancel_login', {})
        self.assertEqual(result['auth'], 'offline'); self.assertEqual(result['account'], 1)
        self.assertIsNone(self.service.client)

    async def test_only_current_device_public_fields_returned(self):
        own = SimpleNamespace(current=True, device_model='我的 Mac', system_version='macOS', app_version='0.2', app_name='API 名称', country='地区', region='区域', hash=123456, ip='secret_ip')
        other = SimpleNamespace(current=False, device_model='secret_other_device')
        self.service.client = AsyncMock(return_value=SimpleNamespace(authorizations=[other, own]))
        self.service.client.is_connected = lambda: True
        await self.service.command('refresh_device_info', {})
        value = json.dumps(self.service.server_profile)
        for sensitive in ('hash', 'ip', 'secret_other_device'): self.assertNotIn(sensitive, value)
        self.assertEqual(self.service.server_profile['device_model'], '我的 Mac')

    async def test_rule_enable_requires_saved_target_and_valid_preview(self):
        self.service.client.get_entity = AsyncMock(return_value=types.User(id=42, first_name='测试', bot=True))
        with self.assertRaises(ValueError): await self.service.rule({'target': '42', 'automatic': True, 'confirmed_target': 42})
        await self.service.rule({'target': '42'})
        with self.assertRaises(ValueError): await self.service.rule({'target': '42', 'automatic': True, 'confirmed_target': 42})
        await self.service.command('preview', {'text': '查岗提醒（编号 12）\n请在 5 分钟内只回复下面的关键词：\n完成\n回复时限说明：'})
        await self.service.rule({'target': '42', 'automatic': True, 'confirmed_target': 42})
        self.assertTrue(self.service.settings()['rule']['automatic'])
        self.service.client.get_entity.return_value = types.User(id=43, first_name='其他', bot=True)
        with self.assertRaises(ValueError): await self.service.rule({'target': '43', 'automatic': True, 'confirmed_target': 43})

    async def test_normal_user_requires_explicit_test_switch(self):
        self.service.client.get_entity = AsyncMock(return_value=types.User(id=42, first_name='测试', bot=False))
        with self.assertRaises(ValueError): await self.service.rule({'target': '42'})
        await self.service.rule({'target': '42', 'allow_user_test': True})
        self.assertTrue(self.service.settings()['rule']['allow_user_test'])
        self.service.client.get_entity.return_value = types.User(id=1, first_name='本人')
        with self.assertRaises(ValueError): await self.service.rule({'target': '1', 'allow_user_test': True})

    async def test_deselected_conversation_does_not_retry_media(self):
        self.service.store.put_messages(1, 42, [{'id': 9, 'date': 1, 'sender': '', 'text': '', 'media_state': 'failed'}])
        self.service.client.get_messages = AsyncMock()
        await self.service.retry_pending_media()
        self.service.client.get_messages.assert_not_awaited()

    async def test_disk_exhaustion_stops_archive_before_any_write(self):
        with patch('engine.service.shutil.disk_usage', return_value=SimpleNamespace(free=1)):
            with self.assertRaises(OSError): await self.service.archive_messages(42, [])
        self.assertTrue(self.service.quota_paused); self.assertTrue(self.service.settings()['pause_sends'])

    async def test_runtime_status_contains_no_private_fields(self):
        self.service.identity = {'name': 'private_identity'}
        self.service.write_runtime_status()
        data = (Path(self.folder.name) / 'runtime-status.json').read_text()
        for value in ('private_identity', 'account', 'keyword', 'api_hash', 'phone'): self.assertNotIn(value, data)


class WholeDayTests(unittest.TestCase):
    def test_whole_day_and_seven_day_preview(self):
        weekly = [[['00:00', '24:00']] for _ in range(7)]
        now = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(active(weekly, 'UTC', now))
        self.assertEqual(len(intervals(weekly, 'UTC', now)), 7)
        with self.assertRaises(ValueError): validate([[['24:00', '01:00']]] * 7, 'UTC')
