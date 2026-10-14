import re
import json
import hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from engine.schedule import validate


HEADER = re.compile(r"^\[#(\d+) (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] ([^:]*):(.*)$")


def read_legacy(path, zone):
    """旧脚本文件不含账号和会话 ID，归属必须由用户明确选择。"""
    path = Path(path)
    if path.suffix.lower() != ".txt" or path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("请选择 50 MB 以内的旧版聊天文本文件")
    rows = []
    validate([[] for _ in range(7)], zone)
    timezone = ZoneInfo(zone)
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HEADER.match(line)
        if match:
            identifier, date, sender, text = match.groups()
            text = text.strip()
            reply = re.match(r'^\(回复 #(\d+)\)\s*', text)
            if reply: text = text[reply.end():]
            media = re.search(r'〔图片〕\s*→\s*(media/[^\r\n]+)\s*$', text)
            rows.append({"id": int(identifier), "date": datetime.strptime(date, "%Y-%m-%d %H:%M").replace(tzinfo=timezone).timestamp(),
                         "sender": sender.strip(), "text": text, "reply_to": int(reply.group(1)) if reply else 0,
                         "legacy_media": media.group(1).strip() if media else '', "media_state": 'not_requested' if media else 'none'})
        elif line.startswith("    ") and rows:
            rows[-1]["text"] += "\n" + line[4:]
        elif line.strip():
            raise ValueError("文件不符合旧项目聊天记录格式，未导入")
    for row in rows:
        media = re.search(r'〔图片〕\s*→\s*(media/[^\r\n]+)\s*$', row['text'])
        if media: row.update(legacy_media=media.group(1).strip(), media_state='not_requested')
    return rows


def legacy_image(text_path, value):
    base = Path(text_path).resolve().parent
    path = (base / value).resolve()
    if not path.is_relative_to((base / 'media').resolve()) or path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
        raise ValueError('旧图片路径或格式不安全')
    if not path.is_file(): return None
    if path.stat().st_size > 10 * 1024 * 1024: raise ValueError('旧图片超过 10 MB，未导入')
    with path.open('rb') as stream: header = stream.read(16)
    if not (header.startswith(b'\xff\xd8\xff') or header.startswith(b'\x89PNG\r\n\x1a\n') or header.startswith((b'GIF87a', b'GIF89a', b'BM')) or (header.startswith(b'RIFF') and header[8:12] == b'WEBP')):
        raise ValueError('旧图片内容不是支持的图片格式')
    return path


def read_legacy_ledger(path, account, target):
    path = Path(path)
    if path.suffix.lower() != '.json' or path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('请选择 10 MB 以内的旧发送账本 JSON')
    name = re.fullmatch(r'auto_checkin_(\d+)_(\d+)\.json', path.name)
    if name and (int(name[1]) != account or int(name[2]) != target):
        raise ValueError('旧账本文件名中的账号或目标不匹配')
    raw = path.read_bytes(); data = json.loads(raw)
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('bot_id') != target or not isinstance(data.get('records'), dict):
        raise ValueError('旧账本格式或机器人固定 ID 不匹配')
    rows = []
    for number, record in data['records'].items():
        if not re.fullmatch(r'\d{1,18}', number) or not isinstance(record, dict) or record.get('status') not in ('sending', 'sent', 'confirmed', 'send_unknown', 'expired'):
            raise ValueError('旧账本含无效编号或状态')
        keyword = record.get('keyword', '')
        if not isinstance(keyword, str) or len(keyword) > 100:
            raise ValueError('旧账本关键词格式无效')
        date = 0
        if record.get('requested_at'):
            parsed = datetime.fromisoformat(record['requested_at'])
            if parsed.tzinfo is None: raise ValueError('旧账本时间缺少时区')
            date = parsed.timestamp()
        sent_id = record.get('sent_message_id')
        if sent_id is not None and (type(sent_id) is not int or not 0 < sent_id < 2 ** 63):
            raise ValueError('旧账本发送消息 ID 无效')
        rows.append({'number': number, 'status': 'send_unknown' if record['status'] == 'sending' else record['status'],
                     'keyword': keyword, 'prompt': int(record.get('prompt_message_id') or 0), 'date': date, 'sent_id': sent_id})
    return rows, hashlib.sha256(raw).hexdigest()
