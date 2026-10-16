"""本地清理预览冻结消息集合；确认不扩大范围，不触碰自动回复账本。"""
import hashlib
import json
import time
import uuid


def signature(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_rows(service, keys):
    rows = []
    for chat, identifier in keys:
        row = service.store.db.execute('SELECT * FROM messages WHERE account=? AND chat=? AND id=?',
                                       (service.account, chat, identifier)).fetchone()
        if row: rows.append(dict(row))
    return rows


async def command(service, method, args):
    if method not in ('archive_cleanup_preview', 'archive_cleanup_apply'): raise ValueError('不支持的清理操作')
    if method == 'archive_cleanup_preview':
        scope = args.get('scope')
        if scope not in ('selected', 'filter', 'conversation'):
            raise ValueError('请选择消息、当前筛选或一个会话进行清理')
        media_only = args.get('media_only', False)
        keep = args.get('keep_archiving', True)
        if type(media_only) is not bool or type(keep) is not bool:
            raise ValueError('清理选项无效')
        chat = args.get('chat', 0)
        if type(chat) is not int: raise ValueError('会话无效')
        if scope == 'conversation' and (not chat or not service.store.conversation(service.account, chat)):
            raise ValueError('请先选择一个已有会话')
        if scope == 'conversation' and media_only:
            raise ValueError('会话清空与仅清理图片请分别操作')
        if scope == 'selected':
            messages = args.get('messages')
            if not isinstance(messages, list) or not 1 <= len(messages) <= 500:
                raise ValueError('请选择 1–500 条消息')
            keys = []
            for value in messages:
                if not isinstance(value, dict) or type(value.get('chat')) is not int or type(value.get('id')) is not int or value['id'] <= 0:
                    raise ValueError('消息选择无效')
                key = (value['chat'], value['id'])
                if key in keys: raise ValueError('消息选择重复')
                keys.append(key)
            rows = load_rows(service, keys)
            if len(rows) != len(keys): raise ValueError('所选消息已变化，请刷新后重新选择')
        else:
            filters = {key: args[key] for key in ('query', 'sender', 'start', 'end') if key in args} if scope == 'filter' else {}
            rows = service.store.search(service.account, chat=chat, include_deleted=(True if scope == 'conversation' else bool(args.get('include_deleted', False))), limit=10001, **filters)['messages']
            if len(rows) > 10000: raise ValueError('单次最多清理 10000 条，请用会话或日期缩小范围')
            keys = [(r['chat'], r['id']) for r in rows]
        if media_only:
            rows = [r for r in rows if r['media']]
            keys = [(r['chat'], r['id']) for r in rows]
        # 用相同查询形成确认时指纹，忽略 search 附加的会话标题。
        rows = load_rows(service, keys)
        preview = dict(token=str(uuid.uuid4()), account=service.account, keys=keys,
                       digest=signature(rows), scope=scope, chat=chat, media_only=media_only,
                       keep_archiving=keep, created=time.time())
        service.archive_cleanup = preview
        sizes = []
        for row in rows:
            path = (service.root / row['media']).resolve() if row['media'] else None
            if path and path.is_relative_to((service.root / 'media').resolve()) and path.is_file(): sizes.append(path.stat().st_size)
        return {key: preview[key] for key in ('token', 'account', 'scope', 'chat', 'media_only', 'keep_archiving')} | {
            'count': len(rows), 'images': len(sizes), 'image_bytes': sum(sizes),
            'conversations': len({r['chat'] for r in rows}), 'expires_in': 300}
    preview = service.archive_cleanup
    if type(args.get('confirmed_account')) is not int or not preview or args.get('token') != preview['token'] or args.get('confirmed_account') != service.account or time.time()-preview['created'] > 300:
        raise ValueError('清理预览已过期或账号已变化，请重新预览')
    rows = load_rows(service, preview['keys'])
    if signature(rows) != preview['digest']:
        raise ValueError('预览后的消息内容已变化，请重新预览，尚未执行清理')
    if preview['scope'] == 'conversation':
        current = service.store.db.execute('SELECT id FROM messages WHERE account=? AND chat=? AND date<=?', (service.account,preview['chat'],int(preview['created']))).fetchall()
        if {r[0] for r in current} != {r['id'] for r in rows if r['date'] <= int(preview['created'])}:
            raise ValueError('历史补齐范围已变化，请重新预览，尚未执行清理')
        # 清空只覆盖预览前的历史；预览后收到的新消息继续保留。
        cutoff = int(preview['created'])
        service.store.clear_archive(service.account, preview['chat'], cutoff, preview['keep_archiving'], rows)
    else:
        service.store.clean_messages(service.account, rows, preview['media_only'])
    service.archive_cleanup = None
    if service.store.usage(service.account) < service.quota_bytes(service.settings()):
        service.quota_paused = False
    await service.activity('archive', '用户确认完成本地清理；发送账本及回复任务保留')
    await service.publish()
    return {'ok': True, 'count': len(rows)}
