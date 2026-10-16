import json
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from engine.service import Service
from engine.automation import Automation


class ExperienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.s = Service(self.folder.name, demo=True)
        await self.s.seed_demo()
        self.a = self.s.account
        self.s.store.db.execute('INSERT INTO accounts VALUES(?,?)', (self.a, '合成账号'))
        self.s.store.db.commit()
        cfg = self.s.settings()
        cfg['rule'].update(target=1003, title='测试对象', mode='fixed', match_text='合成提示', reply_text='1', automatic=True, armed_at=time.time()-10)
        self.s.store.save_settings(self.a, cfg)
        self.cfg = self.s.settings()
        self.send = AsyncMock(return_value=SimpleNamespace(id=101))
        self.bot = Automation(self.s.store, self.a, self.s.settings, self.send, AsyncMock(), started=time.time()-10)
        self.s.automation = self.bot

    async def asyncTearDown(self):
        self.s.store.close(); self.folder.cleanup()

    def event(self, text='合成提示', identifier=99, **changes):
        message = SimpleNamespace(id=identifier, raw_text=text, date=datetime.now(timezone.utc), out=False, fwd_from=None, via_bot_id=None)
        for k,v in changes.items(): setattr(message,k,v)
        return SimpleNamespace(sender_id=1003, chat_id=1003, is_private=True, message=message)

    async def test_overview_is_readonly_and_never_claims_platform_history_complete(self):
        self.s.store.range(self.a,1001,'initial','仅从启用时刻开始',start=123)
        rows = (await self.s.command('archive_overview', {}))['rows']
        group = next(r for r in rows if r['chat']==1001)
        self.assertEqual(group['count'],3);self.assertEqual(group['started'],123)
        self.assertIn('仅从',group['basis']);self.assertTrue(group['earliest']<=group['latest'])
        self.assertEqual(self.s.settings(),self.cfg);self.send.assert_not_awaited()
        self.assertIn('不代表完整', (await self.s.command('archive_overview', {}))['note'])

    async def test_overview_pause_stopped_syncing_and_account_isolation(self):
        self.s.syncing_chats.add(1001)
        self.assertIn('分批',next(r for r in (await self.s.command('archive_overview',{}))['rows'] if r['chat']==1001)['status'])
        cfg=self.s.settings();cfg['archive_paused']=True;self.s.store.save_settings(self.a,cfg)
        self.s.store.select(self.a,1002,False)
        self.s.store.put_conversation(777,888,'其他身份','group')
        rows=(await self.s.command('archive_overview',{}))['rows']
        self.assertIn('暂停',next(r for r in rows if r['chat']==1001)['status'])
        self.assertIn('停止',next(r for r in rows if r['chat']==1002)['status'])
        self.assertNotIn(888,[r['chat'] for r in rows])
        self.s.auth='offline';self.s.store.save_settings(self.a,self.cfg)
        self.assertIn('离线',(await self.s.command('archive_overview',{}))['rows'][0]['status'])

    async def test_type_filter_export_and_cleanup_preview_have_identical_scope(self):
        now=time.time()
        self.s.store.put_messages(self.a,1001,[{'id':10,'date':now,'text':'群改名','sender':'演示','attachment':'群组系统消息'},
            {'id':11,'date':now,'text':'图片说明','sender':'演示','attachment':'photo.png','media_state':'not_requested','media_uid':'12'},
            {'id':12,'date':now,'text':'','sender':'演示','attachment':'report.pdf','media_state':'none','media_uid':'13'}])
        for kind,expected in [('system',[10]),('image',[11]),('attachment',[12]),('text',[3,2,1])]:
            args={'chat':1001,'kind':kind}
            search=await self.s.command('search',args)
            self.assertEqual([r['id'] for r in search['messages']],expected)
            self.assertEqual((await self.s.command('export_preview',args))['count'],len(expected))
            self.assertEqual((await self.s.command('archive_cleanup_preview',dict(args,scope='filter')))['count'],len(expected))
        with self.assertRaises(ValueError):await self.s.command('search',{'kind':'invalid'})
        self.assertEqual(self.s.settings(),self.cfg)

    async def test_unmatched_and_forwarded_checks_do_not_save_body_or_send(self):
        await self.bot.handle(self.event('私密的未匹配正文'))
        self.assertEqual(self.bot.checks['legacy']['status'],'unmatched')
        self.assertNotIn('私密',json.dumps(self.bot.checks,ensure_ascii=False))
        await self.bot.handle(self.event(fwd_from=object()))
        self.assertEqual(self.bot.checks['legacy']['status'],'ignored')
        self.send.assert_not_awaited()
        self.assertEqual(self.s.store.results(self.a)['count'],0)
        self.assertEqual(self.s.settings(),self.cfg)

    async def test_checks_explain_pause_expiry_duplicate_and_sent_without_receipt_claim(self):
        cfg=self.s.settings();cfg['pause_sends']=True;self.s.store.save_settings(self.a,cfg)
        await self.bot.handle(self.event());self.assertIn('暂停',self.bot.checks['legacy']['reason'])
        self.s.store.save_settings(self.a,self.cfg)
        await self.bot.handle(self.event(date=datetime.now(timezone.utc)-timedelta(minutes=10)))
        self.assertIn('期限',self.bot.checks['legacy']['reason'])
        await self.bot.handle(self.event())
        self.assertIn('核对',self.bot.checks['legacy']['reason']);self.send.assert_awaited_once()
        self.assertEqual(self.s.store.results(self.a)['rows'][0]['status'],'sent_unverified')
        await self.bot.handle(self.event());self.send.assert_awaited_once()
        self.assertIn('重复',self.bot.checks['legacy']['reason'])
        self.assertEqual(self.s.settings(),self.cfg)

    async def test_checks_bound_to_rule_and_process_not_copied_into_backup(self):
        await self.bot.handle(self.event('不匹配'))
        cfg=self.s.settings();cfg['rule']['target']=0;self.s.store.save_settings(self.a,cfg)
        cfg['tasks']=[dict(self.cfg['rule'],id='second',target=1003)];self.s.store.save_settings(self.a,cfg)
        await self.bot.handle(self.event('不匹配'))
        self.assertEqual(set(self.bot.checks),{'second'})
        clean=Automation(self.s.store,self.a,self.s.settings,self.send,AsyncMock())
        self.assertEqual(clean.checks,{})
        path=Path(self.folder.name).parent/(Path(self.folder.name).name+'-test.zip')
        try:
            await self.s.command('backup',{'path':str(path)})
            self.assertNotIn('task_checks',self.s.settings())
        finally:path.unlink(missing_ok=True)

    async def test_result_refresh_key_ignores_unrelated_activity_and_changes_on_result(self):
        key=self.s.state()['results_refresh_key']
        await self.s.activity('demo','无关活动')
        self.assertEqual(self.s.state()['results_refresh_key'],key)
        self.s.store.prune(self.a,30)
        self.assertEqual(self.s.state()['results_refresh_key'],key)
        await self.bot.handle(self.event())
        changed=self.s.state()['results_refresh_key'];self.assertNotEqual(key,changed)
        # Ordinary missed prompts do not change stored result pages.
        await self.bot.handle(self.event('未匹配',identifier=102))
        self.assertEqual(self.s.state()['results_refresh_key'],changed)

    async def test_changed_rule_hides_previous_check_and_unknown_send_never_retries(self):
        await self.bot.handle(self.event('未匹配'))
        self.assertEqual(len(self.s.state()['task_checks']),1)
        cfg=self.s.settings();cfg['rule']['reply_text']='2';self.s.store.save_settings(self.a,cfg)
        self.assertEqual(self.s.state()['task_checks'],[])
        self.send.side_effect=OSError('synthetic-failure')
        await self.bot.handle(self.event())
        self.assertEqual(self.bot.checks['legacy']['status'],'send_unknown')
        await self.bot.handle(self.event());self.send.assert_awaited_once()

    async def test_backup_verification_tracks_success_without_disarming_or_storing_paths(self):
        path=Path(self.folder.name).parent/(Path(self.folder.name).name+'-encrypted.jixunbackup')
        try:
            await self.s.command('backup',{'path':str(path),'password':'synthetic-password'})
            status=self.s.state()['backup_status'];self.assertTrue(status['encrypted']);self.assertGreater(status['created'],0)
            with self.assertRaises(ValueError):await self.s.command('backup_preview',{'path':str(path),'password':'wrong-password'})
            self.assertNotIn('verified',self.s.state()['backup_status'])
            await self.s.command('backup_preview',{'path':str(path),'password':'synthetic-password'})
            self.assertGreater(self.s.state()['backup_status']['verified'],0)
            self.assertNotIn(str(path),json.dumps(self.s.state()['backup_status']))
            self.assertEqual(self.s.settings(),self.cfg);self.send.assert_not_awaited()
            self.s.account=12345
            self.assertEqual(self.s.state()['backup_status'],{})
            self.s.store.erase_account(self.a)
            self.assertEqual(self.s.store.preference('backup_status.'+str(self.a)),'')
        finally:path.unlink(missing_ok=True)
