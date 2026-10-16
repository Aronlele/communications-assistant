import asyncio
import time
from datetime import datetime, timezone

from auto_checkin import _confirmation_number
from engine.reply_rule import parse_reply
from engine.schedule import active
from engine.tasks import rules, revision


class Automation:
    def __init__(self, store, account, settings, send, activity, started=None):
        self.store, self.account, self.settings = store, account, settings
        self.send, self.activity = send, activity
        self.started = started if started is not None else time.time()
        self.lock = asyncio.Lock()
        self.checks = {}

    def record_check(self, rule, message, status, reason):
        # 仅本进程、每条规则最后一次检查；不持久化正文或关键词。
        self.checks[rule['id']] = {'id': rule['id'], 'target': rule['target'], 'message_id': message.id,
                                   'checked': time.time(), 'status': status, 'reason': reason, 'revision': revision(rule)}
        valid = {value['id'] for value in rules(self.settings())}
        self.checks = {key: value for key, value in self.checks.items() if key in valid}

    async def handle(self, event, now=None):
        config = self.settings()
        message = event.message
        target = event.sender_id
        if not target or not event.is_private or event.chat_id != target:
            return
        bound = [rule for rule in rules(config) if rule['target'] == target]
        if message.out or message.fwd_from is not None or message.via_bot_id is not None:
            for rule in bound: self.record_check(rule, message, 'ignored', '本人发出、转发或经其他机器人发送，未进入回复处理')
            if '查岗提醒' in (message.raw_text or '') and not message.out:
                await self.activity('skipped', '查岗消息来自转发或 via-bot，未发送')
            return
        number = _confirmation_number(message.raw_text or "")
        if number:
            if self.store.confirm(self.account, target, number):
                await self.activity("confirmed", f"编号 {number} 已收到登记确认")
            return
        candidates = [(rule, parse_reply(message.raw_text or '', rule, message.id))
                      for rule in rules(config) if rule['target'] == target]
        matches = [(rule, prompt) for rule, prompt in candidates if prompt]
        for rule, prompt in candidates:
            self.record_check(rule, message, 'matched' if prompt else 'unmatched',
                              '提示已匹配，等待检查发送条件' if prompt else '与完整提示不相同或提取格式不匹配；请在任务编辑中粘贴样例复核')
        if not matches:
            if candidates and '查岗提醒' in (message.raw_text or ''):
                await self.activity('skipped', '目标机器人的查岗消息格式无法识别，未发送')
            return
        # 列表顺序是明确的优先级；仅提醒任务不遮挡已启用任务。
        rule, prompt = next(((rule, prompt) for rule, prompt in matches if rule['automatic']), matches[0])
        for other, _ in matches:
            if other['id'] != rule['id']:
                self.record_check(other, message, 'skipped', '由另一条匹配任务处理，未重复发送')
        def remember(mode, reason=''):
            self.record_check(rule, message, mode, reason or {'reminder':'任务为仅提醒，未开启自动回复', 'sending':'已提交发送处理', 'sent':'发送已提交；实际登记需按回执或原会话核对'}.get(mode, mode))
            date = message.date.timestamp() if isinstance(message.date, datetime) and message.date.tzinfo is not None else 0
            self.store.remember_prompt(self.account, target, prompt, message.id, date, mode, reason, rule['id'], rule['name'])
        if not rule["automatic"]:
            remember('reminder')
            await self.activity("prompt", f"编号 {prompt.number} 已识别，仅提醒")
            return
        async with self.lock:
            # 队列等待期间可能暂停或修改规则，重新读取再决定。
            config = self.settings()
            current_rule = next((value for value in rules(config) if value['id'] == rule['id']), None)
            if current_rule is None or not current_rule['automatic'] or current_rule['armed_at'] != rule['armed_at'] or revision(current_rule) != revision(rule) or config["pause_all"] or config["pause_sends"]:
                remember('skipped', '规则已变化或新发送已暂停')
                return
            current = now or datetime.now(timezone.utc)
            if rule.get("sending_limited") and not active(rule["sending_weekly"], rule["sending_timezone"], current):
                await self.activity("skipped", f"编号 {prompt.number} 未发送：不在允许发送时段内")
                remember('skipped', '不在允许发送时段')
                return
            date = message.date
            if not isinstance(date, datetime) or date.tzinfo is None or current.tzinfo is None:
                self.record_check(rule, message, "skipped", "消息时间不可验证，未发送")
                return
            timestamp = date.timestamp()
            cutoff = max(self.started, rule.get("armed_at", 0))
            age = (current - date).total_seconds()
            remaining = prompt.window_minutes * 60 - age
            if timestamp < cutoff or age < -60 or remaining <= 0:
                remember('skipped', '早于启用时间、超过期限或消息时间异常')
                await self.activity("skipped", f"编号 {prompt.number} 未发送：早于启用时间或超过期限")
                return
            begun = time.monotonic()
            if not self.store.reserve(self.account, target, prompt, message.id, timestamp, rule['id'], rule['name']):
                remember('skipped', '该编号已尝试过，避免重复发送')
                return
            remember('sending')
            remaining -= time.monotonic() - begun
            if remaining <= 0:
                self.store.execution_status(self.account, target, prompt.number, "expired")
                remember('skipped', '处理期间已超过期限')
                return
            try:
                sent = await asyncio.wait_for(self.send(target, prompt.keyword), min(30, remaining))
            except (Exception, asyncio.CancelledError):
                self.store.execution_status(self.account, target, prompt.number, "send_unknown")
                remember('send_unknown', '发送结果待核对，不自动重试')
                await self.activity("attention", f"编号 {prompt.number} 发送结果待核对，不会自动重试")
                return
            fixed = rule.get('mode', 'keyword') == 'fixed'
            self.store.execution_status(self.account, target, prompt.number, "sent_unverified" if fixed else "sent", getattr(sent, "id", None))
            remember('sent')
            await self.activity("sent", f"消息 {message.id} 已发送固定回复，登记未核实" if fixed else f"编号 {prompt.number} 已发送，等待登记确认")
