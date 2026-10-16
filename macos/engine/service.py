import argparse
import asyncio
import contextlib
import errno
import fcntl
import json
import os
import signal
import shutil
import sqlite3
import sys
import time
import tempfile
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, events, functions, types
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError, PasswordHashInvalidError, PhoneNumberInvalidError, PhoneNumberBannedError

from engine.archive_cleanup import command as archive_cleanup_command
from engine.issues import issues
from engine.network import command as network_command, configuration as proxy_configuration, telethon_proxy
from engine.automation import Automation
from engine.importer import read_legacy, legacy_image, read_legacy_ledger
from engine.schedule import active, intervals, validate
from engine.store import DEFAULTS, Store
from engine.profile import VERSION, validate_profile
from engine.backup import create_backup, inspect_backup, restore_backup
from engine.reply_rule import parse_reply, validate_template
from engine.task_templates import evaluate_cases
from engine.tasks import task_rule, disarm_all, forget_resume
from engine.task_commands import command as task_command, task_state


PROTOCOL = 1


def storage_error(exc):
    return isinstance(exc, sqlite3.Error) or (
        isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT, errno.EACCES, errno.EROFS, errno.EIO))


class Service:
    def __init__(self, root, demo=False, system_zone="Asia/Shanghai"):
        self.root = Path(root)
        self.store = Store(root)
        self.demo = demo
        self.system_zone = system_zone
        self.archive_cleanup = None
        self.client = None
        self.account = 0
        self.auth = "disconnected"
        self.identity = {}
        self.writer = None
        self.write_lock = asyncio.Lock()
        self.stop = asyncio.Event()
        self.started = time.time()
        self.last_sync = 0
        self.error = ""
        self.phone = ""
        self.phone_code_hash = None
        self.entities = {}
        self.tasks = set()
        self.chat_locks = defaultdict(asyncio.Lock)
        self.notice_tasks = {}
        self.notice_counts = defaultdict(int)
        self.notice_message_ids = {}
        self.reminder_seen = OrderedDict()
        self.automation = None
        self.online_active = False
        self.cooldown = 0
        self.quota_paused = False
        self.last_reconcile = 0
        self.was_connected = False
        self.generation = 0
        self.quota_warned = False
        self.reconnect_at = 0
        self.reconnect_delay = 2
        self.online_error_until = 0
        self.online_last_success = 0
        self.closing = False
        self.send_jobs = set()
        self.download_slots = asyncio.Semaphore(2)
        saved_profile = self.store.preference("device_profile")
        self.device_profile = validate_profile(json.loads(saved_profile) if saved_profile else {})
        self.active_profile = None
        self.server_profile = {}
        self.code_resend_at = 0
        saved_countries = self.store.preference('countries')
        self.countries = json.loads(saved_countries) if saved_countries else [
            {'key': 'CN:86', 'name': '中国 +86', 'code': '86'}, {'key': 'HK:852', 'name': '中国香港 +852', 'code': '852'},
            {'key': 'TW:886', 'name': '中国台湾 +886', 'code': '886'}, {'key': 'US:1', 'name': '美国 +1', 'code': '1'},
            {'key': 'SG:65', 'name': '新加坡 +65', 'code': '65'}, {'key': 'MY:60', 'name': '马来西亚 +60', 'code': '60'},
            {'key': 'VN:84', 'name': '越南 +84', 'code': '84'}, {'key': 'TH:66', 'name': '泰国 +66', 'code': '66'}]
        self.active_proxy = None
        self.connection_proxy = {}
        self.media_retry_task = None
        self.syncing_chats = set()
        cached_account = int(self.store.preference("last_account") or 0)
        cached = self.store.db.execute("SELECT * FROM accounts WHERE id=?", (cached_account,)).fetchone()
        if cached and not demo:
            self.account = cached_account
            self.identity = {"id": cached_account, "name": cached["name"]}
            avatar = Path('avatars') / (str(cached_account) + '.jpg')
            if (self.root / avatar).is_file():
                self.identity['avatar'] = str(avatar)
            self.auth = "offline"
            self.prepare_recovery()

    def prepare_recovery(self):
        cfg = self.store.settings(self.account)
        disarm_all(cfg, remember=True)
        self.store.save_settings(self.account, cfg)

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self.background_done)
        return task

    def background_done(self, task):
        if task.cancelled():
            return
        error = task.exception()
        if error:
            # 不输出异常文本：平台异常可能包含手机号或消息内容。
            self.error = f"后台任务异常（{type(error).__name__}），请查看活动记录或重新连接"
            if storage_error(error):
                self.fail_closed()

    def fail_closed(self):
        self.quota_paused = True
        self.error = "本地数据无法安全写入，归档和新发送已停止"
        if self.account:
            with contextlib.suppress(Exception):
                cfg = self.settings()
                cfg["pause_sends"] = True
                self.store.save_settings(self.account, cfg)

    def quota_bytes(self, config=None):
        value = (config or self.settings())["quota_mb"]
        return value * 1024 * 1024 if value else float('inf')

    def settings(self):
        cfg = self.store.settings(self.account)
        if self.closing:
            cfg.update(pause_sends=True, pause_all=True)
        return cfg

    def state(self):
        cfg = self.settings()
        next_intervals = intervals(cfg["weekly"], cfg["timezone"])
        return {
            "protocol": PROTOCOL, "auth": self.auth, "issues": issues(self),
            "network_proxy": proxy_configuration(json.loads(self.store.preference("network_proxy") or "{}")),
            "active_proxy": self.active_proxy or {},
            "device_profile": self.device_profile, "active_profile": self.active_profile or {}, "code_resend_at": self.code_resend_at,
            "server_profile": self.server_profile,
            "countries": self.countries,
            "connected": bool(self.demo or (self.client and self.client.is_connected() and self.auth == "ready")),
            "demo": self.demo, "account": self.account, "identity": self.identity,
            "settings": cfg, "error": self.error, "last_sync": self.last_sync,
            "online_active": self.online_active, "quota_paused": self.quota_paused,
            "online_last_success": self.online_last_success,
            "quiet_active": bool(cfg["quiet_enabled"] and active(cfg["quiet_weekly"], cfg["quiet_timezone"])),
            "usage": self.store.usage(self.account), "cooldown": self.cooldown,
            "conversations": self.store.conversations(self.account),
            "activities": self.store.activities(self.account),
            "executions": self.store.executions(self.account), "ranges": self.store.ranges(self.account),
            "prompts": self.store.prompts(self.account),
            "tasks": task_state(cfg), "task_results": self.store.results(self.account),
            "syncing_chats": sorted(self.syncing_chats),
            "schedule": [{"start": a.timestamp(), "end": b.timestamp()} for a, b in next_intervals],
        }

    async def emit(self, kind, data):
        if not self.writer or self.writer.is_closing():
            return
        async with self.write_lock:
            self.writer.write((json.dumps({"protocol": PROTOCOL, "event": kind, "data": data}, ensure_ascii=False) + "\n").encode())
            await self.writer.drain()

    async def publish(self):
        await self.emit("state", self.state())

    def runtime_status(self):
        return {"version": VERSION, "pid": os.getpid(), "started": self.started, "updated": time.time(),
                "auth": self.auth, "connected": bool(self.demo or (self.client and self.client.is_connected())),
                "demo": self.demo, "quota_paused": self.quota_paused, "cooldown": self.cooldown,
                "last_sync": self.last_sync, "syncing_count": len(self.syncing_chats),
                "pending_sends": len(self.send_jobs), "error_present": bool(self.error)}

    def write_runtime_status(self):
        path = self.root / "runtime-status.json"
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            os.chmod(temporary, 0o600)
            json.dump(self.runtime_status(), handle)
        temporary.replace(path)

    async def activity(self, kind, detail):
        self.store.activity(self.account, kind, detail)
        await self.publish()

    def require_account(self, live=False):
        if not self.account or self.auth not in ("ready", "offline"):
            raise ValueError("请先登录并确认账号")
        if live and not self.demo and (not self.client or not self.client.is_connected()):
            raise ValueError("连接已断开，请恢复连接后操作")

    async def command(self, method, args):
        if method == "voice_result" and (not self.account or self.auth not in ("ready", "offline") or args.get("account") != self.account):
            return {"ok": True}  # 退出或切换后迟到的语音回调不影响其他账号。
        if method == "resend_code":
            if self.auth != "code":
                raise ValueError("当前没有待重发的验证码")
            method, args = "send_code", {"phone": self.phone}
        if method == "state":
            return self.state()
        if method.startswith("network_"):
            return await network_command(self, method, args)
        if method == "connect":
            return await self.connect(args)
        if method in ("device_profile", "apply_device_profile"):
            if self.demo or self.auth in ("connecting", "phone", "code", "password", "identity", "logging_out"):
                raise ValueError("请完成或取消当前登录后再修改设备信息")
            profile = validate_profile(args)
            if method == "apply_device_profile":
                self.require_account(live=True)
                if self.send_jobs:
                    raise ValueError("自动发送正在进行，请等待完成后再重新连接")
            self.device_profile = profile
            self.store.preference("device_profile", json.dumps(profile, ensure_ascii=False))
            if method == "apply_device_profile":
                api_id, api_hash = self.client.api_id, self.client.api_hash
                cfg = self.settings()
                disarm_all(cfg, remember=True)
                self.store.save_settings(self.account, cfg)
                self.auth = "offline"
                return await self.connect({"api_id": api_id, "api_hash": api_hash, "proxy": self.connection_proxy})
            await self.publish()
            return self.state()
        if method == "cancel_login":
            if self.demo or self.auth not in ("phone", "code", "password"):
                raise ValueError("当前没有可取消的登录操作")
            if self.client:
                await self.client.disconnect()
            self.client = None
            self.phone = ""
            self.phone_code_hash = None
            cached_account = int(self.store.preference('last_account') or 0)
            cached = self.store.db.execute('SELECT name FROM accounts WHERE id=?', (cached_account,)).fetchone()
            self.account = cached_account if cached else 0
            self.identity = {'id': self.account, 'name': cached['name']} if cached else {}
            self.auth = "offline" if cached else "disconnected"
            self.error = ""
        elif method == "send_code":
            if not self.client or self.auth not in ("phone", "code"):
                raise ValueError("请先点击登录 Telegram 建立连接")
            self.phone = args["phone"].strip()
            if not self.phone.startswith("+") or not self.phone[1:].isdigit():
                raise ValueError("手机号需包含国家区号，例如 +86…")
            if time.time() < max(self.code_resend_at, self.cooldown):
                raise ValueError("请等待重发倒计时结束")
            try:
                result = await self.client.send_code_request(self.phone)
            except PhoneNumberInvalidError:
                raise ValueError('手机号格式不正确，请检查国家区号与号码') from None
            except PhoneNumberBannedError:
                raise ValueError('Telegram 拒绝此号码登录，请在官方客户端核对账号状态') from None
            self.phone_code_hash = result.phone_code_hash
            self.code_resend_at = time.time() + max(30, getattr(result, "timeout", None) or 60)
            self.auth = "code"
        elif method == "sign_in":
            if not self.client or self.auth != "code":
                raise ValueError("请先获取验证码")
            try:
                await self.client.sign_in(self.phone, args["code"], phone_code_hash=self.phone_code_hash)
            except SessionPasswordNeededError:
                self.auth = "password"
            except PhoneCodeInvalidError:
                raise ValueError('验证码不正确，请重新输入') from None
            except PhoneCodeExpiredError:
                self.code_resend_at = 0
                await self.publish()
                raise ValueError('验证码已过期，请点击重发验证码') from None
            else:
                await self.prepare_identity()
        elif method == "password":
            if not self.client or self.auth != "password":
                raise ValueError("当前无需二次验证密码")
            try:
                await self.client.sign_in(password=args["password"])
            except PasswordHashInvalidError:
                raise ValueError('两步验证密码不正确，请重新输入') from None
            await self.prepare_identity()
        elif method == "confirm_identity":
            if self.auth != "identity":
                raise ValueError("请先完成 Telegram 登录")
            await self.activate()
        elif method == "shutdown":
            self.closing = True
            self.stop.set()
            return {"ok": True}
        elif method == "demo_seed":
            if not self.demo:
                raise ValueError("当前不是演示模式")
            await self.seed_demo()
        else:
            self.require_account()
            if method.startswith('archive_cleanup_'):
                return await archive_cleanup_command(self, method, args)
            if method == 'issues': return {'rows': issues(self)}
            if method.startswith('task_') or method == 'setup_complete':
                return await task_command(self, method, args)
            elif method == "dialogs":
                await self.refresh_dialogs()
            elif method == "settings":
                await self.update_settings(args)
            elif method == "voice_result":
                result = args.get("result")
                if result not in ("queued", "playing", "completed", "cancelled", "failed", "skipped"):
                    raise ValueError("语音结果无效")
                if args.get("account") == self.account:
                    await self.activity("voice", {"queued": "语音提醒已加入播放队列", "playing": "语音开始播报", "completed": "语音播报完成", "cancelled": "语音播报已取消", "failed": "语音播报未完成", "skipped": "较早的语音提醒已合并为摘要"}[result])
            elif method == "backup":
                try:
                    counts = await asyncio.to_thread(create_backup, self.root, self.account, args["path"], args.get("password", ""))
                except OSError:
                    raise ValueError('备份未保存，请检查保存位置、权限与空间；现有任务未改动') from None
                await self.activity("backup", "本地备份已保存，不包含登录会话和 API 凭据")
                return counts
            elif method == "backup_preview":
                return await asyncio.to_thread(inspect_backup, args["path"], self.account, password=args.get("password", ""))
            elif method == "restore_backup":
                if int(args.get("confirmed_account", 0)) != self.account:
                    raise ValueError("请确认备份属于当前账号")
                if self.send_jobs:
                    raise ValueError("发送仍在进行，请等待完成后恢复备份")
                account, generation = self.account, self.generation
                # 先解密和验证，再暂停实际恢复。错误密码、文件变化不触碰工作配置。
                with tempfile.TemporaryDirectory(prefix='tga-restore-') as folder:
                    manifest = await asyncio.to_thread(inspect_backup, args["path"], account, folder, password=args.get("password", ""))
                    if args.get('digest') != manifest['digest']:
                        raise ValueError("备份在预览后已变化，请重新预览并确认")
                    if self.account != account or self.generation != generation:
                        raise ValueError("账号连接已变化，请重新核对后恢复")
                    if self.send_jobs: raise ValueError("发送仍在进行，请等待完成后恢复备份")
                    original = self.settings()
                    self.store.save_settings(account, dict(original, pause_sends=True))
                    restored = False
                    try:
                        await self.publish()
                        counts = restore_backup(self.store, account, folder, manifest)
                        restored = True
                        self.device_profile = validate_profile(manifest.get('device_profile', {}))
                        await self.publish()
                        return counts
                    except Exception:
                        if not restored: self.store.save_settings(account, original)
                        raise
            elif method == "control":
                await self.control(args["action"])
            elif method == "rule":
                await self.rule(args)
            elif method == 'refresh_device_info':
                self.require_account(live=True)
                response = await self.client(functions.account.GetAuthorizationsRequest())
                current = next((item for item in response.authorizations if item.current), None)
                if current is None: raise ValueError('Telegram 未返回当前会话信息')
                self.server_profile = {key: getattr(current, key) for key in ('device_model', 'system_version', 'app_version', 'app_name', 'country', 'region')}
            elif method == 'send_test':
                self.require_account(live=True)
                target = task_rule(self.settings(), args.get('id', 'legacy'))['target']
                text = args.get('text', '')
                if self.demo or not target or args.get('confirmed_target') != target:
                    raise ValueError('请先确认当前绑定机器人的固定身份')
                if not isinstance(text, str) or not text.strip() or len(text) > 100 or any(ord(c) < 32 for c in text):
                    raise ValueError('测试消息需为 1–100 个字符的单行文本')
                try:
                    sent = await asyncio.wait_for(self.send_keyword(target, text), 30)
                except Exception:
                    await self.activity('attention', '手动测试发送结果待核对，不自动重试')
                    raise ValueError('测试消息发送结果待核对，请在原会话确认后再操作') from None
                await self.activity('test', '手动测试消息已发送；此操作不构成查岗登记确认')
                return {'sent_id': sent.id}
            elif method == 'reply_template':
                self.require_account()
                template = validate_template(args)
                cfg = self.settings()
                cfg['rule'].update(template, automatic=False, armed_at=0, preview_validated=False)
                forget_resume(cfg, 'legacy')
                self.store.save_settings(self.account, cfg)
            elif method == "preview":
                cfg = self.settings()
                prompt = parse_reply(args.get("text", ""), cfg['rule'], 1)
                if cfg['rule']['target']:
                    cfg['rule']['preview_validated'] = bool(prompt) and (not cfg['rule'].get('sample_cases') or evaluate_cases(cfg['rule'], cfg['rule']['sample_cases'])['passed'])
                    self.store.save_settings(self.account, cfg)
                    await self.publish()
                return {"recognized": bool(prompt), "reply_mode": cfg['rule']['mode'], **(vars(prompt) if prompt else {})}
            elif method == 'follow_system_timezone':
                zone = args['timezone']; validate([[] for _ in range(7)], zone)
                cfg = self.settings(); cfg.update(timezone=zone, quiet_timezone=zone)
                for rule in [cfg['rule'], *cfg['tasks']]:
                    rule.update(sending_timezone=zone, automatic=False, armed_at=0)
                cfg['resume_tasks'] = []
                self.store.save_settings(self.account, cfg); self.system_zone = zone
                await self.activity('settings', '已采用新的系统时区，在线、静音与发送时段已调整；自动回复恢复仅提醒')
            elif method == 'schedule_preview':
                values = intervals(args['weekly'], args['timezone'])
                return {'intervals': [{'start': first.timestamp(), 'end': last.timestamp()} for first, last in values]}
            elif method == "archive_select":
                chat = int(args["chat"])
                row = self.store.conversation(self.account, chat)
                if not row or row["kind"] == "channel":
                    raise ValueError("首版支持私聊和群组归档")
                self.store.select(self.account, chat, bool(args["selected"]), float(args.get("start", 0)))
                if args["selected"]:
                    self.spawn(self.sync_chat(chat, args.get("mode", "recent")))
            elif method == "sync":
                self.require_account(live=True)
                for row in self.store.conversations(self.account):
                    if row["selected"]:
                        self.spawn(self.sync_chat(row["chat"]))
            elif method == "search":
                allowed = {k: args[k] for k in ("query", "chat", "sender", "start", "end", "offset", "include_deleted") if k in args}
                return self.store.search(self.account, **allowed)
            elif method == "notification_context":
                chat, message_id = int(args["chat"]), int(args["id"])
                if not self.store.conversation(self.account, chat):
                    raise ValueError("这条通知的会话已不可用")
                local = self.store.db.execute("SELECT * FROM messages WHERE account=? AND chat=? AND id=?", (self.account, chat, message_id)).fetchone()
                if local:
                    return {"messages": [dict(local)], "source": "local"}
                if self.demo:
                    raise ValueError("演示数据中没有这条原消息；演示模式不会联网读取")
                self.require_account(live=True)
                message = await self.client.get_messages(self.entities.get(chat, chat), ids=message_id)
                if not message or getattr(message, "ttl_period", None):
                    raise ValueError("这条消息已不可访问，请在原客户端核对")
                sender = await message.get_sender()
                title = " ".join(filter(None, (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")))) or getattr(sender, "title", "") or "未知发送者"
                return {"messages": [{"id": message.id, "chat": chat, "date": message.date.timestamp(), "sender": title, "text": message.raw_text or "", "outgoing": message.out}], "source": "remote"}
            elif method == "retry_media":
                self.require_account(live=True)
                if not self.settings()["images"] or self.archive_blocked() or time.time() < self.cooldown:
                    raise ValueError("请先开启图片归档，并确认归档未暂停或限流")
                chat, identifier = int(args["chat"]), int(args["id"])
                row = self.store.db.execute("SELECT * FROM messages WHERE account=? AND chat=? AND id=? AND deleted=0", (self.account, chat, identifier)).fetchone()
                if not row:
                    raise ValueError("找不到这条本地归档")
                with self.store.db:
                    self.store.db.execute("UPDATE messages SET media_attempts=0 WHERE account=? AND chat=? AND id=?", (self.account, chat, identifier))
                async with self.chat_locks[chat]:
                    message = await self.client.get_messages(self.entities.get(chat, chat), ids=identifier)
                    if not message:
                        raise ValueError("平台消息已不可访问")
                    await self.archive_messages(chat, [message])
            elif method == "rule_hours":
                weekly, zone = args["weekly"], args["timezone"]
                validate(weekly, zone)
                cfg = self.settings()
                cfg["rule"].update(sending_limited=bool(args["limited"]), sending_weekly=weekly,
                                   sending_timezone=zone, automatic=False, armed_at=0)
                forget_resume(cfg, 'legacy')
                self.store.save_settings(self.account, cfg)
                await self.activity("rule", "允许发送时段已保存，规则恢复仅提醒，请重新确认后启用")
            elif method in ("import_preview", "import_legacy"):
                rows = read_legacy(args["path"], args["timezone"])
                if method == "import_preview":
                    return {"count": len(rows), "images": sum(bool(r.get('legacy_media')) for r in rows), "first": rows[:2], "start": min((r["date"] for r in rows), default=0),
                            "end": max((r["date"] for r in rows), default=0)}
                if int(args.get("confirmed_account", 0)) != self.account:
                    raise ValueError("请确认文件属于当前账号")
                chat = int(args["chat"])
                if not self.store.conversation(self.account, chat):
                    raise ValueError("请先选择对应会话")
                rows = [r for r in rows if not self.store.db.execute(
                    "SELECT 1 FROM messages WHERE account=? AND chat=? AND id=?", (self.account, chat, r["id"])).fetchone()]
                size = sum(len(r["text"].encode("utf-8")) + 128 for r in rows)
                images = [(row, legacy_image(args['path'], row['legacy_media'])) for row in rows if args.get('include_media') and row.get('legacy_media')]
                size += sum(path.stat().st_size for _, path in images if path)
                if self.store.usage(self.account) + size > self.quota_bytes():
                    raise ValueError("导入后会超过归档容量上限，请先清理数据或调整容量")
                copied = []
                try:
                    for row, origin in images:
                        if origin is None:
                            row.update(media_state='failed', media_attempts=3, media_error='LegacyMissing'); continue
                        relative = Path('media') / str(self.account) / str(chat) / ('legacy-' + str(row['id']) + origin.suffix.lower())
                        destination = self.root / relative
                        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        if not destination.exists():
                            shutil.copyfile(origin, destination); os.chmod(destination, 0o600); copied.append(destination)
                        row.update(media=str(relative), media_state='saved')
                    self.store.put_messages(self.account, chat, rows)
                except Exception:
                    for destination in copied: destination.unlink(missing_ok=True)
                    raise
                self.store.range(self.account, chat, "import", "用户确认的旧版文本；不代表历史覆盖完整",
                                 start=min((r["date"] for r in rows), default=time.time()))
                await self.activity("archive", f"旧版文本已导入 {len(rows)} 条，保留现有同编号消息")
            elif method in ('ledger_preview', 'import_ledger'):
                target = self.settings()['rule']['target']
                if not target: raise ValueError('请先绑定要导入账本的机器人，并保存为仅提醒')
                rows, digest = read_legacy_ledger(args['path'], self.account, target)
                if method == 'ledger_preview':
                    return {'count': len(rows), 'target': target, 'digest': digest,
                            'statuses': {status: sum(row['status'] == status for row in rows) for status in ('sent', 'confirmed', 'send_unknown', 'expired')}}
                if int(args.get('confirmed_account', 0)) != self.account or args.get('digest') != digest:
                    raise ValueError('请重新预览并确认旧账本归属，文件可能已变化')
                if self.send_jobs: raise ValueError('发送仍在进行，请等待后导入账本')
                with self.store.db:
                    for row in rows:
                        self.store.db.execute('INSERT OR IGNORE INTO executions(account,target,number,status,keyword,prompt,date,updated,sent_id,deadline) VALUES(?,?,?,?,?,?,?,?,?,NULL)',
                                              (self.account, target, row['number'], row['status'], row['keyword'], row['prompt'], row['date'], time.time(), row['sent_id']))
                        if row['status'] == 'confirmed':
                            self.store.db.execute("UPDATE executions SET status='confirmed' WHERE account=? AND target=? AND number=?", (self.account, target, row['number']))
                    cfg = self.settings(); cfg['rule'].update(automatic=False, armed_at=0)
                    forget_resume(cfg, 'legacy')
                    self.store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (self.account, json.dumps(cfg, ensure_ascii=False)))
                await self.activity('import', f'旧发送账本已合并 {len(rows)} 条，保留已有编号，自动回复恢复仅提醒')
            elif method == "context":
                chat, identifier = int(args["chat"]), int(args["id"])
                before = self.store.db.execute("SELECT * FROM messages WHERE account=? AND chat=? AND id<=? "
                                               "ORDER BY id DESC LIMIT 11", (self.account, chat, identifier)).fetchall()
                after = self.store.db.execute("SELECT * FROM messages WHERE account=? AND chat=? AND id>? "
                                              "ORDER BY id LIMIT 10", (self.account, chat, identifier)).fetchall()
                return {"messages": [dict(r) for r in [*reversed(before), *after]]}
            elif method == "export_preview":
                return self.export_preview(args)
            elif method == "export":
                return self.export(args)
            elif method == "prune":
                self.store.prune(self.account, self.settings()["retention_days"])
                self.quota_paused = False
            elif method == "erase_chat":
                if args.get("confirm") != "删除":
                    raise ValueError("请输入“删除”确认")
                self.store.erase_chat(self.account, int(args["chat"]))
            elif method == "erase_account":
                if args.get("confirm") != "删除全部":
                    raise ValueError("请输入“删除全部”确认")
                account = self.account
                await self.logout()
                self.store.erase_account(account)
            elif method == "diagnostics":
                return self.diagnostics()
            elif method == "logout":
                return await self.logout()
            else:
                raise ValueError("不支持的命令")
        await self.publish()
        return self.state()

    async def connect(self, args):
        if self.demo:
            return self.state()
        if self.auth == "ready":
            raise ValueError("更换账号前请先退出当前账号")
        proxy = args.get("proxy", json.loads(self.store.preference("network_proxy") or "{}"))
        connection_proxy = telethon_proxy(proxy)
        cached_account, cached_identity = self.account, self.identity
        self.clear_notices()
        if self.client:
            await self.client.disconnect()
        self.generation += 1
        self.account = 0
        self.identity = {}
        self.auth = "connecting"
        self.error = ""
        await self.publish()
        api_id = int(args["api_id"])
        api_hash = args["api_hash"].strip()
        if api_id <= 0 or len(api_hash) != 32 or any(c not in "0123456789abcdefABCDEF" for c in api_hash):
            self.auth = "disconnected"
            raise ValueError("API ID 应为正整数，API Hash 应为 32 位十六进制字符串")
        self.client = TelegramClient(str(self.root / "telegram"), api_id, api_hash,
                                     **self.device_profile, proxy=connection_proxy,
                                     request_retries=1, connection_retries=5, retry_delay=2,
                                     flood_sleep_threshold=0, timeout=20)
        self.connection_proxy = dict(proxy)
        self.active_proxy = proxy_configuration(proxy)
        self.active_profile = dict(self.device_profile)
        self.client.add_event_handler(self.new_message, events.NewMessage())
        self.client.add_event_handler(self.edited_message, events.MessageEdited())
        self.client.add_event_handler(self.deleted_message, events.MessageDeleted())
        try:
            await asyncio.wait_for(self.client.connect(), 30)
            if await self.client.is_user_authorized():
                await self.prepare_identity()
                # 每次进程启动必须在界面确认恢复的身份，避免把账户自动化套在错误登录上。
            else:
                self.auth = "phone"
                self.spawn(self.load_countries())
        except Exception:
            self.account, self.identity = cached_account, cached_identity
            self.auth = "offline" if cached_account else "disconnected"
            self.error = "无法连接 Telegram，请检查网络或代理"
            raise
        self.harden_session()
        await self.publish()
        return self.state()

    def harden_session(self):
        for path in self.root.glob("telegram.session*"):
            os.chmod(path, 0o600)

    async def prepare_identity(self):
        me = await self.client.get_me()
        self.identity = {"id": me.id, "name": " ".join(filter(None, (me.first_name, me.last_name))),
                         "username": me.username or "", "phone": "••••" + (me.phone or "")[-4:]}
        self.phone = ""
        self.phone_code_hash = None
        self.auth = "identity"
        if getattr(me, 'photo', None):
            self.spawn(self.load_avatar(me, self.generation))

    async def load_countries(self):
        client = self.client
        try:
            result = await asyncio.wait_for(client(functions.help.GetCountriesListRequest(lang_code='zh-hans', hash=0)), 10)
            countries = [{'key': country.iso2 + ':' + code.country_code, 'name': (country.name or country.default_name) + ' +' + code.country_code, 'code': code.country_code}
                         for country in result.countries if not country.hidden for code in country.country_codes]
            if countries:
                self.countries = countries
                self.store.preference('countries', json.dumps(countries, ensure_ascii=False))
                await self.publish()
        except FloodWaitError as exc:
            self.cooldown = time.time() + exc.seconds
        except Exception:
            pass  # 国家列表不可用时仍允许输入完整国际号码。

    async def load_avatar(self, me, generation):
        folder = self.root / 'avatars'; folder.mkdir(exist_ok=True, mode=0o700)
        relative = Path('avatars') / (str(me.id) + '.jpg')
        temporary = (self.root / relative).with_suffix('.part')
        try:
            await asyncio.wait_for(self.client.download_profile_photo(me, file=str(temporary), download_big=False), 10)
            if generation != self.generation or self.identity.get('id') != me.id or not temporary.is_file(): return
            if temporary.stat().st_size > 1024 * 1024: return
            os.chmod(temporary, 0o600); temporary.replace(self.root / relative)
            self.identity['avatar'] = str(relative)
            await self.publish()
        except Exception:
            pass
        finally:
            temporary.unlink(missing_ok=True)

    async def activate(self):
        self.account = self.identity["id"]
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO accounts VALUES(?,?)", (self.account, self.identity["name"]))
        self.auth = "ready"
        self.store.preference("last_account", self.account)
        cfg = self.settings()
        if not self.store.db.execute("SELECT 1 FROM settings WHERE account=?", (self.account,)).fetchone():
            cfg["timezone"] = self.system_zone
            cfg["quiet_timezone"] = self.system_zone
            cfg["rule"]["sending_timezone"] = self.system_zone
        disarm_all(cfg, remember=True)
        self.store.save_settings(self.account, cfg)
        self.automation = Automation(self.store, self.account, self.settings, self.send_keyword, self.activity, self.started)
        self.harden_session()
        await self.refresh_dialogs()
        await self.activity("connection", "账号已确认，自动回复保持仅提醒，请按需重新启用")
        for row in self.store.conversations(self.account):
            if row["selected"]:
                self.spawn(self.sync_chat(row["chat"]))

    async def refresh_dialogs(self):
        if self.demo:
            return
        self.require_account(live=True)
        async for dialog in self.client.iter_dialogs():
            kind = "private" if dialog.is_user else "group" if dialog.is_group else "channel"
            self.entities[dialog.id] = dialog.entity
            self.store.put_conversation(self.account, dialog.id, dialog.name or str(dialog.id), kind, getattr(dialog.entity, "username", None) or "")

    async def update_settings(self, args):
        config = self.settings()
        booleans = ("reminders", "private", "mentions", "voice", "preview", "images", "online", "sound", "voice_content", "quiet_enabled")
        for key in booleans:
            if key in args:
                if not isinstance(args[key], bool):
                    raise ValueError("开关值无效")
                config[key] = args[key]
        for key, lower, upper in (("retention_days", 1, 365), ("quota_mb", 100, 10240)):
            if key in args:
                config[key] = 0 if key == "quota_mb" and int(args[key]) == 0 else max(lower, min(upper, int(args[key])))
        if "excluded" in args:
            config["excluded"] = [int(x) for x in args["excluded"]]
        if "weekly" in args or "timezone" in args:
            weekly, zone = args.get("weekly", config["weekly"]), args.get("timezone", config["timezone"])
            validate(weekly, zone)
            config.update(weekly=weekly, timezone=zone)
        if "quiet_weekly" in args or "quiet_timezone" in args:
            weekly, zone = args.get("quiet_weekly", config["quiet_weekly"]), args.get("quiet_timezone", config["quiet_timezone"])
            validate(weekly, zone)
            config.update(quiet_weekly=weekly, quiet_timezone=zone)
        if "conversation_modes" in args:
            modes = args["conversation_modes"]
            if not isinstance(modes, dict) or len(modes) > 500 or any(not str(k).lstrip('-').isdigit() or v not in ('default', 'silent', 'notification', 'voice', 'both') for k, v in modes.items()):
                raise ValueError("会话提醒方式无效")
            config["conversation_modes"] = modes
        self.store.save_settings(self.account, config)
        if not config["reminders"]:
            self.clear_notices()

    def clear_notices(self):
        for task in self.notice_tasks.values():
            task.cancel()
        self.notice_tasks.clear()
        self.notice_counts.clear()
        self.notice_message_ids.clear()

    async def control(self, action):
        cfg = self.settings()
        if action == "pause_reminders":
            cfg["paused_reminders_until"] = time.time() + 1800
            self.clear_notices()
        elif action == "resume_reminders":
            cfg["paused_reminders_until"] = 0
        elif action == "pause_sends":
            cfg["pause_sends"] = True
        elif action == "resume_sends":
            cfg["pause_sends"] = False
        elif action == "pause_archive":
            cfg["archive_paused"] = True
        elif action == "resume_archive":
            cfg["archive_paused"] = False
        elif action == "pause_all":
            cfg["pause_all"] = True
            self.clear_notices()
        elif action == "resume_all":
            cfg["pause_all"] = False
        else:
            raise ValueError("未知的暂停操作")
        self.store.save_settings(self.account, cfg)
        if action in ("pause_all", "pause_archive"):
            for row in self.store.conversations(self.account):
                if row["selected"]:
                    self.store.range(self.account, row["chat"], "pause", "用户暂停期间可能缺少记录；恢复后尝试补齐")
        descriptions = {
            "pause_reminders": "消息提醒已暂停 30 分钟，归档和自动回复保持原有状态",
            "resume_reminders": "已取消提醒暂停，继续按原有设置提醒",
            "pause_sends": "新发送已暂停，已提交的请求可能仍会完成",
            "resume_sends": "已取消新发送暂停，继续按原有规则执行",
            "pause_archive": "聊天归档已暂停，已有记录保留",
            "resume_archive": "聊天归档已恢复，将尝试补齐可访问的记录",
            "pause_all": "全部功能已暂停，继续接收已有发送的回执",
            "resume_all": "已取消全部暂停，先前单独暂停的功能保持暂停",
        }
        await self.activity("control", descriptions[action])

    async def rule(self, args):
        self.require_account(live=True)
        target = args.get("target", "").strip()
        if not target:
            cfg = self.settings()
            cfg["rule"] = DEFAULTS["rule"].copy()
            forget_resume(cfg, 'legacy')
            self.store.save_settings(self.account, cfg)
            return
        if self.demo:
            raise ValueError("演示模式不允许开启真实自动回复")
        entity = await self.client.get_entity(int(target) if target.lstrip("-").isdigit() else target)
        user_test = bool(args.get('allow_user_test', self.settings()['rule'].get('allow_user_test', False)))
        if not isinstance(entity, types.User) or entity.id == self.account or (not entity.bot and not user_test):
            raise ValueError("请选择机器人；普通联系人需要先开启允许选项，不能绑定本人")
        cfg = self.settings()
        previous = cfg["rule"]
        enable = bool(args.get("automatic", False))
        if enable and args.get("confirmed_target") != entity.id:
            raise ValueError("请先预览并确认机器人的固定 ID，再开启自动回复")
        if enable and (previous['target'] != entity.id or not previous['preview_validated']):
            raise ValueError('请先保存目标并成功预览模板，再确认开启自动回复')
        cfg["rule"] = dict(previous, target=entity.id, title=entity.first_name or entity.username or str(entity.id),
                           automatic=enable, armed_at=time.time() if enable else 0,
                           allow_user_test=user_test and not entity.bot,
                           preview_validated=previous['preview_validated'] if previous['target'] == entity.id else False)
        forget_resume(cfg, 'legacy')
        self.entities[entity.id] = entity
        self.store.save_settings(self.account, cfg)
        await self.activity("rule", "已开启所选对象的自动回复" if enable else "规则已保存，仅提醒")

    async def send_keyword(self, target, keyword):
        if self.closing or self.quota_paused or time.time() < self.cooldown:
            raise RuntimeError("发送已暂停")
        task = asyncio.current_task()
        self.send_jobs.add(task)
        try:
            try:
                return await self.client.send_message(self.entities.get(target, target), keyword, parse_mode=None)
            except FloodWaitError as exc:
                self.cooldown = time.time() + exc.seconds
                raise
        finally:
            self.send_jobs.discard(task)

    async def new_message(self, event):
        if self.auth != "ready":
            return
        try:
            if self.automation:
                await self.automation.handle(event)
            cfg = self.settings()
            row = self.store.conversation(self.account, event.chat_id)
            if row is None and hasattr(event, "get_chat"):
                chat_entity = await event.get_chat()
                name = getattr(chat_entity, "title", "") or " ".join(filter(None, (getattr(chat_entity, "first_name", ""), getattr(chat_entity, "last_name", ""))))
                kind = "private" if event.is_private else "channel" if isinstance(chat_entity, types.Channel) and not chat_entity.megagroup else "group"
                self.store.put_conversation(self.account, event.chat_id, name or str(event.chat_id), kind, getattr(chat_entity, "username", None) or "")
                row = self.store.conversation(self.account, event.chat_id)
            if row and row["selected"] and not cfg["pause_all"] and not cfg["archive_paused"] and not self.quota_paused:
                async with self.chat_locks[event.chat_id]:
                    await self.archive_messages(event.chat_id, [event.message])
            if event.message.out or event.chat_id in cfg["excluded"]:
                return
            key = (self.account, event.chat_id, event.message.id)
            if key in self.reminder_seen:
                return
            self.reminder_seen[key] = None
            if len(self.reminder_seen) > 10000:
                self.reminder_seen.popitem(last=False)
            if not cfg["reminders"] or cfg["pause_all"] or cfg["paused_reminders_until"] > time.time():
                return
            mode = cfg["conversation_modes"].get(str(event.chat_id), "default")
            if mode == "silent" or (cfg["quiet_enabled"] and active(cfg["quiet_weekly"], cfg["quiet_timezone"])):
                return
            if not (event.is_private or event.message.mentioned):
                return
            if mode == "default" and not ((event.is_private and cfg["private"]) or (event.message.mentioned and cfg["mentions"])):
                return
            self.notice_counts[event.chat_id] += 1
            self.notice_message_ids[event.chat_id] = event.message.id
            if event.chat_id not in self.notice_tasks:
                title = row["title"] if row else str(event.chat_id)
                self.notice_tasks[event.chat_id] = self.spawn(self.delayed_notice(event.chat_id, title, event.message.raw_text or ""))
            self.last_sync = time.time()
        except FloodWaitError as exc:
            self.cooldown = time.time() + exc.seconds
            await self.activity("attention", f"Telegram 要求等待 {exc.seconds} 秒；新发送已进入冷却")
        except Exception as exc:
            if storage_error(exc):
                self.fail_closed()
            else:
                self.error = f"消息处理暂未完成（{type(exc).__name__}）"
            await self.publish()

    async def delayed_notice(self, chat, title, text):
        await asyncio.sleep(10)
        count = self.notice_counts.pop(chat, 0)
        self.notice_tasks.pop(chat, None)
        cfg = self.settings()
        message_id = self.notice_message_ids.pop(chat, 0)
        mode = cfg["conversation_modes"].get(str(chat), "default")
        if cfg["pause_all"] or not cfg["reminders"] or cfg["paused_reminders_until"] > time.time() or chat in cfg["excluded"] or mode == "silent" or (cfg["quiet_enabled"] and active(cfg["quiet_weekly"], cfg["quiet_timezone"])):
            return
        notify = mode != "voice"
        voice = cfg["voice"] if mode == "default" else mode in ("voice", "both")
        await self.emit("reminder", {"account": self.account, "chat": chat, "message_id": message_id, "title": title, "count": count, "created": time.time(),
                                     "body": text[:160] if cfg["preview"] else "收到新的消息", "notify": notify, "sound": cfg["sound"], "voice": voice,
                                     "voice_text": title + f"收到{count}条新消息" + ("。" + text[:500] if cfg["voice_content"] else "")})
        await self.activity("reminder", f"已请求{'系统' if notify else '语音'}提醒：{count} 条消息")

    async def edited_message(self, event):
        if self.auth != "ready":
            return
        row = self.store.conversation(self.account, event.chat_id)
        if row and row["selected"] and not self.archive_blocked():
            async with self.chat_locks[event.chat_id]:
                await self.archive_messages(event.chat_id, [event.message], edited=True)
            await self.publish()

    async def deleted_message(self, event):
        if self.auth == "ready":
            if event.chat_id is None:
                ambiguous = False
                for identifier in event.deleted_ids:
                    # 私聊 ID 在账号内唯一；频道/超级群删除有 chat_id，不能套用其局部 ID。
                    rows = self.store.db.execute("SELECT m.chat FROM messages m JOIN conversations c ON m.account=c.account AND m.chat=c.chat WHERE m.account=? AND m.id=? AND c.kind='private' AND m.chat>0", (self.account, identifier)).fetchall()
                    if len(rows) == 1:
                        self.store.delete_messages(self.account, [identifier], rows[0]['chat'])
                    elif len(rows) > 1:
                        ambiguous = True
                await self.activity('attention' if ambiguous else 'archive', '删除消息在旧导入记录中存在歧义，未修改，请核对原会话' if ambiguous else '已按保存的唯一私聊消息 ID 处理删除；未归档内容不作推测')
                return
            self.store.delete_messages(self.account, event.deleted_ids, event.chat_id)
            await self.activity("archive", "已处理平台可定位的删除事件；未收到的事件无法保证同步")

    def archive_blocked(self):
        cfg = self.settings()
        return cfg["pause_all"] or cfg["archive_paused"] or self.quota_paused

    async def sync_chat(self, chat, mode="recent"):
        if self.demo or self.archive_blocked():
            return
        generation = self.generation
        async with self.chat_locks[chat]:
            row = self.store.conversation(self.account, chat)
            if not row or not row["selected"]:
                return
            account = self.account
            entity = self.entities.get(chat, chat)
            self.syncing_chats.add(chat)
            try:
                if not row["initialized"] and row["initial_start"]:
                    start = datetime.fromtimestamp(row["initial_start"], timezone.utc)
                    batch = []
                    kwargs = {"min_id": row["cursor"], "reverse": True}
                    if not row["cursor"]:
                        kwargs["offset_date"] = start
                    async for message in self.client.iter_messages(entity, **kwargs):
                        selected = self.store.conversation(account, chat)
                        if generation != self.generation or self.archive_blocked() or not selected or not selected["selected"]:
                            return
                        batch.append(message)
                        if len(batch) == 100:
                            await self.archive_messages(chat, batch, cursor=batch[-1].id)
                            batch.clear()
                    if batch:
                        await self.archive_messages(chat, batch, cursor=batch[-1].id)
                    if self.quota_paused:
                        return
                    self.store.put_messages(account, chat, [], initialized=True)
                    self.store.range(account, chat, "initial", "从指定日期开始补齐", start=row["initial_start"])
                elif not row["initialized"]:
                    recent = await self.client.get_messages(entity, limit=300 if mode != "live" else 1)
                    records = list(reversed(recent)) if mode != "live" else []
                    if generation != self.generation or self.archive_blocked():
                        return
                    await self.archive_messages(chat, records, cursor=max((m.id for m in recent), default=0), initialized=True)
                    self.store.range(account, chat, "initial", "从最近 300 条开始" if mode != "live" else "仅从启用时刻开始",
                                     start=min((m.date.timestamp() for m in records), default=time.time()))
                else:
                    batch = []
                    async for message in self.client.iter_messages(entity, min_id=row["cursor"], reverse=True):
                        latest = self.store.conversation(account, chat)
                        if generation != self.generation or self.archive_blocked() or not latest or not latest["selected"]:
                            return
                        batch.append(message)
                        if len(batch) == 100:
                            await self.archive_messages(chat, batch, cursor=batch[-1].id)
                            batch.clear()
                            await self.emit("progress", {"chat": chat, "detail": "历史消息分批补齐中"})
                    if batch:
                        await self.archive_messages(chat, batch, cursor=batch[-1].id)
                if not self.quota_paused:
                    with self.store.db:
                        self.store.db.execute("UPDATE ranges SET kind='retried',detail='已恢复补齐；平台已删除或不可访问的内容可能仍缺失',end=? "
                                              "WHERE account=? AND chat=? AND kind IN ('gap','pause')", (time.time(), account, chat))
                self.last_sync = time.time()
                await self.publish()
            except FloodWaitError as exc:
                self.cooldown = time.time() + exc.seconds
                await self.activity("attention", f"归档请求限流，等待 {exc.seconds} 秒后继续")
            except sqlite3.Error:
                self.fail_closed()
                await self.publish()
            except Exception as exc:
                if storage_error(exc):
                    self.fail_closed()
                self.store.range(account, chat, "gap", f"补齐未完成（{type(exc).__name__}），游标保留以便重试")
                await self.activity("attention", "某个会话尚未补齐，将在连接恢复后继续")
            finally:
                self.syncing_chats.discard(chat)

    async def archive_messages(self, chat, messages, cursor=None, initialized=None, edited=False):
        records = []
        account = self.account
        cfg = self.settings()
        if shutil.disk_usage(self.root).free < 5 * 1024 * 1024:
            self.fail_closed()
            raise OSError(errno.ENOSPC, '磁盘空间不足')
        estimated = self.store.usage(account)
        for message in messages:
            if getattr(message, "ttl_period", None) or getattr(getattr(message, "media", None), "ttl_seconds", None):
                # 不把阅后即焚内容变成永久记录；游标仍可前进。
                continue
            if self.store.archive_excluded(account, chat, message.id, message.date.timestamp()): continue
            old = self.store.db.execute("SELECT * FROM messages WHERE account=? AND chat=? AND id=?", (account, chat, message.id)).fetchone()
            if old and old["deleted"]:
                continue
            sender = await message.get_sender()
            title = " ".join(filter(None, (getattr(sender, "first_name", ""), getattr(sender, "last_name", ""))))
            title = title or getattr(sender, "title", "") or str(message.sender_id or "")
            file = message.file
            attachment = file.name or file.mime_type or "附件" if file else ""
            record = {"id": message.id, "date": message.date.timestamp(), "sender": title,
                      "text": message.raw_text or "", "outgoing": message.out,
                      "attachment": attachment, "edited": edited or bool(getattr(message, "edit_date", None))}
            reply = getattr(message, "reply_to", None)
            media = getattr(message, "photo", None) or getattr(message, "document", None)
            record.update(reply_to=getattr(reply, "reply_to_msg_id", 0) or 0,
                          forwarded=bool(getattr(message, "fwd_from", None)),
                          edited_at=getattr(message, "edit_date", None).timestamp() if getattr(message, "edit_date", None) else 0,
                          media_uid=str(getattr(media, "id", "")),
                          media_attempts=old["media_attempts"] if old and old["media_uid"] == str(getattr(media, "id", "")) else 0)
            if record["edited"] and old and old["media_uid"] != record["media_uid"]:
                if old["media"]:
                    self.store.remove_media(old["media"])
            estimated += len(record["text"].encode("utf-8")) + len(title.encode("utf-8")) + 128
            if estimated > self.quota_bytes(cfg):
                self.quota_paused = True
                break
            image = message.photo or (file and file.mime_type and file.mime_type.startswith("image/") and not message.sticker)
            record["media_state"] = "not_requested" if image else "none"
            if old and old["media"] and old["media_uid"] == record["media_uid"] and (self.root / old["media"]).is_file():
                record.update(media=old["media"], media_state="saved")
                records.append(record)
                continue
            if cfg["images"] and image:
                record["media_state"] = "unknown_size" if not file or file.size is None else "too_large" if file.size > 10 * 1024 * 1024 else "failed"
            if cfg["images"] and image and file and file.size is not None and file.size <= 10 * 1024 * 1024 and not self.store.archive_excluded(account, chat, message.id, message.date.timestamp(), media=True):
                if record["media_attempts"] >= 3:
                    record.update(media_retry_at=0, media_error=old["media_error"] if old else "")
                    records.append(record)
                    continue
                if self.store.usage(account) + file.size > self.quota_bytes(cfg):
                    self.quota_paused = True
                    break
                relative = Path("media") / str(account) / str(chat) / (str(message.id) + (file.ext or ".jpg"))
                destination = self.root / relative
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temporary = destination.with_suffix(destination.suffix + ".part")
                record["media_attempts"] += 1
                try:
                    if shutil.disk_usage(self.root).free < file.size + 5 * 1024 * 1024:
                        raise OSError(errno.ENOSPC, "磁盘空间不足")
                    async with self.download_slots:
                        await asyncio.wait_for(self.client.download_media(message, file=str(temporary)), 30)
                    os.chmod(temporary, 0o600)
                    temporary.replace(destination)
                    record["media"] = str(relative)
                    record["media_state"] = "saved"
                except Exception as exc:
                    temporary.unlink(missing_ok=True)
                    if isinstance(exc, FloodWaitError):
                        self.cooldown = time.time() + exc.seconds
                    if storage_error(exc):
                        self.fail_closed()
                    record.update(media_state="failed", media_error=type(exc).__name__,
                                  media_retry_at=max(self.cooldown, time.time() + 30 * 2 ** (record["media_attempts"] - 1)))
                    self.store.activity(account, "attention", "图片下载未完成，文字记录仍保留")
            records.append(record)
        # 若配额在本批耗尽，不推进游标；重新补齐时依靠主键去重。
        self.store.put_messages(account, chat, records, cursor=None if self.quota_paused else cursor,
                                initialized=None if self.quota_paused else initialized)
        self.store.harden()

    def export_filters(self, args):
        return {key: args[key] for key in ("query", "chat", "sender", "start", "end", "include_deleted") if key in args}

    async def retry_pending_media(self):
        account, generation = self.account, self.generation
        rows = self.store.db.execute("SELECT m.chat,m.id FROM messages m JOIN conversations c ON m.account=c.account AND m.chat=c.chat WHERE m.account=? AND c.selected=1 AND m.deleted=0 AND m.media_state='failed' AND m.media_attempts<3 AND m.media_retry_at<=? LIMIT 5", (account, time.time())).fetchall()
        for row in rows:
            if account != self.account or generation != self.generation or self.archive_blocked() or not self.settings()["images"] or time.time() < self.cooldown:
                break
            if self.chat_locks[row["chat"]].locked():
                continue
            async with self.chat_locks[row["chat"]]:
                try:
                    message = await self.client.get_messages(self.entities.get(row["chat"], row["chat"]), ids=row["id"])
                    if message:
                        await self.archive_messages(row["chat"], [message])
                    else:
                        with self.store.db:
                            self.store.db.execute("UPDATE messages SET media_attempts=3,media_retry_at=0,media_error='Unavailable' WHERE account=? AND chat=? AND id=?", (account, row["chat"], row["id"]))
                except FloodWaitError as exc:
                    self.cooldown = time.time() + exc.seconds
                    break
                except Exception:
                    with self.store.db:
                        self.store.db.execute("UPDATE messages SET media_attempts=media_attempts+1,media_retry_at=? WHERE account=? AND chat=? AND id=?", (time.time() + 120, account, row["chat"], row["id"]))

    def export_preview(self, args):
        result = self.store.search(self.account, **self.export_filters(args), limit=0)
        rows = self.store.search(self.account, **self.export_filters(args), limit=1000000)["messages"]
        media_bytes = sum((self.root / r["media"]).stat().st_size for r in rows if r["media"] and (self.root / r["media"]).is_file()) if args.get("include_media") else 0
        return {"count": result["count"], "format": args.get("format", "json"), "includes_credentials": False,
                "estimated_bytes": sum(len(r["text"].encode("utf-8")) + 256 for r in rows) + media_bytes,
                "start": min((r["date"] for r in rows), default=0), "end": max((r["date"] for r in rows), default=0)}

    def export(self, args):
        # 路径由原生保存面板传入，不覆盖会话数据库或凭据目录。
        path = Path(args["path"]).expanduser().resolve()
        if path.is_relative_to(self.root.resolve()):
            raise ValueError("请将导出文件保存到应用数据目录之外")
        fmt = args.get("format", "json")
        if fmt not in ("json", "txt"):
            raise ValueError("仅支持 JSON 和 UTF-8 文本")
        rows = self.store.search(self.account, **self.export_filters(args), limit=1000000)["messages"]
        public = [{k: r[k] for k in ("chat", "title", "id", "date", "sender", "text", "outgoing", "attachment", "edited", "reply_to", "forwarded", "edited_at", "deleted", "media_state")} for r in rows]
        if args.get("include_media"):
            folder = path.parent / (path.stem + "-图片")
            if folder.exists():
                raise ValueError("同名图片目录已存在，请选择新的导出文件名")
            folder.mkdir(mode=0o700)
            for source, row in zip(rows, public):
                if source["media"]:
                    original = (self.root / source["media"]).resolve()
                    if original.is_relative_to((self.root / "media").resolve()) and original.is_file():
                        name = f"{row['chat']}-{row['id']}{original.suffix}"
                        shutil.copyfile(original, folder / name)
                        os.chmod(folder / name, 0o600)
                        row["image"] = folder.name + "/" + name
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            if fmt == "json":
                json.dump({"version": 1, "messages": public}, handle, ensure_ascii=False, indent=2)
            else:
                for row in reversed(public):
                    date = datetime.fromtimestamp(row["date"]).strftime("%Y-%m-%d %H:%M:%S")
                    image = "\n图片：" + row["image"] if row.get("image") else ""
                    handle.write(f"[{date}] {row['title']} / {row['sender']}\n{row['text']}{image}\n\n")
        temporary.replace(path)
        return {"count": len(public), "path": str(path)}

    def diagnostics(self):
        return {"version": VERSION, "protocol": PROTOCOL, "schema": 5, "demo": self.demo,
                "auth": self.auth, "connection": bool(self.client and self.client.is_connected()),
                "archive_count": sum(c["count"] for c in self.store.conversations(self.account)),
                "usage_bytes": self.store.usage(self.account), "quota_paused": self.quota_paused,
                "execution_statuses": {status: sum(e["status"] == status for e in self.store.executions(self.account))
                                       for status in ("sent", "sent_unverified", "confirmed", "send_unknown")},
                "note": "不包含身份、凭据、消息正文或关键词"}

    async def logout(self):
        if self.account:
            cfg = self.settings(); disarm_all(cfg)
            self.store.save_settings(self.account, cfg)
        self.closing = True
        self.auth = "logging_out"
        self.generation += 1
        self.clear_notices()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        revoked = self.demo
        if self.client:
            try:
                revoked = bool(await asyncio.wait_for(self.client.log_out(), 10))
            except Exception:
                revoked = False
            finally:
                await self.client.disconnect()
                self.client = None
        for path in self.root.glob("telegram.session*"):
            path.unlink(missing_ok=True)
        self.account, self.identity, self.automation = 0, {}, None
        self.entities.clear()
        self.reminder_seen.clear()
        self.auth = "disconnected"
        self.closing = False
        self.store.preference("last_account", 0)
        self.online_active = False
        self.error = "" if revoked else "本地会话已删除，远端撤销未确认；请到 Telegram 的设备管理中核对"
        await self.publish()
        return {"revoked": revoked, "state": self.state()}

    async def maintenance(self):
        last_prune = 0
        while not self.stop.is_set():
            try:
                if self.auth == "ready":
                    cfg = self.settings()
                    usage = self.store.usage(self.account)
                    if usage >= self.quota_bytes(cfg) * 0.8 and not self.quota_warned:
                        self.quota_warned = True
                        await self.activity("attention", "归档容量已达到 80%，请考虑清理数据")
                    elif usage < self.quota_bytes(cfg) * 0.8:
                        self.quota_warned = False
                    if usage >= self.quota_bytes(cfg):
                        if not self.quota_paused:
                            self.quota_paused = True
                            await self.activity("attention", "归档达到容量上限，已暂停；清理记录后可继续")
                    if time.time() - last_prune > 3600:
                        self.store.prune(self.account, cfg["retention_days"])
                        last_prune = time.time()
                    connected = self.demo or bool(self.client and self.client.is_connected())
                    if not connected and self.client and time.time() >= self.reconnect_at:
                        try:
                            await asyncio.wait_for(self.client.connect(), 10)
                            connected = self.client.is_connected()
                            if connected and not await self.client.is_user_authorized():
                                self.auth = "offline"
                                disarm_all(cfg, remember=True)
                                self.store.save_settings(self.account, cfg)
                                self.error = "Telegram 会话已失效，请重新登录；本地归档仍可浏览"
                                connected = False
                            else:
                                self.reconnect_delay = 2
                        except Exception:
                            self.reconnect_at = time.time() + self.reconnect_delay
                            self.reconnect_delay = min(60, self.reconnect_delay * 2)
                    if connected != self.was_connected:
                        self.was_connected = connected
                        if not connected:
                            for row in self.store.conversations(self.account):
                                if row['selected']:
                                    self.store.range(self.account, row['chat'], 'gap', '连接中断或设备睡眠；恢复后尝试补齐')
                        await self.activity("demo" if self.demo else "connection", "本地演示已就绪" if self.demo else "连接已恢复" if connected else "连接中断，等待自动重连")
                    if connected and not self.demo:
                        cfg = self.settings()
                        if cfg["images"] and not self.archive_blocked() and (self.media_retry_task is None or self.media_retry_task.done()):
                            self.media_retry_task = self.spawn(self.retry_pending_media())
                        if time.time() - self.last_reconcile >= 60 and time.time() >= self.cooldown:
                            self.last_reconcile = time.time()
                            for row in self.store.conversations(self.account):
                                if row["selected"] and not self.chat_locks[row["chat"]].locked():
                                    self.spawn(self.sync_chat(row["chat"]))
                        desired = cfg["online"] and not cfg["pause_all"] and not cfg["pause_sends"] and active(cfg["weekly"], cfg["timezone"])
                        if time.time() >= self.online_error_until and (desired or self.online_active):
                            try:
                                await asyncio.wait_for(self.client(functions.account.UpdateStatusRequest(offline=not desired)), 10)
                                self.online_active = desired
                                self.online_last_success = time.time()
                            except FloodWaitError as exc:
                                self.online_error_until = time.time() + exc.seconds
                            except Exception:
                                self.online_error_until = time.time() + 60
                                self.error = "在线状态请求未确认，将稍后重试"
                self.write_runtime_status()
                await self.publish()
            except (OSError, sqlite3.Error):
                self.fail_closed()
                await self.emit("error", {"message": self.error})
            except Exception as exc:
                self.error = f"状态更新异常（{type(exc).__name__}）"
            await asyncio.sleep(20)

    async def seed_demo(self, account=999001, name="本地演示账号"):
        self.account = account
        self.identity = {"id": self.account, "name": name, "username": "demo", "phone": "无真实账号"}
        self.auth = "ready"
        for chat, title, kind in ((1001, "项目讨论（演示）", "group"), (1002, "同事消息（演示）", "private"), (1003, "查岗机器人（演示）", "private")):
            self.store.put_conversation(self.account, chat, title, kind)
            self.store.select(self.account, chat, True)
            self.store.put_messages(self.account, chat, [
                {"id": i, "date": time.time() - (4 - i) * 600, "sender": "演示成员", "text": text}
                for i, text in enumerate(("这是一条本地示例消息，不会连接 Telegram。", "请确认今天的项目进度。", "文字记录可以搜索和导出。"), 1)
            ], cursor=3, initialized=True)
        self.last_sync = time.time()
        self.store.activity(self.account, "demo", "演示数据仅在独立目录保存，不连接网络、不发送消息")

    async def handle_socket(self, reader, writer):
        if self.writer and not self.writer.is_closing():
            writer.close()
            return
        self.writer = writer
        await self.publish()
        try:
            while not self.stop.is_set():
                line = await reader.readline()
                if not line:
                    self.stop.set()  # 原生应用退出或崩溃时不遗留后台进程。
                    break
                request = json.loads(line)
                identifier = request.get("id")
                if request.get("protocol") != PROTOCOL:
                    response = {"id": identifier, "error": "协议版本不兼容"}
                else:
                    try:
                        result = await asyncio.wait_for(self.command(request["method"], request.get("args", {})), 90)
                        response = {"id": identifier, "result": result}
                    except FloodWaitError as exc:
                        self.cooldown = time.time() + exc.seconds
                        response = {"id": identifier, "error": f"Telegram 要求等待 {exc.seconds} 秒，请稍后再试"}
                    except (ValueError, KeyError) as exc:
                        response = {"id": identifier, "error": str(exc) if isinstance(exc, ValueError) else "请求参数缺失"}
                    except Exception as exc:
                        if storage_error(exc):
                            self.fail_closed()
                        response = {"id": identifier, "error": f"操作未完成（{type(exc).__name__}），请检查网络或输入后重试"}
                async with self.write_lock:
                    writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
                    await writer.drain()
        except (ConnectionError, asyncio.LimitOverrunError, ValueError):
            self.stop.set()
        finally:
            writer.close()

    async def run(self, socket):
        if self.demo:
            await self.seed_demo()
        server = await asyncio.start_unix_server(self.handle_socket, path=socket, limit=2 * 1024 * 1024)
        os.chmod(socket, 0o600)
        maintenance = asyncio.create_task(self.maintenance())
        await self.stop.wait()
        self.closing = True
        server.close()
        await server.wait_closed()
        maintenance.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance
        await self.close()
        Path(socket).unlink(missing_ok=True)

    async def close(self):
        self.closing = True
        self.clear_notices()
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if self.send_jobs:
            _, pending = await asyncio.wait(self.send_jobs, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if self.client:
            await self.client.disconnect()
        self.store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (root / "engine.lock").open("w")
    os.chmod(root / "engine.lock", 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    socket = Path(args.socket)
    socket.unlink(missing_ok=True)
    from engine.accounts import AccountManager
    service = AccountManager(root, args.demo, args.timezone)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, service.stop.set)
    try:
        loop.run_until_complete(service.run(str(socket)))
    finally:
        loop.close()
        lock.close()


if __name__ == "__main__":
    main()
