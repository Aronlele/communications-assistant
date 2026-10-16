import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from telethon import events, types
from engine.service import Service
from telethon.errors import FloodWaitError
from engine.backup import create_backup, inspect_backup, restore_backup
from engine.store import Store
from engine.message_content import GroupServiceMessage, service_content


def message(action, identifier=10, actor=1, chat=42, date=None):
    value = types.MessageService(id=identifier, peer_id=types.PeerChat(chat),
                                 date=date or datetime.now(timezone.utc), action=action,
                                 from_id=types.PeerUser(actor))
    value._sender = types.User(id=actor, first_name='操作者')
    value._action_entities = [types.User(id=1, first_name='操作者'), types.User(id=2, first_name='新成员')]
    return value


class ContentTests(unittest.TestCase):
    def test_join_leave_title_and_other_system_actions(self):
        cases = [(types.MessageActionChatAddUser([2]), '操作者邀请新成员加入群组'),
                 (types.MessageActionChatAddUser([1]), '操作者加入群组'),
                 (types.MessageActionChatDeleteUser(2), '操作者将新成员移出群组'),
                 (types.MessageActionChatDeleteUser(1), '操作者退出群组'),
                 (types.MessageActionChatEditTitle('新名称'), '操作者将群组名称修改为「新名称」'),
                 (types.MessageActionChatJoinedByLink(9), '操作者通过邀请链接加入群组'),
                 (types.MessageActionChatCreate('测试群', [1, 2]), '操作者创建群组「测试群」'),
                 (types.MessageActionChatDeletePhoto(), '操作者移除群组头像'),
                 (types.MessageActionPinMessage(), '操作者置顶一条消息'),
                 (types.MessageActionChatJoinedByRequest(), '操作者通过入群申请加入群组'),
                 (types.MessageActionChannelCreate('合成超级群'), '操作者创建群组「合成超级群」'),
                 (types.MessageActionChatEditPhoto(types.PhotoEmpty(1)), '操作者更换群组头像'),
                 (types.MessageActionHistoryClear(), '操作者清空群组历史记录'),
                 (types.MessageActionChatMigrateTo(99), '操作者将群组升级为超级群'),
                 (types.MessageActionChannelMigrateFrom('原群',42), '群组「原群」已升级为超级群'),
                 (types.MessageActionGroupCall(types.InputGroupCall(1,2)), '操作者发起群组通话'),
                 (types.MessageActionGroupCall(types.InputGroupCall(1,2),duration=0), '操作者结束群组通话'),
                 (types.MessageActionGroupCallScheduled(types.InputGroupCall(1,2),datetime.now(timezone.utc)), '操作者安排群组通话'),
                 (types.MessageActionInviteToGroupCall(types.InputGroupCall(1,2),[2]), '操作者邀请新成员参与群组通话'),
                 (types.MessageActionSetMessagesTTL(60), '操作者将消息自动删除时间设为60秒'),
                 (types.MessageActionSetMessagesTTL(0), '操作者关闭消息自动删除'),
                 (types.MessageActionTopicCreate('合成话题',0), '操作者创建话题「合成话题」'),
                 (types.MessageActionTopicEdit(title='新话题',closed=True), '操作者将话题名称修改为「新话题」、关闭话题'),
                 (types.MessageActionTopicEdit(closed=False,hidden=False), '操作者重新开启话题、显示话题'),
                 (types.MessageActionTopicEdit(hidden=True), '操作者隐藏话题'),
                 (types.MessageActionTopicEdit(), '操作者更新话题设置'),
                 (types.MessageActionGameScore(1,10), '操作者更新游戏得分为10')]
        for action, expected in cases:
            with self.subTest(action=type(action).__name__):
                result = service_content(message(action))
                self.assertEqual(result['text'], expected)
                self.assertEqual(result['attachment'], '群组系统消息')

    def test_missing_entities_unknown_events_and_plain_messages(self):
        value = message(types.MessageActionChatAddUser([999])); value._action_entities = []
        self.assertIn('成员 ID 999', service_content(value)['text'])
        value.action = types.MessageActionEmpty()
        self.assertIn('暂未支持', service_content(value)['text'])
        value = message(types.MessageActionChatDeleteUser(2)); value.from_id = None; value._sender_id = None
        self.assertIn('离开或被移出', service_content(value)['text'])
        value.peer_id = types.PeerUser(3)
        self.assertIsNone(service_content(value))
        self.assertIsNone(service_content(SimpleNamespace(action=None)))

    def test_service_event_builder_includes_service_and_rejects_regular_updates(self):
        value = message(types.MessageActionChatEditTitle('合成'))
        update = types.UpdateNewMessage(value, pts=1, pts_count=1)
        self.assertIsNone(events.NewMessage.build(update))
        self.assertIs(GroupServiceMessage.build(update).message, value)
        update = types.UpdateNewMessage(types.Message(id=11, peer_id=types.PeerChat(42), message='普通文字', date=value.date), pts=2, pts_count=1)
        self.assertIsNone(GroupServiceMessage.build(update))
        self.assertIsNone(GroupServiceMessage.build(types.UpdateChatParticipantDelete(chat_id=42, user_id=2, version=1)))


class ServiceArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.service = Service(self.folder.name)
        self.service.account = 100; self.service.auth = 'ready'
        self.service.store.db.execute("INSERT INTO accounts VALUES(100,'合成账号')"); self.service.store.db.commit()
        self.service.store.put_conversation(100, -42, '合成群', 'group')
        self.service.store.select(100, -42, True)
        self.service.client = SimpleNamespace(get_messages=AsyncMock(), is_connected=lambda:True)
        self.date = datetime.now(timezone.utc)
        cfg=self.service.settings();cfg['rule'].update(automatic=True, target=55, armed_at=time.time());self.service.store.save_settings(100,cfg)
        self.before = self.service.settings()
        self.service.store.put_messages(100, -42, [{'id':10, 'date':self.date.timestamp(), 'sender':'旧发送者', 'text':'', 'attachment':''}])

    async def asyncTearDown(self):
        self.service.store.close(); self.folder.cleanup()

    async def test_archive_system_content_is_searchable_and_does_not_change_configuration(self):
        value = message(types.MessageActionChatEditTitle('新群名'), date=self.date)
        value.get_sender = AsyncMock(return_value=value._sender)
        await self.service.archive_messages(-42, [value])
        result = self.service.store.search(100, query='新群名')['messages']
        self.assertEqual(len(result), 1); self.assertEqual(result[0]['attachment'], '群组系统消息')
        self.assertEqual(self.service.settings(), self.before)

    async def test_realtime_service_event_never_calls_auto_reply_and_respects_selection(self):
        value = message(types.MessageActionChatAddUser([2]), identifier=11)
        value.get_sender = AsyncMock(return_value=value._sender)
        self.service.automation = SimpleNamespace(handle=AsyncMock())
        event = SimpleNamespace(chat_id=-42, message=value)
        await self.service.group_service_message(event)
        self.assertIn('新成员', self.service.store.search(100, query='新成员')['messages'][0]['text'])
        self.service.automation.handle.assert_not_awaited()
        self.service.store.select(100, -42, False)
        value.id = 12; await self.service.group_service_message(event)
        self.assertFalse(self.service.store.db.execute('select 1 from messages where id=12').fetchone())
        self.service.store.select(100, -42, True)
        cfg=self.service.settings();cfg['archive_paused']=True;self.service.store.save_settings(100,cfg)
        value.id=13;await self.service.group_service_message(event)
        self.assertFalse(self.service.store.db.execute('select 1 from messages where id=13').fetchone())

    async def preview(self):
        return await self.service.command('archive_repair_preview', {'chat':-42})

    async def apply(self, preview):
        return await self.service.command('archive_repair_apply', {'token':preview['token'], 'confirmed_account':100})

    async def test_repair_preview_is_readonly_and_apply_preserves_cursor_settings_and_ledger(self):
        self.service.store.put_messages(100, -42, [], cursor=500, initialized=True)
        preview=await self.preview();self.assertEqual(preview['count'],1)
        self.service.client.get_messages.assert_not_awaited()
        self.assertEqual(self.service.store.search(100)['messages'][0]['text'], '')
        self.service.client.get_messages.return_value=[message(types.MessageActionChatEditTitle('补全名称'), date=self.date)]
        result=await self.apply(preview)
        self.assertEqual(result['repaired'],1);self.assertEqual(result['remaining'],0)
        self.assertIn('补全名称',self.service.store.search(100)['messages'][0]['text'])
        self.assertEqual(self.service.settings(),self.before)
        self.assertEqual(self.service.store.conversation(100,-42)['cursor'],500)
        self.assertEqual(self.service.store.db.execute('select count(*) from executions').fetchone()[0],0)
        with self.assertRaises(ValueError):await self.apply(preview)

    async def test_missing_unavailable_and_non_service_records_remain_honest(self):
        for remote in (None, SimpleNamespace(id=10, chat_id=-42, date=None), SimpleNamespace(id=10,chat_id=-42,date=self.date,action=None)):
            with self.subTest(remote=type(remote).__name__):
                preview=await self.preview();self.service.client.get_messages.return_value=[remote]
                result=await self.apply(preview)
                self.assertEqual(result['repaired'],0);self.assertEqual(result['remaining'],1)
                self.assertEqual(self.service.store.search(100)['messages'][0]['text'],'')

    async def test_repair_rejects_wrong_identity_expiry_changed_deleted_and_missing_records(self):
        preview=await self.preview()
        with self.assertRaises(ValueError):await self.service.command('archive_repair_apply',{'token':preview['token'],'confirmed_account':200})
        self.service.archive_repair['created']=time.time()-301
        with self.assertRaises(ValueError):await self.apply(preview)
        preview=await self.preview();self.service.store.put_messages(100,-42,[{'id':10,'date':self.date.timestamp(),'sender':'旧发送者','text':'新内容'}])
        with self.assertRaises(ValueError):await self.apply(preview)
        self.service.client.get_messages.assert_not_awaited()

    async def test_repair_rechecks_after_network_await_and_does_not_restore_deleted_content(self):
        preview=await self.preview()
        async def fetch(*args,**kwargs):
            self.service.store.delete_messages(100,[10],-42)
            return [message(types.MessageActionChatEditTitle('不应复活'),date=self.date)]
        self.service.client.get_messages.side_effect=fetch
        with self.assertRaises(ValueError):await self.apply(preview)
        row=self.service.store.search(100,include_deleted=True)['messages'][0]
        self.assertTrue(row['deleted']);self.assertEqual(row['text'],'')

    async def test_repair_limits_scope_to_one_group_and_100_rows_and_never_crosses_accounts(self):
        for i in range(11,121):self.service.store.put_messages(100,-42,[{'id':i,'date':self.date.timestamp(),'sender':'合成','text':''}])
        self.service.store.put_messages(200,-42,[{'id':999,'date':self.date.timestamp(),'sender':'其他账号','text':''}])
        preview=await self.preview();self.assertEqual(preview['count'],100);self.assertEqual(preview['total'],111)
        following=await self.service.command('archive_repair_preview',{'chat':-42,'after_id':preview['next_id']})
        self.assertEqual(following['count'],11);self.assertFalse(following['has_more'])
        with self.assertRaises(ValueError):await self.service.command('archive_repair_preview',{'chat':0})
        self.service.store.put_conversation(100,99,'合成私聊','private')
        with self.assertRaises(ValueError):await self.service.command('archive_repair_preview',{'chat':99})

    async def test_repair_network_failure_and_wrong_chat_or_date_do_not_write(self):
        preview=await self.preview();self.service.client.get_messages.side_effect=OSError('synthetic network error')
        with self.assertRaises(ValueError):await self.apply(preview)
        self.assertEqual(self.service.settings(),self.before)
        self.service.client.get_messages.side_effect=None
        for remote in (message(types.MessageActionChatEditTitle('错误'),chat=43,date=self.date),message(types.MessageActionChatEditTitle('错误'),date=datetime.fromtimestamp(1,timezone.utc))):
            preview=await self.preview();self.service.client.get_messages.return_value=[remote]
            result=await self.apply(preview);self.assertEqual(result['repaired'],0)
        self.assertEqual(self.service.store.search(100)['messages'][0]['text'],'')

    async def test_repair_preserves_enabled_tasks_on_rate_limit_and_generation_changes(self):
        preview=await self.preview();self.service.client.get_messages.side_effect=FloodWaitError(request=None,capture=60)
        with self.assertRaises(FloodWaitError):await self.apply(preview)
        self.assertEqual(self.service.settings(),self.before)
        self.service.client.get_messages.side_effect=None
        preview=await self.preview();self.service.generation+=1
        with self.assertRaises(ValueError):await self.apply(preview)
        self.assertEqual(self.service.settings(),self.before)

    async def test_repair_honors_pause_quota_and_protected_content(self):
        preview=await self.preview();cfg=self.service.settings();cfg['archive_paused']=True;self.service.store.save_settings(100,cfg)
        with self.assertRaises(ValueError):await self.apply(preview)
        self.service.client.get_messages.assert_not_awaited()
        self.service.store.save_settings(100,self.before)
        value=message(types.MessageActionChatEditTitle('保护'),date=self.date);value.ttl_period=30
        self.service.client.get_messages.return_value=[value]
        preview=await self.preview();result=await self.apply(preview);self.assertEqual(result['protected'],1)
        value.ttl_period=None;preview=await self.preview()
        with patch.object(self.service,'quota_bytes',return_value=0):
            with self.assertRaises(ValueError):await self.apply(preview)
        self.assertEqual(self.service.store.search(100)['messages'][0]['text'],'')
        self.assertEqual(self.service.settings(),self.before)

    async def test_service_text_survives_export_backup_restore_and_reopen_without_schema_change(self):
        value=message(types.MessageActionChatEditTitle('可搜索事件'),date=self.date);value.get_sender=AsyncMock(return_value=value._sender)
        await self.service.archive_messages(-42,[value])
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'export.json';self.service.export({'path':str(output),'format':'json'})
            self.assertIn('可搜索事件',output.read_text())
            archive=Path(folder)/'backup.zip';create_backup(self.service.root,100,archive)
            with tempfile.TemporaryDirectory() as extracted:
                manifest=inspect_backup(archive,100,extracted)
                target=Store(Path(folder)/'restored')
                try:
                    restore_backup(target,100,extracted,manifest)
                    self.assertIn('可搜索事件',target.search(100)['messages'][0]['text'])
                    self.assertEqual(target.db.execute('pragma user_version').fetchone()[0],5)
                finally:target.close()
            self.service.store.close();self.service.store=Store(self.service.root)
            self.assertIn('可搜索事件',self.service.store.search(100)['messages'][0]['text'])
