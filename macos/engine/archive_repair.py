"""逐会话补全旧空服务消息；冻结范围，只读平台，不发送或重新下载媒体。"""
import asyncio
import hashlib
import json
import time
import uuid

from telethon.errors import FloodWaitError
from engine.message_content import service_content


def signature(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def load(service, chat, ids):
    return [dict(row) for identifier in ids for row in service.store.db.execute(
        'SELECT * FROM messages WHERE account=? AND chat=? AND id=?', (service.account, chat, identifier))]


EMPTY = "account=? AND chat=? AND id>0 AND text='' AND attachment='' AND media='' AND media_uid='' AND deleted=0"


def count(service, chat):
    return service.store.db.execute('SELECT count(*) FROM messages WHERE '+EMPTY, (service.account, chat)).fetchone()[0]


def validate(service, args):
    preview = service.archive_repair
    if (not preview or args.get('token') != preview['token'] or type(args.get('confirmed_account')) is not int or
        args['confirmed_account'] != service.account or preview['account'] != service.account or
        preview['generation'] != service.generation or time.time()-preview['created'] > 300):
        raise ValueError('补全预览已过期或账号变化，请重新预览')
    rows = load(service, preview['chat'], preview['ids'])
    if signature(rows) != preview['digest']: raise ValueError('原记录已变化，请重新预览，尚未补全')
    return preview, rows


async def command(service, method, args):
    if method == 'archive_repair_preview':
        chat = args.get('chat')
        row = service.store.conversation(service.account, chat) if type(chat) is int else None
        if not row or row['kind'] != 'group': raise ValueError('请先选择一个已有归档的群组，再检查旧空记录')
        after = args.get('after_id', 0)
        if type(after) is not int or not 0 <= after < 2**63: raise ValueError('补全分页位置无效，请从头预览')
        rows = [dict(row) for row in service.store.db.execute('SELECT * FROM messages WHERE '+EMPTY+' AND id>? ORDER BY id LIMIT 100', (service.account, chat, after))]
        total = count(service, chat)
        next_id = rows[-1]['id'] if rows else after
        has_more = service.store.db.execute('SELECT 1 FROM messages WHERE '+EMPTY+' AND id>? LIMIT 1', (service.account, chat, next_id)).fetchone() is not None
        preview = {'token':str(uuid.uuid4()), 'account':service.account, 'generation':service.generation,
                   'chat':chat, 'ids':[r['id'] for r in rows], 'digest':signature(rows), 'created':time.time()}
        service.archive_repair = preview
        return {key:preview[key] for key in ('token','account','chat')} | {'count':len(rows), 'total':total, 'expires_in':300, 'next_id':next_id, 'has_more':has_more}
    if method != 'archive_repair_apply': raise ValueError('不支持的补全操作')
    service.require_account(live=True)
    if service.demo: raise ValueError('演示模式只预览，不访问真实Telegram或补全记录')
    if service.archive_blocked(): raise ValueError('归档当前暂停或容量受限，尚未读取或补全')
    if service.cooldown > time.time(): raise ValueError('Telegram当前要求等待，请稍后重新预览')
    preview, _ = validate(service, args)
    chat = preview['chat']
    if chat in service.syncing_chats: raise ValueError('当前群组正在补档，请完成后重新预览')
    async with service.chat_locks[chat]:
        preview, rows = validate(service, args)
        ids = preview['ids']
        if not ids: raise ValueError('没有需要补全的旧空记录')
        try:
            remote = await asyncio.wait_for(service.client.get_messages(service.entities.get(chat, chat), ids=ids), 20)
        except FloodWaitError:
            service.archive_repair = None
            raise
        except (OSError, asyncio.TimeoutError):
            service.archive_repair = None
            raise ValueError('平台读取未完成，原记录未更改；请稍后重新预览') from None
        _, rows = validate(service, args)
        if service.archive_blocked(): raise ValueError('归档状态已变化，尚未补全')
        by_id = {m.id:m for m in (remote or []) if m is not None and getattr(m, 'chat_id', None) == chat}
        changed = []; unavailable = other = protected = 0
        for row in rows:
            value = by_id.get(row['id'])
            if value is None or getattr(value, 'date', None) is None or value.date.timestamp() != row['date']:
                unavailable += 1; continue
            if (getattr(value,'ttl_period',None) or getattr(getattr(value,'media',None),'ttl_seconds',None) or
                service.store.archive_excluded(service.account,chat,row['id'],row['date'])):
                protected += 1; continue
            content = service_content(value)
            if content is None:
                other += 1; continue
            changed.append(dict(row, **content))
        size = sum(len(r['text'].encode())+len(r['attachment'].encode()) for r in changed)
        if service.store.usage(service.account)+size > service.quota_bytes(service.settings()):
            raise ValueError('归档容量不足，原记录未更改；请先清理或调整容量')
        service.store.put_messages(service.account, chat, changed)
        service.archive_repair = None
        remaining = count(service, chat)
        await service.activity('archive',f'旧空归档补全：{len(changed)}条；平台不可用{unavailable}条，其他类型{other}条，保护跳过{protected}条')
        await service.publish()
        return {'repaired':len(changed),'unavailable':unavailable,'other_type':other,'protected':protected,'remaining':remaining}
