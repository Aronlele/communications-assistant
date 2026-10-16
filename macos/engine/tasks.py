"""旧单规则原位保留为 legacy 任务，新增任务与它共享账号的发送账本。"""
import copy
import hashlib
import json

from engine.store import DEFAULTS


def revision(rule):
    value = {key: value for key, value in {**DEFAULTS['rule'], 'priority': 0, **rule}.items()
             if key not in ('automatic', 'armed_at', 'preview_validated', 'sample_text', 'sample_cases', 'name', 'id')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rules(config):
    first = dict(config['rule'], id='legacy', name=config['rule'].get('name') or '原有回复任务')
    return sorted(([first] if first['target'] else []) + copy.deepcopy(config.get('tasks', [])),
                  key=lambda rule: -rule.get('priority', 0))


def task_rule(config, identifier):
    if identifier == 'legacy':
        return config['rule']
    value = next((rule for rule in config.get('tasks', []) if rule['id'] == identifier), None)
    if value is None:
        raise ValueError('任务不存在，请刷新任务列表')
    return value


def forget_resume(config, identifier):
    config['resume_tasks'] = [key for key in config.get('resume_tasks', []) if key != identifier]


def disarm_all(config, remember=False):
    pending = list(config.get('resume_tasks', [])) if remember else []
    for identifier, rule in [('legacy', config['rule']), *((rule['id'], rule) for rule in config.get('tasks', []))]:
        if remember and rule.get('target') and rule.get('automatic') and identifier not in pending:
            pending.append(identifier)
        rule.update(automatic=False, armed_at=0)
    config['resume_tasks'] = pending
