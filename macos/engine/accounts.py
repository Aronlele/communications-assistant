"""一个受限 IPC 服务管理多个独立账号，不迁移旧账号的数据库和会话。"""
import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path

from telethon.errors import FloodWaitError

from engine.service import PROTOCOL, Service, storage_error


class AccountService(Service):
    def __init__(self, manager, slot, root):
        self.manager, self.slot = manager, slot
        super().__init__(root, manager.demo, manager.system_zone)

    async def emit(self, kind, data):
        if kind == "state":
            data = self.manager.decorate(self.slot, data)
        await self.manager.emit({"protocol": PROTOCOL, "event": kind, "slot": self.slot, "data": data})

    def write_runtime_status(self):
        self.manager.write_runtime_status()


class AccountManager:
    def __init__(self, root, demo=False, system_zone="Asia/Shanghai"):
        self.root, self.demo, self.system_zone = Path(root), demo, system_zone
        self.stop = asyncio.Event()
        self.writer = None
        self.write_lock = asyncio.Lock()
        self.registry_lock = asyncio.Lock()
        self.locks = {}
        self.services = {}
        self.maintenance = {}
        self.requests = set()
        self.running = False
        path = self.root / 'accounts.json'
        registry = json.loads(path.read_text()) if path.exists() else {'slots': ['legacy'], 'active': 'legacy'}
        slots = registry.get('slots', [])
        if not slots or slots[0] != 'legacy' or len(set(slots)) != len(slots):
            raise ValueError('账号目录损坏，请保留数据并恢复账号目录备份')
        if demo and len(slots) == 1:
            slots = slots + ['00000000-0000-4000-8000-000000000002']
        for slot in slots:
            if slot != 'legacy' and (not isinstance(slot, str) or str(uuid.UUID(slot)) != slot):
                raise ValueError('账号目录标识无效')
            self.add_service(slot)
        self.active = registry.get('active', 'legacy')
        if self.active not in self.services:
            raise ValueError('当前账号目录不存在')

    def add_service(self, slot):
        if slot != 'legacy':
            (self.root / 'accounts').mkdir(exist_ok=True, mode=0o700)
            os.chmod(self.root / 'accounts', 0o700)
        root = self.root if slot == 'legacy' else self.root / 'accounts' / slot
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        service = AccountService(self, slot, root)
        self.services[slot] = service
        self.locks[slot] = asyncio.Lock()
        if self.running:
            self.maintenance[slot] = asyncio.create_task(service.maintenance())
        return service

    def save_registry(self):
        path = self.root / 'accounts.json'
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as file:
            os.chmod(temporary, 0o600)
            json.dump({'slots': list(self.services), 'active': self.active}, file)
        temporary.replace(path)

    def account_list(self):
        return [{'slot': slot, 'account': service.account, 'identity': service.identity,
                 'auth': service.auth, 'connected': bool(service.demo or (service.client and service.client.is_connected() and service.auth == 'ready')),
                 'automatic': service.settings()['rule']['automatic'],
                 'pause_all': service.settings()['pause_all'], 'error_present': bool(service.error),
                 'data_directory': '' if slot == 'legacy' else 'accounts/' + slot}
                for slot, service in self.services.items()]

    def decorate(self, slot, state):
        return dict(state, slot=slot, active_slot=self.active, accounts=self.account_list(),
                    data_directory='' if slot == 'legacy' else 'accounts/' + slot)

    async def emit(self, message):
        async with self.write_lock:
            if self.writer and not self.writer.is_closing():
                self.writer.write((json.dumps(message, ensure_ascii=False) + '\n').encode())
                await self.writer.drain()

    async def command(self, slot, method, args):
        if method == 'shutdown':
            for service in self.services.values():
                service.closing = True
            return {'ok': True}
        if method in ('add_account', 'select_account'):
            async with self.registry_lock:
                if method == 'add_account':
                    if self.demo:
                        raise ValueError('请在真实模式中添加 Telegram 账号')
                    # 复用未登录的空入口，取消登录后不积累空目录。
                    empty = next((key for key, value in self.services.items()
                                  if value.auth == 'disconnected' and not value.store.db.execute('SELECT 1 FROM accounts').fetchone()), None)
                    self.active = empty or str(uuid.uuid4())
                    if not empty:
                        self.add_service(self.active)
                else:
                    selected = args.get('slot')
                    if selected not in self.services:
                        raise ValueError('账号不存在，请刷新账号列表')
                    self.active = selected
                self.save_registry()
                await self.services[self.active].publish()
                return self.decorate(self.active, self.services[self.active].state())
        if slot not in self.services:
            raise ValueError('账号不存在，操作未执行')
        service = self.services[slot]
        async with self.locks[slot]:
            if method == 'confirm_identity':
                identity = service.identity.get('id')
                if identity and any(identity in (other.account, other.identity.get('id'))
                                    for key, other in self.services.items() if key != slot):
                    raise ValueError('这个 Telegram 账号已经添加，请切换到已有账号；可以取消本次登录')
            if method == 'demo_seed' and self.demo:
                index = list(self.services).index(slot)
                await service.seed_demo(999001 + index, '本地演示账号' if index == 0 else f'第二演示账号')
                await service.publish()
                return self.decorate(slot, service.state())
            if method == 'cancel_login' and service.auth == 'identity':
                cached_account = int(service.store.preference('last_account') or 0)
                cached = service.store.db.execute('SELECT name FROM accounts WHERE id=?', (cached_account,)).fetchone()
                revoked = True
                # 新入口取消时撤销它自己的新会话；恢复已有入口只断开连接，保留离线数据。
                if service.client:
                    if not cached:
                        try:
                            revoked = bool(await asyncio.wait_for(service.client.log_out(), 10))
                        except Exception:
                            revoked = False
                    await service.client.disconnect()
                service.client = None
                service.generation += 1
                service.identity = {'id': cached_account, 'name': cached['name']} if cached else {}
                service.account = cached_account if cached else 0
                service.auth = 'offline' if cached else 'disconnected'
                if not cached:
                    for path in service.root.glob('telegram.session*'):
                        path.unlink(missing_ok=True)
                service.error = '' if revoked else '本地登录已取消，远端撤销未确认，请在 Telegram 设备管理中核对新会话'
                await service.publish()
                return self.decorate(slot, service.state())
            if method in ('export', 'backup'):
                if Path(args['path']).resolve().is_relative_to(self.root.resolve()):
                    raise ValueError('请保存到应用数据目录以外，避免覆盖其他账号的数据')
            result = await service.command(method, args)
            return self.decorate(slot, result) if isinstance(result, dict) and 'auth' in result else result

    def write_runtime_status(self):
        rows = [dict(service.runtime_status(), slot=slot) for slot, service in self.services.items()]
        value = dict(self.services[self.active].runtime_status(), accounts=rows, account_count=len(rows))
        value['connected'] = all(row['connected'] for row in rows if row['auth'] == 'ready') and any(row['connected'] for row in rows)
        value['pending_sends'] = sum(row['pending_sends'] for row in rows)
        value['error_present'] = any(row['error_present'] for row in rows)
        path = self.root / 'runtime-status.json'
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as file:
            os.chmod(temporary, 0o600)
            json.dump(value, file)
        temporary.replace(path)

    async def dispatch(self, request):
        identifier = request.get('id')
        slot = request.get('slot', self.active)
        service = self.services.get(slot)
        try:
            if request.get('protocol') != PROTOCOL:
                raise ValueError('协议版本不兼容')
            result = await asyncio.wait_for(self.command(slot, request['method'], request.get('args', {})), 90)
            response = {'id': identifier, 'slot': slot, 'result': result}
        except FloodWaitError as exc:
            if service:
                import time
                service.cooldown = time.time() + exc.seconds
            response = {'id': identifier, 'slot': slot, 'error': f'Telegram 要求等待 {exc.seconds} 秒，请稍后再试'}
        except (ValueError, KeyError) as exc:
            response = {'id': identifier, 'slot': slot, 'error': str(exc) if isinstance(exc, ValueError) else '请求参数缺失'}
        except Exception as exc:
            if service and storage_error(exc):
                service.fail_closed()
            response = {'id': identifier, 'slot': slot, 'error': f'操作未完成（{type(exc).__name__}），请检查网络或输入后重试'}
        await self.emit(response)
        if request.get('method') == 'shutdown' and 'error' not in response:
            self.stop.set()

    async def handle_socket(self, reader, writer):
        if self.writer and not self.writer.is_closing():
            writer.close()
            return
        self.writer = writer
        for service in self.services.values():
            await service.publish()
        try:
            while not self.stop.is_set():
                line = await reader.readline()
                if not line:
                    break
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError('请求无效')
                task = asyncio.create_task(self.dispatch(request))
                self.requests.add(task)
                task.add_done_callback(self.request_done)
        except (ConnectionError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            self.stop.set()
            writer.close()

    def request_done(self, task):
        self.requests.discard(task)
        if not task.cancelled() and task.exception():
            self.stop.set()

    async def close(self):
        for service in self.services.values():
            service.closing = True
            service.stop.set()
        tasks = list(self.maintenance.values()) + list(self.requests)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(service.close() for service in self.services.values()))

    async def run(self, socket):
        if self.demo:
            for index, service in enumerate(self.services.values()):
                await service.seed_demo(999001 + index, '本地演示账号' if index == 0 else '第二演示账号')
        self.running = True
        self.save_registry()
        self.maintenance = {slot: asyncio.create_task(service.maintenance()) for slot, service in self.services.items()}
        server = await asyncio.start_unix_server(self.handle_socket, path=socket, limit=2 * 1024 * 1024)
        os.chmod(socket, 0o600)
        try:
            await self.stop.wait()
        finally:
            server.close()
            if self.writer:
                self.writer.close()
                with contextlib.suppress(ConnectionError):
                    await self.writer.wait_closed()
            await server.wait_closed()
            await self.close()
            Path(socket).unlink(missing_ok=True)
