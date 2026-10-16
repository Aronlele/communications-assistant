import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from engine.service import Service
from engine.accounts import AccountManager
from engine.store import Store
from engine.network import configuration, telethon_proxy, probe
from engine.encrypted_backup import encrypt, decrypt, is_encrypted
from engine.backup import create_backup, inspect_backup, restore_backup
from engine.match_explanation import explain
from engine.upgrade_backup import create as upgrade_backup


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.service = Service(self.root/'account', demo=True); await self.service.seed_demo()
        cfg = self.service.settings();cfg['rule'].update(target=1003, mode='fixed', match_text='抽查\n请回复', reply_text='1', automatic=True, preview_validated=True, armed_at=123)
        self.service.store.save_settings(self.service.account,cfg)

    async def asyncTearDown(self):
        for t in list(self.service.tasks): t.cancel()
        self.service.store.close();self.temp.cleanup()

    async def test_draft_explanation_locates_difference_and_never_changes_existing_task(self):
        before = copy.deepcopy(self.service.settings())
        draft = dict(before['rule'])
        result = await self.service.command('task_draft_preview', {'draft':draft,'text':'抽查\n请 回应'})
        self.assertFalse(result['recognized']);self.assertEqual((result['line'],result['column']),(2,2))
        self.assertIn('空格',result['explanation']);self.assertEqual(self.service.settings(),before)
        result = await self.service.command('task_draft_preview', {'draft':draft,'text':'  抽查\r\n请回复\n'})
        self.assertTrue(result['recognized']);self.assertEqual(self.service.settings(),before)
        draft['match_mode']='contains'
        self.assertEqual(explain('普通消息',draft)['match_reason'],'missing_text')
        self.assertEqual(explain('',draft)['match_reason'],'empty')

    async def test_wizard_save_validates_new_task_without_disarming_original(self):
        before = copy.deepcopy(self.service.settings()['rule'])
        saved = await self.service.command('task_save',dict(id='new',name='向导任务',target='1003',mode='fixed',match_text='新提示',reply_text='2',sample_text='新提示'))
        preview = await self.service.command('task_preview',{'id':saved['saved_task_id'],'text':'新提示'})
        self.assertTrue(preview['recognized']);self.assertEqual(self.service.settings()['rule'],before)
        task=self.service.settings()['tasks'][0];self.assertFalse(task['automatic']);self.assertTrue(task['preview_validated'])

    async def test_proxy_validation_and_credentials_never_persist_in_state(self):
        for value in ({'mode':'mtproto'},{'mode':'http','host':'https://proxy','port':80},{'mode':'socks5','host':'x','port':True},{'mode':'http','host':'x','port':65536}):
            with self.assertRaises(ValueError):configuration(value)
        self.service.demo=False;self.service.auth='ready';self.service.client=SimpleNamespace(is_connected=lambda:True)
        before=copy.deepcopy(self.service.settings())
        cfg=dict(mode='socks5',host='localhost',port=1080,username='synthetic',requires_password=True,password='synthetic-secret')
        await self.service.command('network_save',{'proxy':cfg})
        self.assertEqual(self.service.settings(),before)
        self.assertNotIn('synthetic-secret',json.dumps(self.service.state()))
        self.assertNotIn('password',json.loads(self.service.store.preference('network_proxy')))
        self.assertEqual(telethon_proxy(cfg)['proxy_type'],'socks5')
        with self.assertRaises(ValueError):telethon_proxy(configuration(cfg))
        self.assertIsNone(telethon_proxy({'mode':'direct','password':'not-forwarded'}))
        self.service.client=None

    async def test_network_apply_requires_identity_and_remembers_only_original_enabled_task(self):
        self.service.demo=False;self.service.auth='ready'
        self.service.client=SimpleNamespace(is_connected=lambda:True,api_id=123,api_hash='0'*32)
        before=copy.deepcopy(self.service.settings())
        with self.assertRaises(ValueError): await self.service.command('network_apply',{'proxy':{'mode':'direct'},'confirmed_account':0})
        self.assertEqual(self.service.settings(),before)
        self.service.send_jobs.add('synthetic')
        with self.assertRaises(ValueError): await self.service.command('network_apply',{'proxy':{'mode':'direct'},'confirmed_account':self.service.account})
        self.service.send_jobs.clear()
        with patch.object(self.service,'connect',AsyncMock(return_value={'ok':True})) as connect:
            await self.service.command('network_apply',{'proxy':{'mode':'direct'},'confirmed_account':self.service.account})
            self.assertEqual(connect.await_args.args[0]['proxy'],{'mode':'direct'})
        cfg=self.service.settings();self.assertFalse(cfg['rule']['automatic']);self.assertIn('legacy',cfg['resume_tasks'])
        self.service.client=None

    async def test_direct_probe_uses_separate_socket_and_reports_failures(self):
        async def handler(reader,writer):writer.close();await writer.wait_closed()
        server=await asyncio.start_server(handler,'127.0.0.1',0)
        try:
            address=server.sockets[0].getsockname()[:2]
            with patch('engine.network.DESTINATION',address):self.assertTrue((await probe({'mode':'direct'}))['ok'])
        finally:server.close();await server.wait_closed()
        with patch('engine.network.DESTINATION',address):self.assertFalse((await probe({'mode':'direct'},timeout=.1))['ok'])

    async def test_socks5_real_handshake_success_auth_failure_and_timeout(self):
        async def handler(reader,writer):
            try:
                version,n=await reader.readexactly(2);await reader.readexactly(n)
                writer.write(b'\x05\x02');await writer.drain()
                version,n=await reader.readexactly(2);user=await reader.readexactly(n)
                n=(await reader.readexactly(1))[0];password=await reader.readexactly(n)
                good=user==b'user' and password==b'correct'
                writer.write(b'\x01\x00' if good else b'\x01\x01');await writer.drain()
                if not good:return
                head=await reader.readexactly(4)
                if head[3]==1:await reader.readexactly(6)
                elif head[3]==3:n=(await reader.readexactly(1))[0];await reader.readexactly(n+2)
                writer.write(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x01');await writer.drain()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(handler,'127.0.0.1',0)
        try:
            cfg=dict(mode='socks5',host='127.0.0.1',port=server.sockets[0].getsockname()[1],username='user',requires_password=True,password='correct')
            self.assertTrue((await probe(cfg))['ok'])
            bad=await probe(dict(cfg,password='wrong'));self.assertFalse(bad['ok']);self.assertNotIn('wrong',json.dumps(bad))
        finally:server.close();await server.wait_closed()
        with patch('engine.network.asyncio.open_connection',AsyncMock(side_effect=asyncio.TimeoutError)):
            self.assertFalse((await probe({'mode':'direct'},timeout=.01))['ok'])

    async def test_http_connect_real_handshake(self):
        requests=[]
        async def handler(reader,writer):
            try:
                requests.append(await reader.readuntil(b'\r\n\r\n'));writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n');await writer.drain()
            finally:writer.close();await writer.wait_closed()
        server=await asyncio.start_server(handler,'127.0.0.1',0)
        try:
            result=await probe(dict(mode='http',host='127.0.0.1',port=server.sockets[0].getsockname()[1]))
            self.assertTrue(result['ok']);self.assertTrue(requests[0].startswith(b'CONNECT '))
        finally:server.close();await server.wait_closed()

    async def test_encrypted_business_backup_roundtrip_and_plain_compatibility(self):
        password='synthetic-password';archive=self.root/'business.jixunbackup'
        counts=await asyncio.to_thread(create_backup,self.service.root,self.service.account,archive,password)
        self.assertTrue(is_encrypted(archive));self.assertEqual(archive.stat().st_mode&0o777,0o600)
        self.assertNotIn(password.encode(),archive.read_bytes());self.assertNotIn(b'PK\x03\x04',archive.read_bytes())
        preview=await asyncio.to_thread(inspect_backup,archive,self.service.account,password=password)
        self.assertEqual(preview['digest'],hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(preview['counts'],counts)
        with tempfile.TemporaryDirectory() as extracted:
            manifest=await asyncio.to_thread(inspect_backup,archive,self.service.account,extracted,password=password)
            target=Store(self.root/'restored')
            try:
                restore_backup(target,self.service.account,extracted,manifest)
                self.assertEqual(target.search(self.service.account)['count'],counts['messages'])
                self.assertFalse(target.settings(self.service.account)['rule']['automatic'])
            finally:target.close()
        plain=self.root/'plain.zip';create_backup(self.service.root,self.service.account,plain)
        self.assertEqual(inspect_backup(plain,self.service.account)['counts'],counts)

    async def test_wrong_password_tamper_and_wrong_account_never_modify_original(self):
        archive=self.root/'encrypted';create_backup(self.service.root,self.service.account,archive,'synthetic-password')
        before=self.service.store.db.total_changes;settings=copy.deepcopy(self.service.settings())
        for secret in ('','bad-password'):
            with self.assertRaises(ValueError):inspect_backup(archive,self.service.account,password=secret)
        with self.assertRaises(ValueError):inspect_backup(archive,123,password='synthetic-password')
        data=bytearray(archive.read_bytes());data[60]^=1;archive.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'密码错误或备份已损坏'):inspect_backup(archive,self.service.account,password='synthetic-password')
        self.assertEqual(self.service.store.db.total_changes,before);self.assertEqual(self.service.settings(),settings)
        self.assertEqual(list(self.root.glob('.jixun-encrypted-*')),[])

    async def test_restore_wrong_password_restores_original_enabled_configuration(self):
        archive=self.root/'encrypted';await self.service.command('backup',{'path':str(archive),'password':'synthetic-password'})
        preview=await self.service.command('backup_preview',{'path':str(archive),'password':'synthetic-password'})
        before=copy.deepcopy(self.service.settings())
        with patch.object(self.service.store,'save_settings',wraps=self.service.store.save_settings) as save:
            with self.assertRaises(ValueError):await self.service.command('restore_backup',{'path':str(archive),'password':'wrong-password','confirmed_account':self.service.account,'digest':preview['digest']})
            save.assert_not_called()
        self.assertEqual(self.service.settings(),before)

    async def test_encryption_weak_password_and_output_failure_preserve_existing_file(self):
        plain=self.root/'plain';plain.write_bytes(b'synthetic payload');destination=self.root/'saved';destination.write_bytes(b'existing')
        with self.assertRaises(ValueError):encrypt(plain,destination,'short')
        self.assertEqual(destination.read_bytes(),b'existing')
        encrypt(plain,self.root/'encrypted','synthetic-password')
        with self.assertRaises(FileExistsError):decrypt(self.root/'encrypted',destination,'synthetic-password')
        self.assertEqual(destination.read_bytes(),b'existing')


class AccountRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'data';self.manager=AccountManager(self.root)
        self.slot=str(uuid.uuid4());store=Store(self.root/'accounts'/self.slot)
        store.db.execute('INSERT INTO accounts VALUES(?,?)',(555,'合成保留账号'));store.db.commit();store.preference('last_account',555)
        cfg=store.settings(555);cfg['rule'].update(target=1003,automatic=True,preview_validated=True,armed_at=100);store.save_settings(555,cfg);store.close()

    async def asyncTearDown(self):await self.manager.close();self.temp.cleanup()

    async def test_retained_preview_is_readonly_and_restore_disarms_only_recovered_entry(self):
        current=self.manager.services['legacy'];current.account=111;current.identity={'id':111};current.auth='ready'
        before=current.settings();cfg=current.store.settings(111);cfg['rule'].update(target=1003,automatic=True,preview_validated=True);current.store.save_settings(111,cfg)
        before=current.settings();rows=(await self.manager.command('legacy','retained_accounts',{}))['rows'];row=rows[0]
        self.assertEqual(row['account'],555);self.assertNotIn(self.slot,self.manager.services)
        result=await self.manager.command('legacy','restore_account_entry',dict(slot=self.slot,digest=row['digest'],confirmed_account=555))
        self.assertEqual(result['slot'],self.slot);self.assertEqual(current.settings(),before)
        restored=self.manager.services[self.slot];self.assertFalse(restored.settings()['rule']['automatic']);self.assertIn('legacy',restored.settings()['resume_tasks'])

    async def test_changed_or_duplicate_identity_rejected_without_registry_mutation(self):
        row=(await self.manager.command('legacy','retained_accounts',{}))['rows'][0]
        with self.assertRaises(ValueError):await self.manager.command('legacy','restore_account_entry',dict(slot=self.slot,digest='stale',confirmed_account=555))
        self.manager.services['legacy'].account=555
        with self.assertRaises(ValueError):await self.manager.command('legacy','restore_account_entry',dict(slot=self.slot,digest=row['digest'],confirmed_account=555))
        self.assertEqual(list(self.manager.services),['legacy'])
        for value in ('../escape',str(uuid.uuid4()).upper()):
            with self.assertRaises(ValueError):await self.manager.command('legacy','restore_account_entry',dict(slot=value,digest='',confirmed_account=555))

    async def test_symlink_retained_data_not_followed_or_restored(self):
        other=str(uuid.uuid4());(self.root/'accounts'/other).symlink_to(self.root/'accounts'/self.slot,target_is_directory=True)
        rows=(await self.manager.command('legacy','retained_accounts',{}))['rows'];self.assertTrue(next(r for r in rows if r['slot']==other)['unavailable'])
        with self.assertRaises(ValueError):await self.manager.command('legacy','restore_account_entry',dict(slot=other,digest='',confirmed_account=555))

    async def test_upgrade_snapshot_and_preflight_failure_preserve_running_configuration(self):
        self.manager.save_registry();service=self.manager.services['legacy'];service.account=111;service.auth='ready'
        cfg=service.settings();cfg['rule'].update(target=1003,automatic=True);service.store.save_settings(111,cfg)
        result=await self.manager.command('legacy','upgrade_prepare',dict(destination=str(Path(self.temp.name)/'upgrade'),confirmed_slots=['legacy']))
        backup=Path(result['backup_root']);self.assertTrue((backup/'data/accounts.json').is_file());self.assertTrue(result['contains_login_sessions'])
        self.assertEqual(service.settings(),cfg);self.assertTrue(service.settings()['rule']['automatic'])
        with self.assertRaises(ValueError):await self.manager.command('legacy','upgrade_prepare',dict(destination=str(self.root/'nested'),confirmed_slots=['legacy']))
        service.send_jobs.add('pending')
        with self.assertRaises(ValueError):await self.manager.command('legacy','upgrade_prepare',dict(destination=str(Path(self.temp.name)/'no-upgrade'),confirmed_slots=['legacy']))
        service.send_jobs.clear();self.assertEqual(service.settings(),cfg)

    async def test_foreign_backup_and_corrupt_retained_directory_do_not_pause_existing_account(self):
        service=self.manager.services['legacy'];service.account=111;service.auth='ready'
        cfg=service.settings();cfg['rule'].update(target=1003,automatic=True);service.store.save_settings(111,cfg)
        captured=[]
        async def emit(row):captured.append(row)
        self.manager.emit=emit
        with patch('engine.accounts.create_upgrade_backup',side_effect=OSError(28,'synthetic full backup volume')):
            await self.manager.dispatch({'protocol':1,'id':'one','slot':'legacy','method':'upgrade_prepare','args':{'destination':str(Path(self.temp.name)/'failed'),'confirmed_slots':['legacy']}})
        self.assertIn('error',captured[-1]);self.assertEqual(service.settings(),cfg);self.assertFalse(service.quota_paused)
        broken=str(uuid.uuid4());folder=self.root/'accounts'/broken;folder.mkdir();(folder/'assistant.sqlite').write_bytes(b'not a sqlite database')
        await self.manager.dispatch({'protocol':1,'id':'two','slot':'legacy','method':'restore_account_entry','args':{'slot':broken,'digest':'x','confirmed_account':555}})
        self.assertIn('error',captured[-1]);self.assertEqual(service.settings(),cfg);self.assertFalse(service.quota_paused)
        self.assertNotIn(broken,self.manager.services)

    async def test_registry_write_failure_restores_original_entry_and_active_selection(self):
        row=(await self.manager.command('legacy','retained_accounts',{}))['rows'][0]
        with patch.object(self.manager,'save_registry',side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):await self.manager.command('legacy','restore_account_entry',dict(slot=self.slot,digest=row['digest'],confirmed_account=555))
        self.assertEqual(list(self.manager.services),['legacy']);self.assertEqual(self.manager.active,'legacy')
