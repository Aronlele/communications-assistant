"""仅解释用户主动预览的样例，不改变严格识别或发送条件。"""
from engine.reply_rule import normalized, parse_reply


def explain(text, rule):
    prompt = parse_reply(text, rule, 1)
    if prompt:
        return {'match_reason': 'matched', 'explanation': '样例符合匹配条件；这只是预览，是否发送仍取决于对象、时间、暂停及去重状态。'}
    actual = normalized(text)
    if not actual:
        return {'match_reason': 'empty', 'explanation': '请粘贴完整提示，当前样例为空。'}
    if rule.get('mode') == 'fixed':
        expected = normalized(rule.get('match_text', ''))
        if rule.get('match_mode') == 'contains':
            return {'match_reason': 'missing_text', 'explanation': '样例中没有完整包含指定文本，请核对文字、空行和符号。包含匹配不会忽略中间空白。'}
        index = next((i for i, pair in enumerate(zip(expected, actual)) if pair[0] != pair[1]), min(len(expected), len(actual)))
        line = actual[:index].count('\n') + 1
        column = len(actual[:index].rsplit('\n', 1)[-1]) + 1
        def visible(value):
            return {'\n': '换行', ' ': '空格', '\t': '制表符', '': '文本结束'}.get(value, repr(value))
        return {'match_reason': 'exact_difference', 'line': line, 'column': column,
                'explanation': f'第{line}行第{column}列不同：模板为{visible(expected[index:index+1])}，样例为{visible(actual[index:index+1])}。全文匹配仅统一首尾空白及换行格式，其余须一致。'}
    if rule.get('prompt_template'):
        return {'match_reason': 'extraction_structure', 'explanation': '自定义提取结构未匹配。请核对模板中的固定文字和空行；编号须为1–18位数字，时限为1–1440分钟，关键词须独占一行且不超过100字符。称呼变化需使用{name}。'}
    return {'match_reason': 'builtin_structure', 'explanation': '这条消息不符合内置编号查岗格式。请粘贴完整提示；固定内容提示可改用“匹配提示，固定回复”，其他编号结构可在高级选项中使用自定义提取模板。'}
