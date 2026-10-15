"""查岗关键词与用户配置的固定回复；不执行脚本或正则。"""
import re
import unicodedata

from auto_checkin import CheckinPrompt, parse_checkin_prompt, _REQUEST, _FOOTER


def normalized(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').strip()


def compile_prompt_template(text):
    """只有受限占位符，用户不能提供正则或脚本。"""
    fields = re.findall(r'\{([^{}]*)\}', text)
    if any(fields.count(key) != 1 for key in ('number', 'minutes', 'keyword')) or any(key not in ('number', 'minutes', 'keyword', 'name') for key in fields) or fields.count('name') > 1:
        raise ValueError('提取模板需各含一个 {number}、{minutes}、{keyword}；可选一个 {name}')
    if '{' in re.sub(r'\{[^{}]*\}', '', text) or '}' in re.sub(r'\{[^{}]*\}', '', text):
        raise ValueError('模板中的花括号请只用于占位符')
    if not re.search(r'(?m)^[ \t]*\{keyword\}[ \t]*$', text):
        raise ValueError('{keyword} 必须单独占一行，以免提取多余内容')
    parts = {'number': r'(?P<number>\d{1,18})', 'minutes': r'(?P<minutes>\d{1,4})',
             'keyword': r'(?P<keyword>[^\r\n]{1,100})', 'name': r'[^\r\n]{1,100}'}
    return re.compile(''.join(parts[part[1:-1]] if part.startswith('{') else re.escape(part)
                              for part in re.split(r'(\{[^{}]*\})', normalized(text))))


def template_from_sample(text):
    text = normalized(text)
    prompt = parse_checkin_prompt(text)
    if not prompt:
        raise ValueError('此样例无法按内置查岗格式识别，请手动填写带占位符的提取模板')
    start, end = _REQUEST.search(text).end(), _FOOTER.search(text).start()
    text = text[:start] + re.sub(r'(?m)^([ \t]*)' + re.escape(prompt.keyword) + r'([ \t]*)$', r'\g<1>{keyword}\g<2>', text[start:end], count=1) + text[end:]
    text = re.sub(r'(编号\s*)' + re.escape(prompt.number), r'\g<1>{number}', text, count=1)
    text = re.sub(r'(请在[ \t]*)\d{1,4}([ \t]*分钟内)', r'\g<1>{minutes}\g<2>', text, count=1)
    compile_prompt_template(text)
    return text


def validate_template(value):
    mode = value.get('mode', 'keyword')
    if mode not in ('keyword', 'fixed'):
        raise ValueError('回复方式无效')
    match = value.get('match_text', '')
    reply = value.get('reply_text', '1')
    match_mode = value.get('match_mode', 'exact')
    minutes = value.get('valid_minutes', 5)
    prompt_template = value.get('prompt_template', '')
    sample = value.get('sample_text', '')
    for text in (prompt_template, sample):
        if not isinstance(text, str) or len(text) > 4000 or any(unicodedata.category(c).startswith('C') and c not in '\r\n\t' for c in text):
            raise ValueError('模板和样例限 4000 字符，不得包含隐藏控制字符')
    if prompt_template:
        compile_prompt_template(prompt_template)
    if not isinstance(match, str) or len(match) > 2000 or any(unicodedata.category(c).startswith('C') and c not in '\r\n\t' for c in match):
        raise ValueError('提示文本限 2000 字符，不得包含隐藏控制字符')
    if not isinstance(reply, str) or not reply.strip() or len(reply) > 100 or any(unicodedata.category(c).startswith('C') for c in reply):
        raise ValueError('回复内容需为 1–100 字符的单行文本')
    if match_mode not in ('exact', 'contains'):
        raise ValueError('匹配方式无效')
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValueError('本地回复有效期需为 1–1440 分钟')
    match = normalized(match)
    if mode == 'fixed' and not match:
        raise ValueError('请填写需要匹配的提示文本')
    return {'mode': mode, 'match_text': match, 'match_mode': match_mode,
            'reply_text': reply, 'valid_minutes': minutes,
            'prompt_template': normalized(prompt_template), 'sample_text': sample}


def parse_reply(text, rule, message_id):
    if rule.get('mode', 'keyword') == 'keyword':
        if rule.get('prompt_template'):
            try:
                result = compile_prompt_template(rule['prompt_template']).fullmatch(normalized(text))
                if not result or not 1 <= int(result['minutes']) <= 1440:
                    return None
                keyword = result['keyword'].strip()
                if not keyword or any(unicodedata.category(c).startswith('C') for c in keyword):
                    return None
                return CheckinPrompt(result['number'], keyword, int(result['minutes']))
            except (ValueError, TypeError):
                return None
        return parse_checkin_prompt(text)
    try:
        template = validate_template(rule)
    except ValueError:
        return None
    text, match = normalized(text), template['match_text']
    matched = text == match if template['match_mode'] == 'exact' else match in text
    if not matched or type(message_id) is not int or message_id <= 0:
        return None
    return CheckinPrompt('message:' + str(message_id), template['reply_text'], template['valid_minutes'])
