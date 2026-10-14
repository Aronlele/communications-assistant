import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine.accounts import AccountManager
from engine.automation import Automation
from engine.store import Store


class AccountsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.manager = AccountManager(self.folder.name)
        self.first = self.manager.services['legacy']
        self.activate(self.first, 101)
        result = await self.manager.command('legacy', 'add_account', {})
        self.slot = result['slot']
        self.second = self.manager.services[self.slot]
        self.activate(self.second, 202)

    def activate(self, service, account):
        service.account = account
        service.identity = {'id': account, 'name': '测试账号'}
        service.auth = 'ready'
        service.client = SimpleNamespace(is_connected=lambda: True, disconnect=AsyncMock(), log_out=AsyncMock(return_value=True))
        service.store.db.execute('INSERT INTO accounts VALUES(?,?)', (account, '测试账号'))
        service.store.db.commit()
        service.store.preference('last_account', account)

    async def asyncTearDown(self):
        await self.manager.close()
        self.folder.cleanup()

    async def test_add_and_switch_do_not_disconnect_existing_account(self):
        await self.manager.command(self.slot, 'select_account', {'slot': 'legacy'})
        self.assertTrue(self.first.state()['connected'])
        self.assertTrue(self.second.state()['connected'])
        self.first.client.disconnect.assert_not_called()
        self.second.client.disconnect.assert_not_called()
        self.assertEqual(self.manager.active, 'legacy')

    async def test_custom_reply_template_changes_only_requested_account(self):
        cfg = self.first.settings()
        cfg['rule'].update(target=42, automatic=True, preview_validated=True)
        self.first.store.save_settings(101, cfg)
        await self.manager.command(self.slot, 'reply_template', {
            'mode': 'fixed', 'match_text': '在线抽查', 'match_mode': 'exact',
            'reply_text': '1', 'valid_minutes': 5})
        self.assertEqual(self.second.settings()['rule']['mode'], 'fixed')
        self.assertFalse(self.second.settings()['rule']['automatic'])
        self.assertEqual(self.first.settings()['rule']['mode'], 'keyword')
        self.assertTrue(self.first.settings()['rule']['automatic'])
        self.assertEqual(len(self.manager.account_list()), 2)

    async def test_settings_and_pause_are_independent(self):
        await self.manager.command('legacy', 'settings', {'voice': True, 'images': True})
        await self.manager.command('legacy', 'control', {'action': 'pause_all'})
        self.assertTrue(self.first.settings()['voice'])
        self.assertTrue(self.first.settings()['images'])
        self.assertTrue(self.first.settings()['pause_all'])
        for key in ('voice', 'images', 'pause_all'):
            self.assertFalse(self.second.settings()[key])

    async def test_same_message_ids_search_and_media_roots_are_isolated(self):
        for service, text in ((self.first, '第一账号私密消息'), (self.second, '第二账号私密消息')):
            service.store.put_conversation(service.account, 42, '机器人', 'private')
            service.store.put_messages(service.account, 42, [{'id': 7, 'date': 1, 'sender': '机器人', 'text': text}])
        first = await self.manager.command('legacy', 'search', {})
        second = await self.manager.command(self.slot, 'search', {})
        self.assertEqual(first['messages'][0]['text'], '第一账号私密消息')
        self.assertEqual(second['messages'][0]['text'], '第二账号私密消息')
        self.assertNotEqual(self.first.root, self.second.root)
        self.assertEqual(await self.manager.command(self.slot, 'search', {'query': '第一账号'}), {'count': 0, 'messages': []})

    async def test_both_accounts_send_same_number_once_and_match_their_receipts(self):
        now = datetime.now(timezone.utc)
        prompt = '🔔 查岗提醒（编号 123）\n请在20分钟内只回复下面的关键词：\n完成\n回复时限说明：'
        for service in (self.first, self.second):
            cfg = service.settings()
            cfg['rule'].update(target=42, automatic=True, armed_at=now.timestamp() - 1)
            service.store.save_settings(service.account, cfg)
            service.send_keyword = AsyncMock(return_value=SimpleNamespace(id=8))
            service.automation = Automation(service.store, service.account, service.settings, service.send_keyword, service.activity, now.timestamp() - 1)
        def event(text):
            return SimpleNamespace(is_private=True, sender_id=42, chat_id=42,
                                   message=SimpleNamespace(id=7, raw_text=text, out=False, fwd_from=None, via_bot_id=None, date=now))
        await asyncio.gather(self.first.automation.handle(event(prompt), now), self.second.automation.handle(event(prompt), now))
        await self.first.automation.handle(event(prompt), now)
        await self.second.automation.handle(event(prompt), now)
        for service in (self.first, self.second):
            service.send_keyword.assert_awaited_once_with(42, '完成')
            self.assertEqual(service.store.executions(service.account)[0]['status'], 'sent')
        await self.first.automation.handle(event('✅ 按时响应\n编号 123 · 已记录 · 感谢配合！'), now)
        self.assertEqual(self.first.store.executions(101)[0]['status'], 'confirmed')
        self.assertEqual(self.second.store.executions(202)[0]['status'], 'sent')

    async def test_inactive_account_notices_keep_account_and_slot(self):
        self.manager.emit = AsyncMock()
        self.first.notice_counts[42] = 2
        self.first.notice_message_ids[42] = 7
        with patch('engine.service.asyncio.sleep', new=AsyncMock()):
            await self.first.delayed_notice(42, '机器人', '消息')
        message = next(call.args[0] for call in self.manager.emit.await_args_list if call.args[0].get('event') == 'reminder')
        self.assertEqual(message['slot'], 'legacy')
        self.assertEqual(message['data']['account'], 101)
        self.assertEqual(message['data']['count'], 2)
        self.assertEqual(self.manager.active, self.slot)

    async def test_logout_one_leaves_other_connection_and_settings(self):
        first_client = self.first.client
        second_client = self.second.client
        await self.manager.command('legacy', 'logout', {})
        first_client.log_out.assert_awaited_once()
        second_client.disconnect.assert_not_called()
        self.assertTrue(self.second.state()['connected'])
        self.assertEqual(self.second.account, 202)

    async def test_late_voice_completion_after_logout_does_not_error_other_account(self):
        await self.manager.command('legacy', 'logout', {})
        result = await self.manager.command('legacy', 'voice_result', {'account': 101, 'result': 'cancelled'})
        self.assertEqual(result, {'ok': True})
        self.assertTrue(self.second.state()['connected'])

    async def test_resend_uses_own_pending_phone_after_ui_account_switch(self):
        self.second.auth = 'code'
        self.second.phone = '+12345678901'
        self.second.client.send_code_request = AsyncMock(return_value=SimpleNamespace(phone_code_hash='synthetic', timeout=60))
        self.first.phone = '+10987654321'
        await self.manager.command(self.slot, 'resend_code', {})
        self.second.client.send_code_request.assert_awaited_once_with('+12345678901')
        self.assertEqual(self.first.phone, '+10987654321')
        self.assertEqual(self.second.auth, 'code')

    async def test_duplicate_account_cannot_be_confirmed(self):
        self.second.auth = 'identity'
        self.second.identity = dict(self.first.identity)
        with self.assertRaisesRegex(ValueError, '已经添加'):
            await self.manager.command(self.slot, 'confirm_identity', {})
        self.assertTrue(self.first.state()['connected'])

    async def test_cancel_duplicate_draft_does_not_revoke_existing_session(self):
        self.second.store.preference('last_account', 0)
        self.second.auth = 'identity'
        self.second.identity = dict(self.first.identity)
        client = self.second.client
        await self.manager.command(self.slot, 'cancel_login', {})
        client.log_out.assert_awaited_once()
        self.first.client.log_out.assert_not_called()
        self.first.client.disconnect.assert_not_called()
        self.assertEqual(self.second.auth, 'disconnected')

    async def test_cancel_restored_identity_keeps_offline_account_and_session(self):
        self.second.auth = 'identity'
        client = self.second.client
        session = self.second.root / 'telegram.session'
        session.write_text('synthetic session')
        await self.manager.command(self.slot, 'cancel_login', {})
        self.assertEqual(self.second.auth, 'offline')
        self.assertEqual(self.second.account, 202)
        self.assertTrue(session.exists())
        client.log_out.assert_not_called()

    async def test_cancel_new_session_reports_unconfirmed_revoke(self):
        self.second.store.preference('last_account', 0)
        self.second.auth = 'identity'
        self.second.client.log_out = AsyncMock(side_effect=ConnectionError())
        result = await self.manager.command(self.slot, 'cancel_login', {})
        self.assertEqual(result['auth'], 'disconnected')
        self.assertIn('远端撤销未确认', result['error'])
        self.assertTrue(self.first.state()['connected'])

    async def test_backup_cross_account_restore_and_output_overwrite_rejected(self):
        with tempfile.TemporaryDirectory() as output:
            path = Path(output) / 'account.zip'
            await self.manager.command('legacy', 'backup', {'path': str(path)})
            with self.assertRaises(ValueError):
                await self.manager.command(self.slot, 'backup_preview', {'path': str(path)})
        with self.assertRaisesRegex(ValueError, '其他账号'):
            await self.manager.command('legacy', 'export', {'path': str(self.second.root / 'assistant.sqlite')})

    async def test_slow_account_command_does_not_block_other_account(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
            return {'ok': True}
        self.first.command = slow
        task = asyncio.create_task(self.manager.command('legacy', 'sync', {}))
        await entered.wait()
        result = await asyncio.wait_for(self.manager.command(self.slot, 'state', {}), 1)
        self.assertEqual(result['account'], 202)
        release.set()
        await task

    async def test_restart_preserves_legacy_paths_and_all_accounts_disarms_auto(self):
        for service in (self.first, self.second):
            cfg = service.settings(); cfg['rule']['automatic'] = True
            service.store.save_settings(service.account, cfg)
        self.manager.save_registry()
        await self.manager.close()
        self.manager = AccountManager(self.folder.name)
        self.assertEqual(self.manager.services['legacy'].root, Path(self.folder.name))
        self.assertEqual(set(self.manager.services), {'legacy', self.slot})
        self.assertEqual(self.manager.active, self.slot)
        for service in self.manager.services.values():
            self.assertEqual(service.auth, 'offline')
            self.assertFalse(service.settings()['rule']['automatic'])

    async def test_unknown_slot_cannot_fall_back_to_active_account(self):
        with self.assertRaises(ValueError):
            await self.manager.command('unknown', 'control', {'action': 'pause_all'})
        self.assertFalse(self.second.settings()['pause_all'])

    async def test_runtime_diagnostics_omit_identity_and_messages(self):
        self.manager.write_runtime_status()
        data = json.loads((Path(self.folder.name) / 'runtime-status.json').read_text())
        self.assertEqual(data['account_count'], 2)
        self.assertEqual(len(data['accounts']), 2)
        text = json.dumps(data)
        for key in ('identity', 'phone', 'api_hash', 'keyword', 'text'):
            self.assertNotIn('"' + key + '"', text)

    async def test_shutdown_closes_connected_ipc_without_waiting_for_client(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = AccountManager(folder, demo=True)
            address = str(Path(folder) / 'engine.sock')
            run = asyncio.create_task(manager.run(address))
            for _ in range(100):
                if Path(address).exists():
                    break
                await asyncio.sleep(.01)
            reader, writer = await asyncio.open_unix_connection(address)
            writer.write((json.dumps({'protocol': 1, 'id': 'shutdown', 'method': 'shutdown'}) + '\n').encode())
            await writer.drain()
            async def response():
                while True:
                    data = json.loads(await reader.readline())
                    if data.get('id') == 'shutdown':
                        return data
            result = await asyncio.wait_for(response(), 2)
            self.assertEqual(result['result'], {'ok': True})
            await asyncio.wait_for(run, 2)
            self.assertFalse(Path(address).exists())
            writer.close()
            await writer.wait_closed()


if __name__ == '__main__':
    unittest.main()
