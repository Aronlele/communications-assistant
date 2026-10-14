"""查岗关键词与用户配置的固定回复；不执行脚本或正则。"""
import unicodedata

from auto_checkin import CheckinPrompt, parse_checkin_prompt


def normalized(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').strip()


def validate_template(value):
    mode = value.get('mode', 'keyword')
    if mode not in ('keyword', 'fixed'):
        raise ValueError('回复方式无效')
    match = value.get('match_text', '')
    reply = value.get('reply_text', '1')
    match_mode = value.get('match_mode', 'exact')
    minutes = value.get('valid_minutes', 5)
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
            'reply_text': reply, 'valid_minutes': minutes}


def parse_reply(text, rule, message_id):
    if rule.get('mode', 'keyword') == 'keyword':
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
