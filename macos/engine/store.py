import json
import os
import sqlite3
import time
from pathlib import Path


DEFAULTS = {
    "reminders": True, "private": True, "mentions": True, "voice": False,
    "sound": True, "voice_content": False, "conversation_modes": {},
    "quiet_enabled": False, "quiet_weekly": [[] for _ in range(7)], "quiet_timezone": "Asia/Shanghai",
    "preview": False, "excluded": [], "paused_reminders_until": 0,
    "pause_all": False, "pause_sends": False, "archive_paused": False,
    "retention_days": 30, "quota_mb": 1024, "images": False,
    "online": False, "timezone": "Asia/Shanghai", "weekly": [[] for _ in range(7)],
    "tasks": [], "resume_tasks": [], "setup_completed": False,
    "rule": {"target": 0, "title": "", "automatic": False, "armed_at": 0,
             "name": "原有回复任务", "priority": 0, "prompt_template": "", "sample_text": "",
             "preview_validated": False, "allow_user_test": False,
             "mode": "keyword", "match_text": "", "match_mode": "exact", "reply_text": "1", "valid_minutes": 5,
             "sending_limited": False, "sending_weekly": [[] for _ in range(7)], "sending_timezone": "Asia/Shanghai"},
}


class Store:
    """仅由引擎事件循环写入。所有业务查询都强制指定账号。"""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        path = self.root / "assistant.sqlite"
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            self.db.close()
            raise RuntimeError("数据版本较新，请使用更新版本的应用")
        if version in (1, 2, 3):
            backup_path = self.root / "assistant.before-v4.sqlite"
            backup = sqlite3.connect(backup_path)
            try:
                self.db.backup(backup)
            finally:
                backup.close()
            os.chmod(backup_path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(account INTEGER PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS conversations(
            account INTEGER, chat INTEGER, title TEXT, kind TEXT, selected INTEGER DEFAULT 0,
            initialized INTEGER DEFAULT 0, cursor INTEGER DEFAULT 0,
            PRIMARY KEY(account,chat));
        CREATE TABLE IF NOT EXISTS messages(
            account INTEGER, chat INTEGER, id INTEGER, date REAL, sender TEXT, text TEXT,
            outgoing INTEGER, media TEXT DEFAULT '', attachment TEXT DEFAULT '',
            edited INTEGER DEFAULT 0, PRIMARY KEY(account,chat,id));
        CREATE INDEX IF NOT EXISTS message_date ON messages(account,date);
        DROP TABLE IF EXISTS message_search;
        CREATE TABLE IF NOT EXISTS executions(
            account INTEGER, target INTEGER, number TEXT, status TEXT, keyword TEXT,
            prompt INTEGER, date REAL, updated REAL, sent_id INTEGER,
            PRIMARY KEY(account,target,number));
        CREATE TABLE IF NOT EXISTS activities(
            id INTEGER PRIMARY KEY AUTOINCREMENT, account INTEGER, date REAL,
            kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS ranges(
            id INTEGER PRIMARY KEY AUTOINCREMENT, account INTEGER, chat INTEGER,
            start REAL, end REAL, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS prompts(
            account INTEGER, target INTEGER, message_id INTEGER, number TEXT, keyword TEXT,
            date REAL, deadline REAL, mode TEXT, reason TEXT,
            PRIMARY KEY(account,target,message_id));
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(conversations)")}
        self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            if "initial_start" not in columns:
                self.db.execute("ALTER TABLE conversations ADD COLUMN initial_start REAL DEFAULT 0")
            execution_columns = {row[1] for row in self.db.execute("PRAGMA table_info(executions)")}
            if "deadline" not in execution_columns:
                self.db.execute("ALTER TABLE executions ADD COLUMN deadline REAL")
            for table in ('executions', 'prompts'):
                existing = {row[1] for row in self.db.execute(f'PRAGMA table_info({table})')}
                for field, default in (('task_id', 'legacy'), ('task_name', '原有回复任务')):
                    if field not in existing:
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {field} TEXT DEFAULT '{default}'")
            self.db.execute('CREATE INDEX IF NOT EXISTS execution_source ON executions(account,target,prompt)')
            message_columns = {row[1] for row in self.db.execute("PRAGMA table_info(messages)")}
            for name, definition in {"reply_to": "INTEGER DEFAULT 0", "forwarded": "INTEGER DEFAULT 0", "edited_at": "REAL DEFAULT 0", "deleted": "INTEGER DEFAULT 0", "media_state": "TEXT DEFAULT ''", "media_attempts": "INTEGER DEFAULT 0", "media_retry_at": "REAL DEFAULT 0", "media_error": "TEXT DEFAULT ''", "media_uid": "TEXT DEFAULT ''"}.items():
                if name not in message_columns:
                    self.db.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")
            conversation_columns = {row[1] for row in self.db.execute("PRAGMA table_info(conversations)")}
            if "username" not in conversation_columns:
                self.db.execute("ALTER TABLE conversations ADD COLUMN username TEXT DEFAULT ''")
            self.db.execute("PRAGMA user_version=4")
        # 发送中的进程已退出：无法证明平台没有收到，必须保留去重账本。
        with self.db:
            self.db.execute("UPDATE executions SET status='send_unknown' WHERE status='sending'")
        self.harden()

    def harden(self):
        for path in self.root.glob("assistant.sqlite*"):
            os.chmod(path, 0o600)

    def settings(self, account):
        row = self.db.execute("SELECT value FROM settings WHERE account=?", (account,)).fetchone()
        result = json.loads(json.dumps(DEFAULTS))
        if row:
            result.update(json.loads(row[0]))
            result["rule"] = dict(DEFAULTS["rule"], **result["rule"])
        return result

    def save_settings(self, account, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",
                            (account, json.dumps(value, ensure_ascii=False)))

    def preference(self, key, value=None):
        if value is not None:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (key, str(value)))
        row = self.db.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

    def activity(self, account, kind, detail):
        with self.db:
            self.db.execute("INSERT INTO activities(account,date,kind,detail) VALUES(?,?,?,?)",
                            (account, time.time(), kind, detail))

    def activities(self, account, limit=200):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM activities WHERE account=? ORDER BY id DESC LIMIT ?", (account, limit))]

    def conversations(self, account):
        return [dict(r) for r in self.db.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.account=c.account AND m.chat=c.chat) AS count "
            "FROM conversations c WHERE account=? ORDER BY selected DESC,title", (account,))]

    def conversation(self, account, chat):
        return self.db.execute("SELECT * FROM conversations WHERE account=? AND chat=?", (account, chat)).fetchone()

    def put_conversation(self, account, chat, title, kind, username=""):
        with self.db:
            self.db.execute("INSERT INTO conversations(account,chat,title,kind,username) VALUES(?,?,?,?,?) "
                            "ON CONFLICT(account,chat) DO UPDATE SET title=excluded.title,kind=excluded.kind,username=excluded.username",
                            (account, chat, title, kind, username))

    def select(self, account, chat, selected, initial_start=0):
        with self.db:
            self.db.execute("UPDATE conversations SET selected=? WHERE account=? AND chat=?",
                            (int(selected), account, chat))
            if initial_start:
                self.db.execute("UPDATE conversations SET initial_start=? WHERE account=? AND chat=? AND initialized=0",
                                (initial_start, account, chat))

    def put_messages(self, account, chat, records, cursor=None, initialized=None):
        # 内容和历史游标同时提交；实时消息不会越过尚未补齐的历史游标。
        with self.db:
            for record in records:
                self.db.execute("INSERT INTO messages(account,chat,id,date,sender,text,outgoing,media,attachment,edited,reply_to,forwarded,edited_at,deleted,media_state,media_attempts,media_retry_at,media_error,media_uid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                                "ON CONFLICT(account,chat,id) DO UPDATE SET "
                                "date=excluded.date,sender=excluded.sender,text=CASE WHEN messages.deleted=1 THEN '' ELSE excluded.text END,"
                                "outgoing=excluded.outgoing,attachment=CASE WHEN messages.deleted=1 THEN '' ELSE excluded.attachment END,"
                                "media=CASE WHEN messages.deleted=1 THEN '' WHEN excluded.edited=1 THEN excluded.media WHEN excluded.media='' THEN messages.media ELSE excluded.media END,"
                                "edited=MAX(messages.edited,excluded.edited),reply_to=excluded.reply_to,forwarded=excluded.forwarded,edited_at=MAX(messages.edited_at,excluded.edited_at),"
                                "deleted=MAX(messages.deleted,excluded.deleted),media_state=CASE WHEN messages.deleted=1 THEN '' ELSE excluded.media_state END,"
                                "media_attempts=excluded.media_attempts,media_retry_at=excluded.media_retry_at,media_error=excluded.media_error,media_uid=excluded.media_uid",
                                (account, chat, record["id"], record["date"], record["sender"],
                                 record["text"], int(record.get("outgoing", False)), record.get("media", ""),
                                 record.get("attachment", ""), int(record.get("edited", False)), record.get("reply_to", 0), int(record.get("forwarded", False)), record.get("edited_at", 0), int(record.get("deleted", False)),
                                 record.get("media_state", ""), record.get("media_attempts", 0), record.get("media_retry_at", 0), record.get("media_error", ""), record.get("media_uid", "")))
            if cursor is not None:
                self.db.execute("UPDATE conversations SET cursor=MAX(cursor,?) WHERE account=? AND chat=?",
                                (cursor, account, chat))
            if initialized is not None:
                self.db.execute("UPDATE conversations SET initialized=? WHERE account=? AND chat=?",
                                (int(initialized), account, chat))

    def delete_messages(self, account, ids, chat=None):
        if not ids or chat is None:
            return
        placeholders = ",".join("?" for _ in ids)
        clause = f"account=? AND id IN ({placeholders})"
        args = [account, *ids]
        if chat is not None:
            clause += " AND chat=?"
            args.append(chat)
        rows = self.db.execute(f"SELECT media FROM messages WHERE {clause}", args).fetchall()
        with self.db:
            self.db.execute(f"UPDATE messages SET text='',media='',attachment='',deleted=1,media_state='',media_retry_at=0,media_error='' WHERE {clause}", args)
        for row in rows:
            self.remove_media(row[0])

    def remove_media(self, value):
        if value:
            path = (self.root / value).resolve()
            if path.is_relative_to((self.root / "media").resolve()):
                path.unlink(missing_ok=True)

    def search(self, account, query="", chat=0, sender="", start=0, end=0, limit=200, offset=0, include_deleted=False):
        clauses, args = ["m.account=?"], [account]
        if not include_deleted:
            clauses.append("m.deleted=0")
        if query:
            # 支持中文子串；转义通配符，并始终使用参数查询。
            clauses.append("m.text LIKE ? ESCAPE '\\'")
            args.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        if chat:
            clauses.append("m.chat=?")
            args.append(chat)
        if sender:
            clauses.append("m.sender LIKE ?")
            args.append("%" + sender + "%")
        if start:
            clauses.append("m.date>=?")
            args.append(start)
        if end:
            clauses.append("m.date<=?")
            args.append(end)
        where = " AND ".join(clauses)
        count = self.db.execute(f"SELECT COUNT(*) FROM messages m WHERE {where}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT m.*,c.title FROM messages m LEFT JOIN conversations c ON m.account=c.account AND m.chat=c.chat "
            f"WHERE {where} ORDER BY m.date DESC,m.id DESC LIMIT ? OFFSET ?", [*args, limit, offset])
        return {"count": count, "messages": [dict(r) for r in rows]}

    def reserve(self, account, target, prompt, message_id, date, task_id='legacy', task_name='原有回复任务'):
        with self.db:
            result = self.db.execute("INSERT OR IGNORE INTO executions(account,target,number,status,keyword,prompt,date,updated,sent_id,deadline,task_id,task_name) SELECT ?,?,?,?,?,?,?,?,NULL,?,?,? "
                                     "WHERE NOT EXISTS(SELECT 1 FROM executions WHERE account=? AND target=? AND prompt=? AND prompt>0)",
                                     (account, target, prompt.number, "sending", prompt.keyword,
                                      message_id, date, time.time(), date + prompt.window_minutes * 60, task_id, task_name,
                                      account, target, message_id))
        return result.rowcount == 1

    def execution_status(self, account, target, number, status, sent_id=None):
        with self.db:
            self.db.execute("UPDATE executions SET status=CASE WHEN status='confirmed' THEN status ELSE ? END,updated=?,sent_id=COALESCE(?,sent_id) "
                            "WHERE account=? AND target=? AND number=?",
                            (status, time.time(), sent_id, account, target, number))

    def confirm(self, account, target, number):
        with self.db:
            result = self.db.execute("UPDATE executions SET status='confirmed',updated=? "
                                    "WHERE account=? AND target=? AND number=? "
                                    "AND status IN ('sending','sent','send_unknown')",
                                    (time.time(), account, target, number))
        return result.rowcount > 0

    def executions(self, account):
        now = time.time()
        result = [dict(r) for r in self.db.execute(
            "SELECT * FROM executions WHERE account=? ORDER BY updated DESC LIMIT 200", (account,))]
        for row in result:
            row["receipt_overdue"] = row["status"] == "sent" and row["deadline"] is not None and now >= row["deadline"] + 300
        return result

    def remember_prompt(self, account, target, prompt, message_id, date, mode, reason='', task_id='legacy', task_name='原有回复任务'):
        with self.db:
            self.db.execute("INSERT INTO prompts(account,target,message_id,number,keyword,date,deadline,mode,reason,task_id,task_name) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(account,target,message_id) DO UPDATE SET mode=excluded.mode,reason=excluded.reason "
                            "WHERE prompts.mode NOT IN ('sent','send_unknown','sending') OR "
                            "(prompts.mode='sending' AND excluded.mode IN ('sent','send_unknown'))",
                            (account, target, message_id, prompt.number, prompt.keyword, date, date + prompt.window_minutes * 60 if date > 0 else None, mode, reason, task_id, task_name))

    def results(self, account, task_id='', status='', offset=0, limit=50):
        clauses, args = ['account=?'], [account]
        if task_id:
            clauses.append('task_id=?'); args.append(task_id)
        if status == 'attention':
            clauses.append("(status='send_unknown' OR (status='sent' AND deadline IS NOT NULL AND deadline+300<=?))")
            args.append(time.time())
        elif status:
            clauses.append('status=?'); args.append(status)
        # 一条来源消息一行；保留只有旧发送账本、没有 prompts 的历史记录。
        query = """WITH results AS (
            SELECT e.account,e.target,e.number,e.keyword,e.prompt AS message_id,e.date,e.updated,e.deadline,
                   e.status,e.sent_id,e.task_id,e.task_name,'' AS reason
              FROM executions e
            UNION ALL
            SELECT p.account,p.target,p.number,p.keyword,p.message_id,p.date,p.date,p.deadline,
                   CASE WHEN p.mode='reminder' THEN 'reminder' ELSE 'skipped' END,NULL,p.task_id,p.task_name,p.reason
              FROM prompts p WHERE NOT EXISTS(SELECT 1 FROM executions e
                    WHERE e.account=p.account AND e.target=p.target AND e.prompt=p.message_id)
        ) """
        where = ' AND '.join(clauses)
        count = self.db.execute(query + 'SELECT count(*) FROM results WHERE ' + where, args).fetchone()[0]
        rows = [dict(row) for row in self.db.execute(query + 'SELECT * FROM results WHERE ' + where +
                                                    ' ORDER BY updated DESC,target,message_id DESC LIMIT ? OFFSET ?',
                                                    [*args, limit, offset])]
        for row in rows:
            row['receipt_overdue'] = row['status'] == 'sent' and row['deadline'] is not None and time.time() >= row['deadline'] + 300
        return {'count': count, 'rows': rows}

    def prompts(self, account):
        return [dict(row) for row in self.db.execute('SELECT * FROM prompts WHERE account=? ORDER BY date DESC LIMIT 200', (account,))]

    def range(self, account, chat, kind, detail, start=None, end=None):
        with self.db:
            self.db.execute("INSERT INTO ranges(account,chat,start,end,kind,detail) VALUES(?,?,?,?,?,?)",
                            (account, chat, start or time.time(), end or time.time(), kind, detail))

    def ranges(self, account):
        return [dict(r) for r in self.db.execute(
            "SELECT r.*,c.title FROM ranges r LEFT JOIN conversations c ON r.account=c.account AND r.chat=c.chat "
            "WHERE r.account=? ORDER BY r.id DESC LIMIT 100", (account,))]

    def prune(self, account, days=30):
        cutoff = time.time() - days * 86400
        rows = self.db.execute("SELECT media FROM messages WHERE account=? AND date<?", (account, cutoff)).fetchall()
        with self.db:
            self.db.execute("DELETE FROM messages WHERE account=? AND date<?", (account, cutoff))
            self.db.execute("DELETE FROM activities WHERE account=? AND date<?", (account, cutoff))
            self.db.execute("DELETE FROM prompts WHERE account=? AND date<?", (account, cutoff))
            self.db.execute("DELETE FROM ranges WHERE account=? AND end<?", (account, cutoff))
            self.db.execute("UPDATE executions SET keyword='' WHERE account=? AND updated<?", (account, cutoff))
        for row in rows:
            self.remove_media(row[0])
        self.clean_backup(account, cutoff=cutoff)
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def usage(self, account):
        text = self.db.execute("SELECT COALESCE(SUM(length(CAST(text AS BLOB))+length(CAST(sender AS BLOB))+128),0) "
                               "FROM messages WHERE account=?", (account,)).fetchone()[0]
        folder = self.root / "media" / str(account)
        media = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.exists() else 0
        return int(text + media)

    def erase_chat(self, account, chat):
        media = [r[0] for r in self.db.execute("SELECT media FROM messages WHERE account=? AND chat=?", (account, chat))]
        with self.db:
            self.db.execute("DELETE FROM messages WHERE account=? AND chat=?", (account, chat))
            self.db.execute("UPDATE conversations SET selected=0,initialized=0,cursor=0 WHERE account=? AND chat=?",
                            (account, chat))
            self.db.execute("DELETE FROM ranges WHERE account=? AND chat=?", (account, chat))
        for value in media:
            self.remove_media(value)
        self.clean_backup(account, chat=chat)

    def erase_account(self, account):
        for c in self.conversations(account):
            self.erase_chat(account, c["chat"])
        with self.db:
            for table in ("settings", "conversations", "executions", "activities", "ranges", "prompts"):
                self.db.execute(f"DELETE FROM {table} WHERE account=?", (account,))
            self.db.execute("DELETE FROM accounts WHERE id=?", (account,))
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.clean_backup(account)
        self.db.execute("VACUUM")
        (self.root / 'avatars' / (str(account) + '.jpg')).unlink(missing_ok=True)

    def clean_backup(self, account, chat=None, cutoff=None):
        for path in self.root.glob("assistant.before-v*.sqlite"):
            self.clean_backup_file(path, account, chat, cutoff)

    def clean_backup_file(self, path, account, chat=None, cutoff=None):
        backup = sqlite3.connect(path)
        try:
            backup.execute("PRAGMA secure_delete=ON")
            tables = {row[0] for row in backup.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            with backup:
                if cutoff is not None:
                    for table, field in (("messages", "date"), ("activities", "date"), ("ranges", "end"), ('prompts', 'date')):
                        if table in tables:
                            backup.execute(f"DELETE FROM {table} WHERE account=? AND {field}<?", (account, cutoff))
                    if "message_search" in tables:
                        backup.execute("DELETE FROM message_search WHERE account=?", (account,))
                    if "executions" in tables:
                        backup.execute("UPDATE executions SET keyword='' WHERE account=? AND updated<?", (account, cutoff))
                elif chat is not None:
                    for table in ("messages", "message_search", "ranges"):
                        if table in tables:
                            backup.execute(f"DELETE FROM {table} WHERE account=? AND chat=?", (account, chat))
                else:
                    for table in ("messages", "message_search", "settings", "conversations", "executions", "activities", "ranges", "prompts"):
                        if table in tables:
                            backup.execute(f"DELETE FROM {table} WHERE account=?", (account,))
                    if "accounts" in tables:
                        backup.execute("DELETE FROM accounts WHERE id=?", (account,))
            backup.execute("VACUUM")
        finally:
            backup.close()

    def close(self):
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.db.close()
