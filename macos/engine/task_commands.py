"""任务的原子保存、预览、启停和明确恢复。不会发送测试消息。"""
import json
import time
import unicodedata
import uuid

from telethon import types

from engine.reply_rule import parse_reply, template_from_sample, validate_template
from engine.schedule import validate
from engine.store import DEFAULTS
from engine.tasks import forget_resume, rules, task_rule, revision


def task_state(config):
    values = rules(config)
    return [dict(rule, revision=revision(rule), resume_pending=rule['id'] in config.get('resume_tasks', []),
                 conflicts=[other['name'] for other in values if other['id'] != rule['id'] and other['target'] == rule['target']])
            for rule in sorted(values, key=lambda rule: -rule.get('priority', 0))]


async def resolve_target(service, target, allow_user):
    if service.demo:
        row = service.store.conversation(service.account, int(target)) if target.isdigit() else None
        if not row or row['kind'] != 'private':
            raise ValueError('演示模式只能选择本地示例对象 ID 1002 或 1003，不连接真实账号')
        return row['chat'], row['title'], False
    service.require_account(live=True)
    entity = await service.client.get_entity(int(target) if target.lstrip('-').isdigit() else target)
    if not isinstance(entity, types.User) or entity.id == service.account or (not entity.bot and not allow_user):
        raise ValueError('请选择机器人；普通联系人需显式允许，不能选择本人')
    service.entities[entity.id] = entity
    service.store.put_conversation(service.account, entity.id, entity.first_name or str(entity.id), 'private', entity.username or '')
    return entity.id, entity.first_name or entity.username or str(entity.id), allow_user and not entity.bot


async def command(service, method, args):
    cfg = service.settings()
    identifier = args.get('id', 'legacy')
    if method == 'task_draft_preview':
        draft = validate_template(args.get('draft', {}))
        text = args.get('text', '')
        validate_template(dict(draft, sample_text=text))
        prompt = parse_reply(text, draft, 1)
        return {'recognized': bool(prompt), 'reply_mode': draft['mode'], **(vars(prompt) if prompt else {})}
    if method == 'task_results':
        status = args.get('status', '')
        if status not in ('', 'attention', 'sending', 'sent', 'sent_unverified', 'confirmed', 'send_unknown', 'expired', 'skipped', 'reminder'):
            raise ValueError('结果筛选无效')
        offset = args.get('offset', 0)
        if type(offset) is not int or not 0 <= offset <= 1000000:
            raise ValueError('结果页码无效')
        return service.store.results(service.account, args.get('task_id', ''), status, offset)
    if method == 'task_template_from_sample':
        text = args.get('text', '')
        validate_template({'sample_text': text})
        return {'prompt_template': template_from_sample(text)}
    if method == 'task_save':
        existing = identifier != 'new'
        old = task_rule(cfg, identifier) if existing else dict(DEFAULTS['rule'])
        if args.get('base_revision') and args['base_revision'] != revision(old):
            raise ValueError('任务已被修改，请重新打开后编辑')
        if not existing and len(rules(cfg)) >= 50:
            raise ValueError('每个账号最多保存 50 个任务')
        name = args.get('name', old.get('name', '回复任务'))
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(unicodedata.category(c).startswith('C') for c in name):
            raise ValueError('任务名称需为 1–60 个字符的单行文本')
        template = validate_template(dict(old, **{key: args[key] for key in ('mode', 'match_text', 'match_mode', 'reply_text', 'valid_minutes', 'prompt_template', 'sample_text') if key in args}))
        weekly, zone = args.get('sending_weekly', old['sending_weekly']), args.get('sending_timezone', old['sending_timezone'])
        validate(weekly, zone)
        priority = args.get('priority', old.get('priority', 0))
        if type(priority) is not int or not 0 <= priority <= 100:
            raise ValueError('优先级需为 0–100，数字越大越优先')
        target = str(args.get('target', old['target'])).strip()
        allow_user = args.get('allow_user_test', old['allow_user_test'])
        limited = args.get('sending_limited', old['sending_limited'])
        if type(allow_user) is not bool or type(limited) is not bool:
            raise ValueError('任务开关格式无效')
        if not target or target == '0':
            raise ValueError('请选择明确的机器人或联系人')
        if target == str(old['target']) and allow_user == old['allow_user_test']:
            target_id, title, allow_user = old['target'], old['title'], old['allow_user_test']
        else:
            target_id, title, allow_user = await resolve_target(service, target, allow_user)
        updated = dict(old, **template, name=name.strip(), target=target_id, title=title, allow_user_test=allow_user,
                       sending_weekly=weekly, sending_timezone=zone, sending_limited=limited, priority=priority)
        changed = revision(old) != revision(updated)
        if changed or not existing:
            updated.update(automatic=False, armed_at=0, preview_validated=False)
            forget_resume(cfg, identifier)
        if not existing:
            identifier = str(uuid.uuid4())
            cfg['tasks'].append(dict(updated, id=identifier))
        else:
            task_rule(cfg, identifier).update(updated)
        service.store.save_settings(service.account, cfg)
        await service.activity('task', '任务已保存；回复条件变更时仅暂停该任务，其他任务保持原状')
        return dict(service.state(), saved_task_id=identifier)
    if method == 'task_resume':
        service.require_account(live=True)
        selected = args.get('ids')
        if service.demo or args.get('confirmed_account') != service.account or not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
            raise ValueError('请确认账号并选择需要恢复的任务')
        for key in selected:
            rule = task_rule(cfg, key)
            if key not in cfg['resume_tasks'] or not rule['preview_validated'] or args.get('revisions', {}).get(key) != revision(rule):
                raise ValueError('待恢复任务已变化，请重新核对并预览')
            target, _, _ = await resolve_target(service, str(rule['target']), rule['allow_user_test'])
            if target != rule['target']:
                raise ValueError('目标身份已变化，请重新绑定任务')
        for key in selected:
            task_rule(cfg, key).update(automatic=True, armed_at=time.time())
            forget_resume(cfg, key)
    elif method == 'setup_complete':
        cfg['setup_completed'] = True
    else:
        rule = task_rule(cfg, identifier)
        if method == 'task_preview':
            text = args.get('text', '')
            validate_template(dict(rule, sample_text=text))
            prompt = parse_reply(text, rule, 1)
            rule.update(sample_text=text, preview_validated=bool(prompt))
            service.store.save_settings(service.account, cfg)
            await service.publish()
            return {'recognized': bool(prompt), 'reply_mode': rule['mode'], **(vars(prompt) if prompt else {})}
        if method == 'task_delete':
            if args.get('confirmed_id') != identifier:
                raise ValueError('请确认要删除的任务')
            if identifier == 'legacy': cfg['rule'] = json.loads(json.dumps(DEFAULTS['rule']))
            else: cfg['tasks'] = [value for value in cfg['tasks'] if value['id'] != identifier]
            forget_resume(cfg, identifier)
        elif method == 'task_toggle':
            enable = args.get('automatic')
            if type(enable) is not bool:
                raise ValueError('启停状态无效')
            if enable:
                service.require_account(live=True)
                if service.demo or args.get('confirmed_target') != rule['target'] or args.get('revision') != revision(rule) or not rule['preview_validated']:
                    raise ValueError('请成功预览并确认此任务的目标与回复方式')
                target, _, _ = await resolve_target(service, str(rule['target']), rule['allow_user_test'])
                if target != rule['target']:
                    raise ValueError('目标身份已变化，请重新绑定任务')
            rule.update(automatic=enable, armed_at=time.time() if enable else 0)
            forget_resume(cfg, identifier)
        else:
            raise ValueError('不支持的任务操作')
    service.store.save_settings(service.account, cfg)
    await service.publish()
    return service.state()
