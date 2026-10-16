"""可移植的任务模板和合成样例；不包含账号、对象或发送授权。"""
import unicodedata
from engine.reply_rule import parse_reply, validate_template
from engine.schedule import validate
from engine.store import DEFAULTS
from engine.match_explanation import explain

FIELDS = ('name', 'mode', 'match_text', 'match_mode', 'reply_text', 'valid_minutes', 'prompt_template',
          'sample_text', 'sample_cases', 'priority', 'sending_limited', 'sending_weekly', 'sending_timezone')


def validate_cases(cases):
    if not isinstance(cases, list) or len(cases) > 20: raise ValueError('每个任务最多保存 20 条测试样例')
    result = []
    for case in cases:
        if not isinstance(case, dict) or type(case.get('should_match')) is not bool: raise ValueError('请设置样例是否应匹配')
        text, reply = case.get('text', ''), case.get('expected_reply', '')
        validate_template({'sample_text': text})
        if not text.strip(): raise ValueError('测试样例不能为空')
        if not isinstance(reply, str) or (reply and (not case['should_match'] or len(reply) > 100 or any(unicodedata.category(c).startswith("C") for c in reply))):
            raise ValueError('预期回复需为单行内容，仅用于应匹配样例')
        result.append(dict(text=text, should_match=case['should_match'], expected_reply=reply))
    if sum(len(c['text']) + len(c['expected_reply']) for c in result) > 8000:
        raise ValueError('整组样例总长度最多 8000 字符，请缩短或拆分样例')
    return result


def evaluate_cases(rule, cases):
    cases = validate_cases(cases)
    rows = []
    for index, case in enumerate(cases):
        prompt = parse_reply(case['text'], rule, 1)
        passed = bool(prompt) == case['should_match'] and (not prompt or not case['expected_reply'] or prompt.keyword == case['expected_reply'])
        rows.append({'index': index, 'recognized': bool(prompt), 'should_match': case['should_match'],
                     'reply': prompt.keyword if prompt else '', 'passed': passed,
                     'explanation': '实际回复与预期回复不同' if prompt and case['expected_reply'] and prompt.keyword != case['expected_reply'] else ('不应匹配的样例被识别' if prompt and not case['should_match'] else (explain(case['text'], rule)['explanation'] if not prompt and case['should_match'] else '符合预期'))})
    return {'rows': rows, 'passed': bool(rows) and any(c['should_match'] for c in cases) and all(r['passed'] for r in rows)}


def import_template(document):
    if not isinstance(document, dict) or document.get('format') != 'jixun-task-template' or document.get('version') != 1:
        raise ValueError('不支持此任务模板格式')
    source = document.get('rule')
    if not isinstance(source, dict): raise ValueError('任务模板内容无效')
    value = dict(DEFAULTS['rule'], **{key: source[key] for key in FIELDS if key in source})
    value.update(validate_template(value), sample_cases=validate_cases(value.get('sample_cases', [])))
    validate(value['sending_weekly'], value['sending_timezone'])
    if type(value['priority']) is not int or not 0 <= value['priority'] <= 100 or type(value['sending_limited']) is not bool:
        raise ValueError('模板优先级或发送时段选项无效')
    if not isinstance(value['name'], str) or not 1 <= len(value['name'].strip()) <= 60 or any(unicodedata.category(c).startswith('C') for c in value['name']):
        raise ValueError('模板任务名称需为 1–60 个字符的单行文本')
    return dict(value, id='new', target='', title='', automatic=False, armed_at=0, preview_validated=False, allow_user_test=False)


def builtin_templates():
    values = [dict(name='编号查岗', mode='keyword'),
              dict(name='固定内容回复', mode='fixed', match_text='📣 管理员正在进行在线抽查，请回复任意内容确认在岗。',
                   sample_text='📣 管理员正在进行在线抽查，请回复任意内容确认在岗。', reply_text='1'),
              dict(name='自定义关键词', mode='keyword', prompt_template='抽查 {number}\n请在{minutes}分钟内回复\n{keyword}',
                   sample_text='抽查 0001\n请在5分钟内回复\n在岗')]
    return [import_template({'format': 'jixun-task-template', 'version': 1, 'rule': value}) for value in values]
