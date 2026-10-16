import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

from engine.service import Service
from engine.store import Store
from engine.tasks import task_rule


class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.service = Service(self.folder.name, demo=True)
        await self.service.seed_demo()
        self.account = self.service.account
        cfg = self.service.settings()
        cfg['rule'].update(target=1003, title='合成目标', mode='fixed', match_text='抽查', reply_text='1',
                           automatic=True, preview_validated=True, armed_at=100)
        self.service.store.save_settings(self.account, cfg)

    async def asyncTearDown(self):
        for task in list(self.service.tasks): task.cancel()
        self.service.store.close()
        self.folder.cleanup()

    async def test_copy_and_template_import_do_not_enable_or_modify_original(self):
        original = self.service.settings()['rule'].copy()
        result = await self.service.command('task_duplicate', {'id': 'legacy'})
        copied = task_rule(self.service.settings(), result['saved_task_id'])
        self.assertFalse(copied['automatic']); self.assertFalse(copied['preview_validated'])
        self.assertEqual(self.service.settings()['rule'], original)
        document = await self.service.command('task_template_export', {'id': 'legacy'})
        self.assertNotIn('target', document['rule']); self.assertNotIn('automatic', document['rule'])
        document['rule'].update(target=999999, automatic=True, preview_validated=True)
        imported = await self.service.command('task_template_import', {'document': document})
        self.assertEqual(imported['target'], '')
        self.assertEqual(imported['id'], 'new'); self.assertFalse(imported['automatic'])
        self.assertEqual(self.service.settings()['rule'], original)

    async def test_suite_checks_negative_cases_reply_and_cannot_be_bypassed_by_single_preview(self):
        cfg = self.service.settings()
        cfg['rule']['sample_cases'] = [{'text': '抽查', 'should_match': True, 'expected_reply': '2'},
                                       {'text': '普通通知', 'should_match': False}]
        self.service.store.save_settings(self.account, cfg)
        result = await self.service.command('task_cases_preview', {'id': 'legacy'})
        self.assertFalse(result['passed']); self.assertFalse(result['rows'][0]['passed'])
        await self.service.command('task_preview', {'id': 'legacy', 'text': '抽查'})
        self.assertFalse(self.service.settings()['rule']['preview_validated'])
        cfg = self.service.settings(); cfg['rule']['sample_cases'][0]['expected_reply'] = '1'
        self.service.store.save_settings(self.account, cfg)
        self.assertTrue((await self.service.command('task_cases_preview', {'id': 'legacy'}))['passed'])

    async def test_cleanup_preview_freezes_selection_and_preserves_rules_ledger_and_other_account(self):
        store = self.service.store
        for account in (self.account, 99):
            store.put_conversation(account, 42, '合成会话', 'private')
            store.put_messages(account, 42, [{'id': 1, 'date': time.time()-50, 'sender': '合成', 'text': '待清理'}])
        original = self.service.settings()
        from engine.reply_rule import parse_reply
        prompt = parse_reply('抽查', original['rule'], 77)
        store.reserve(self.account, 42, prompt, 77, time.time())
        ledger = [tuple(r) for r in store.db.execute('SELECT * FROM executions')]
        preview = await self.service.command('archive_cleanup_preview', {'scope': 'filter', 'chat': 42})
        store.put_messages(self.account, 42, [{'id': 2, 'date': time.time(), 'sender': '合成', 'text': '预览后新消息'}])
        await self.service.command('archive_cleanup_apply', {'token': preview['token'], 'confirmed_account': self.account})
        self.assertEqual([r['id'] for r in store.search(self.account, chat=42)['messages']], [2])
        self.assertEqual(store.search(99, chat=42)['count'], 1)
        self.assertEqual(self.service.settings(), original)
        self.assertEqual([tuple(r) for r in store.db.execute('SELECT * FROM executions')], ledger)
        store.put_messages(self.account, 42, [{'id': 1, 'date': time.time()-50, 'sender': '合成', 'text': '历史补齐'}])
        self.assertEqual(store.search(self.account, chat=42)['count'], 1)
        with self.assertRaises(ValueError):
            await self.service.command('archive_cleanup_apply', {'token': preview['token'], 'confirmed_account': self.account})

    async def test_clear_continue_accepts_only_new_content_and_clear_stop_disables_only_archive(self):
        store = self.service.store; store.put_conversation(self.account, 42, '合成会话', 'private'); store.select(self.account, 42, True)
        old_date = time.time()-100
        store.put_messages(self.account, 42, [{'id': 1, 'date': old_date, 'sender': '', 'text': '旧内容'}])
        preview = await self.service.command('archive_cleanup_preview', {'scope': 'conversation', 'chat': 42, 'keep_archiving': True})
        await self.service.command('archive_cleanup_apply', {'token': preview['token'], 'confirmed_account': self.account})
        store.put_messages(self.account, 42, [{'id': 2, 'date': old_date, 'sender': '', 'text': '在途旧历史'},
                                            {'id': 3, 'date': time.time()+1, 'sender': '', 'text': '新内容'}])
        self.assertEqual(store.search(self.account, chat=42)['count'], 1)
        self.assertTrue(store.conversation(self.account, 42)['selected'])
        preview = await self.service.command('archive_cleanup_preview', {'scope': 'conversation', 'chat': 42, 'keep_archiving': False})
        await self.service.command('archive_cleanup_apply', {'token': preview['token'], 'confirmed_account': self.account})
        self.assertFalse(store.conversation(self.account, 42)['selected'])
        self.assertTrue(self.service.settings()['rule']['automatic'])

    async def test_cleanup_rejects_wrong_account_stale_preview_and_invalid_scope(self):
        for args in ({'scope': 'other'}, {'scope': 'selected', 'messages': [{'chat': 1003, 'id': -1}]}):
            with self.assertRaises(ValueError): await self.service.command('archive_cleanup_preview', args)
        preview = await self.service.command('archive_cleanup_preview', {'scope': 'filter'})
        with self.assertRaises(ValueError):
            await self.service.command('archive_cleanup_apply', {'token': preview['token'], 'confirmed_account': 999})

    async def test_issues_have_actions_and_do_not_change_send_state(self):
        cfg = self.service.settings(); cfg['pause_sends'] = True; self.service.store.save_settings(self.account, cfg)
        self.service.cooldown = time.time()+60; self.service.quota_paused = True
        issues = (await self.service.command('issues', {}))['rows']
        self.assertTrue({'cooldown', 'storage', 'paused'}.issubset({r['code'] for r in issues}))
        self.assertTrue(all(r['action'] and r['steps'] for r in issues))
        self.assertEqual(self.service.settings(), cfg)

    async def test_cleanup_rejects_edited_content_and_expired_preview_without_deleting(self):
        store = self.service.store
        preview = await self.service.command('archive_cleanup_preview', {'scope': 'filter'})
        key = self.service.archive_cleanup['keys'][0]
        store.db.execute('UPDATE messages SET text=? WHERE account=? AND chat=? AND id=?', ('已编辑',self.account,*key)); store.db.commit()
        with self.assertRaisesRegex(ValueError, '已变化'):
            await self.service.command('archive_cleanup_apply', {'token': preview['token'], 'confirmed_account': self.account})
        self.assertGreater(store.search(self.account)['count'], 0)
        preview = await self.service.command('archive_cleanup_preview', {'scope': 'filter'})
        self.service.archive_cleanup['created'] -= 301
        with self.assertRaisesRegex(ValueError, '过期'):
            await self.service.command('archive_cleanup_apply', {'token': preview['token'], 'confirmed_account': self.account})

    async def test_image_cleanup_preserves_text_and_survives_store_reopen(self):
        store = self.service.store; store.put_conversation(self.account, 42, '合成会话', 'private')
        relative = f'media/{self.account}/42/1.jpg'; path = Path(self.folder.name)/relative
        path.parent.mkdir(parents=True); path.write_bytes(b'synthetic image')
        record = {'id':1,'date':time.time()-30,'sender':'','text':'保留文字','media':relative,'media_state':'saved'}
        store.put_messages(self.account,42,[record])
        preview = await self.service.command('archive_cleanup_preview', {'scope':'filter','chat':42,'media_only':True})
        self.assertEqual(preview['images'],1)
        await self.service.command('archive_cleanup_apply', {'token':preview['token'],'confirmed_account':self.account})
        self.assertFalse(path.exists()); self.assertEqual(store.search(self.account,chat=42)['messages'][0]['text'],'保留文字')
        store.close(); self.service.store = Store(self.folder.name)
        self.service.store.put_messages(self.account,42,[record])
        row = self.service.store.search(self.account,chat=42)['messages'][0]
        self.assertEqual(row['media'],''); self.assertEqual(row['text'],'保留文字')

    async def test_inflight_batch_cannot_restore_deleted_message(self):
        store = self.service.store; store.put_conversation(self.account,42,'合成会话','private')
        record = {'id':1,'date':time.time()-10,'sender':'','text':'旧内容'};store.put_messages(self.account,42,[record])
        preview = await self.service.command('archive_cleanup_preview', {'scope':'selected','messages':[{'chat':42,'id':1}]})
        async def sender():
            await self.service.command('archive_cleanup_apply', {'token':preview['token'],'confirmed_account':self.account})
            return SimpleNamespace(first_name='合成',last_name='')
        message = SimpleNamespace(id=1,date=datetime.fromtimestamp(record['date'],timezone.utc),
                                  get_sender=sender,file=None,photo=None,document=None,raw_text='旧内容',out=False)
        # 删除发生在 get_sender 的网络等待期间，而不是批次开始之前。
        await self.service.archive_messages(42,[message])
        self.assertEqual(store.search(self.account,chat=42)['count'],0)

    async def test_suite_limits_invalid_types_and_draft_preview_does_not_touch_settings(self):
        before = self.service.settings()
        bad = ([{'text':'x','should_match':'yes'}], [{'text':'','should_match':True}],
               [{'text':'x','should_match':False,'expected_reply':'1'}], [{'text':'x','should_match':True}]*21)
        for cases in bad:
            with self.assertRaises(ValueError): await self.service.command('task_cases_preview', {'draft':before['rule'],'cases':cases})
        result = await self.service.command('task_cases_preview', {'draft':before['rule'],'cases':[{'text':'抽查','should_match':True,'expected_reply':'1'},{'text':'其他','should_match':False}]})
        self.assertTrue(result['passed']);self.assertEqual(self.service.settings(),before)

    async def test_builtins_and_template_roundtrip_are_valid_and_unbound(self):
        values = (await self.service.command('task_templates',{}))['rows']
        self.assertEqual(len(values),3)
        for value in values:
            self.assertEqual(value['target'],'');self.assertFalse(value['automatic'])
        with self.assertRaises(ValueError): await self.service.command('task_template_import', {'document':{'format':'wrong','version':1}})

    async def test_combined_sample_size_is_bounded(self):
        cases = [{'text': '合成' * 1900, 'should_match': True}] * 3
        with self.assertRaisesRegex(ValueError, '总长度'):
            await self.service.command('task_cases_preview', {'draft':self.service.settings()['rule'],'cases':cases})

    async def test_backup_restoration_preserves_cleanup_markers(self):
        from engine.backup import create_backup, inspect_backup, restore_backup
        store=self.service.store; preview=await self.service.command('archive_cleanup_preview',{'scope':'filter','chat':1003})
        key = self.service.archive_cleanup['keys'][0]
        old = dict(store.db.execute('select * from messages where account=? and chat=? and id=?', (self.account,*key)).fetchone())
        await self.service.command('archive_cleanup_apply',{'token':preview['token'],'confirmed_account':self.account})
        with tempfile.TemporaryDirectory() as target, tempfile.TemporaryDirectory() as extracted:
            archive=Path(target)/'backup.zip';create_backup(Path(self.folder.name),self.account,archive)
            manifest=inspect_backup(archive,self.account,extracted)
            restored=Store(Path(target)/'restored')
            try:
                restore_backup(restored,self.account,extracted,manifest)
                restored.put_messages(self.account,1003,[dict(old,text='不应复活')])
                self.assertEqual(restored.search(self.account,chat=1003)['count'], 0)
                self.assertTrue(restored.db.execute('select count(*) from archive_deletions').fetchone()[0]>0)
            finally:restored.close()
