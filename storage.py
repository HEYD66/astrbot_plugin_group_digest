"""SQLite is the source of truth for messages, batch reservations and the outbox."""

import datetime as dt
import json
import os
import sqlite3
import threading
import time
import re
from pathlib import Path

# 北京时间的小时、日期、星期（%w：0=周日）
HOUR = "CAST(strftime('%H',m.source_time,'unixepoch','+8 hours') AS INTEGER)"
DAY = "date(m.source_time,'unixepoch','+8 hours')"
WEEKDAY = "CAST(strftime('%w',m.source_time,'unixepoch','+8 hours') AS INTEGER)"
TYPE_SQL = [
    ("text", "纯文字", "m.outline=''"),
    ("image", "图片", "m.outline LIKE '%[Image]%'"),
    ("face", "表情", "m.outline LIKE '%[Face]%'"),
    ("reply", "回复", "m.outline LIKE '%[Reply]%'"),
    ("at", "@提及", "(m.outline LIKE '%[At]%' OR m.outline LIKE '%[AtAll]%')"),
    ("file", "文件", "m.outline LIKE '%[File]%'"),
    ("voice", "语音", "m.outline LIKE '%[Record]%'"),
    ("video", "视频", "m.outline LIKE '%[Video]%'"),
    ("forward", "合并转发", "(m.outline LIKE '%[Forward]%' OR m.outline LIKE '%[Node%')"),
    ("card", "卡片分享", "(m.outline LIKE '%[Json]%' OR m.outline LIKE '%[Share]%')"),
]


def longest_streak(days):
    """已排序的 ISO 日期序列中最长的连续天数。"""
    best = cur = 0
    prev = None
    for d in days:
        day = dt.date.fromisoformat(d)
        cur = cur + 1 if prev is not None and (day - prev).days == 1 else 1
        best, prev = max(best, cur), day
    return best


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, platform TEXT NOT NULL, group_id TEXT NOT NULL,
                group_name TEXT NOT NULL, message_id TEXT NOT NULL,
                sender_id TEXT NOT NULL, sender_name TEXT NOT NULL,
                source_time REAL NOT NULL, received_at REAL NOT NULL,
                text TEXT NOT NULL, outline TEXT NOT NULL,
                eligible INTEGER NOT NULL, summary_job INTEGER,
                UNIQUE(platform, group_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS messages_group ON messages(platform,group_id,id);
            CREATE INDEX IF NOT EXISTS messages_pending ON messages(eligible,summary_job,id);
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, platform TEXT NOT NULL,
                group_id TEXT NOT NULL, group_name TEXT NOT NULL,
                record_ids TEXT NOT NULL, targets TEXT NOT NULL, hits TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
                generation_state TEXT NOT NULL DEFAULT '{}', generation_calls INTEGER NOT NULL DEFAULT 0,
                generation_failures INTEGER NOT NULL DEFAULT 0, delivery_failures INTEGER NOT NULL DEFAULT 0,
                delivery_progress INTEGER NOT NULL DEFAULT 0, delivery_plan TEXT NOT NULL DEFAULT '[]',
                next_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
                instruction TEXT NOT NULL DEFAULT '', input_limit INTEGER NOT NULL DEFAULT 16000,
                created_at REAL NOT NULL, completed_at REAL
            );
            CREATE INDEX IF NOT EXISTS jobs_work ON jobs(status,next_at,id);
            CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
            PRAGMA user_version=1;
        """)
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(messages)")}
        if "source_key" not in columns:
            self.db.execute("ALTER TABLE messages ADD COLUMN source_key TEXT NOT NULL DEFAULT ''")
        job_columns = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        for column, default in [("delivery_mode", "text"), ("context_state", "{}"), ("delivery_receipts", "[]")]:
            if column not in job_columns:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
        if "attachments" not in columns:  # v1.8.0：文件名与大小（JSON），不含下载地址
            self.db.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT ''")
        self.db.executescript("""
            CREATE UNIQUE INDEX IF NOT EXISTS messages_native_key ON messages(platform,group_id,source_key) WHERE source_key!='';
            CREATE TABLE IF NOT EXISTS sync_state (
                platform TEXT NOT NULL, group_id TEXT NOT NULL,
                completed_to REAL NOT NULL, lower_bound REAL NOT NULL DEFAULT 0,
                upper_bound REAL NOT NULL DEFAULT 0, cursor TEXT NOT NULL DEFAULT '',
                inserted_count INTEGER NOT NULL DEFAULT 0, last_success REAL NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', truncated_hours INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(platform,group_id));
            CREATE TABLE IF NOT EXISTS group_directory (
                platform TEXT NOT NULL, group_id TEXT NOT NULL, group_name TEXT NOT NULL,
                joined INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL,
                PRIMARY KEY(platform,group_id));
            PRAGMA user_version=4;
            CREATE INDEX IF NOT EXISTS messages_group_time ON messages(platform,group_id,source_time);
            CREATE INDEX IF NOT EXISTS messages_time ON messages(source_time);
        """)
        self.fts = self._init_fts()
        # 洞察、榜单、搜索走独立只读连接：WAL 下可与写入并发，不阻塞消息入库
        self.reader = sqlite3.connect(str(self.path), check_same_thread=False, timeout=20)
        self.reader.row_factory = sqlite3.Row
        self.reader.execute("PRAGMA query_only=ON")
        self.read_lock = threading.Lock()
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending' WHERE status='generating'")
            self.db.execute("UPDATE jobs SET status='ready' WHERE status='sending'")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    FTS_TOKENIZER = "trigram"
    FTS_TRIGGERS = ("messages_fts_ai", "messages_fts_ad", "messages_fts_au")

    def _init_fts(self):
        """全文索引：FTS5 trigram（中文按三字切分），由触发器随 messages 同步。

        建表、触发器、可用性探测和补建索引在同一事务里完成。索引行数与消息数不一致
        （旧库首次升级、上次建到一半被中断、触发器曾缺失）时整体重建一次。
        SQLite 不支持时删掉触发器，避免它们让每次入库都失败，搜索退回 LIKE。"""
        statements = [
            "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
            f"text, content='messages', content_rowid='id', tokenize='{self.FTS_TOKENIZER}')",
            "CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN "
            "INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text); END",
            "CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN "
            "INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text); END",
            "CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF text ON messages BEGIN "
            "INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text); "
            "INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text); END",
        ]
        if self.db.in_transaction:
            self.db.commit()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            for sql in statements:
                self.db.execute(sql)
            # 表已存在但分词器不可用（SQLite 降级）时，只有真正查询才会报错
            self.db.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? LIMIT 1", ('"fts"',)).fetchall()
            indexed = self.db.execute("SELECT COUNT(*) FROM messages_fts_docsize").fetchone()[0]
            total = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if indexed != total:
                self.db.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
            self.db.commit()
            return True
        except sqlite3.DatabaseError as exc:
            self.db.rollback()
            # 数据库被锁等临时错误保留触发器，下次启动按行数比对补建，本次搜索先退回 LIKE。
            # 其他错误（FTS5/分词器不可用、索引损坏）删掉触发器，保证消息入库不受索引拖累；
            # 下次启动会重新建触发器并整体重建索引。
            if not re.search(r"locked|busy", str(exc), re.I):
                try:
                    with self.db:
                        for name in self.FTS_TRIGGERS:
                            self.db.execute(f"DROP TRIGGER IF EXISTS {name}")
                except sqlite3.OperationalError:
                    pass
            return False

    def close(self):
        with self.read_lock:
            self.reader.close()
        with self.lock:
            self.db.close()

    def ingest(self, record, settings, history=False):
        """Commit the source record and matching notification in one transaction."""
        with self.lock, self.db:
            targets = settings.targets(record["platform"])
            settings = settings.for_group(record["group_id"])  # 单群规则覆盖全局转发/总结规则
            eligible = bool(settings["summary_enabled"] and targets and
                            (not history or settings["backfill_summary_enabled"]))
            key = record.get("source_key", "")
            match = self.db.execute("SELECT id,source_key,source_time,sender_id FROM messages WHERE platform=? AND group_id=? AND message_id=?",
                                    (record["platform"], record["group_id"], record["message_id"])).fetchone()
            id_collision = bool(match and ((key and match["source_key"] and key != match["source_key"]) or
                                           match["sender_id"] != record["sender_id"]))
            if id_collision:
                match = None
            if not match and key:
                match = self.db.execute("SELECT id,source_key FROM messages WHERE platform=? AND group_id=? AND source_key=?",
                                        (record["platform"], record["group_id"], key)).fetchone()
            if not match and key:
                # Bind each legacy row once; never collapse two native sequences
                # merely because the same sender repeats text in the same second.
                def canonical(text):
                    return re.sub(r"\s+", " ", re.sub(r"@[^@\n]*?\((\d+)\)", r"@\1", text)).strip()
                for candidate in self.db.execute("SELECT id,source_key,text,outline FROM messages WHERE platform=? AND group_id=? AND sender_id=? AND source_time=? AND source_key='' ORDER BY id",
                                                 (record["platform"], record["group_id"], record["sender_id"], record["source_time"])):
                    if canonical(candidate["text"]) == canonical(record["text"]) and candidate["outline"] == record["outline"]:
                        match = candidate
                        break
            if match:
                if key and not match["source_key"]:
                    self.db.execute("UPDATE messages SET source_key=? WHERE id=?", (key, match["id"]))
                return None
            names = ["platform", "group_id", "group_name", "message_id", "sender_id", "sender_name", "source_time", "text", "outline"]
            if id_collision:
                if not key:
                    raise ValueError("Message ID collision without stable sequence")
                record = {**record, "message_id": "native:" + key}
            result = self.db.execute(
                "INSERT INTO messages (" + ",".join(names) + ",received_at,eligible,source_key,attachments) VALUES (" + ",".join("?" for _ in range(len(names) + 4)) + ") ON CONFLICT DO NOTHING",
                [record[k] for k in names] + [time.time(), int(eligible), key, record.get("attachments", "") or ""],
            )
            if not result.rowcount:
                return None
            row_id = result.lastrowid
            hits = settings.matches(record["text"])
            if not history and targets and settings["forward_enabled"] and hits:
                self._job("match", record, [row_id], targets, settings, hits)
            return row_id

    def sync_window(self, platform, group, now, max_hours):
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM sync_state WHERE platform=? AND group_id=?", (platform, group)).fetchone()
            if row and row["upper_bound"]:
                return dict(row)
            latest = self.db.execute("SELECT MAX(source_time) FROM messages WHERE platform=? AND group_id=?", (platform, group)).fetchone()[0]
            completed = row["completed_to"] if row else (latest if latest is not None else now)
            lower = max(completed - 120, now - max_hours * 3600)
            truncated = int(completed - 120 < now - max_hours * 3600)
            self.db.execute("INSERT INTO sync_state(platform,group_id,completed_to,lower_bound,upper_bound,truncated_hours) VALUES(?,?,?,?,?,?) ON CONFLICT(platform,group_id) DO UPDATE SET lower_bound=excluded.lower_bound,upper_bound=excluded.upper_bound,cursor='',truncated_hours=excluded.truncated_hours",
                            (platform, group, completed, lower, now, truncated))
            return dict(self.db.execute("SELECT * FROM sync_state WHERE platform=? AND group_id=?", (platform, group)).fetchone())

    def sync_page(self, platform, group, records, settings, cursor, done):
        # Source rows commit before the cursor. If interrupted, replay is safe
        # because native-key and message-ID deduplication runs on every insert.
        count = sum(self.ingest(r, settings, history=True) is not None for r in records)
        with self.lock, self.db:
            self.db.execute("UPDATE sync_state SET cursor=?,inserted_count=inserted_count+?,last_error='',last_success=?,completed_to=CASE WHEN ? THEN upper_bound ELSE completed_to END,upper_bound=CASE WHEN ? THEN 0 ELSE upper_bound END WHERE platform=? AND group_id=?",
                            (cursor, count, time.time(), done, done, platform, group))
        return count

    def sync_error(self, platform, group, error):
        with self.lock, self.db:
            self.db.execute("UPDATE sync_state SET last_error=? WHERE platform=? AND group_id=?", (error, platform, group))

    def reset_sync_cursors(self, platform=None, group=None):
        with self.lock, self.db:
            if platform is None:
                self.db.execute("UPDATE sync_state SET cursor='' WHERE upper_bound>0")
            else:
                self.db.execute("UPDATE sync_state SET cursor='' WHERE platform=? AND group_id=? AND upper_bound>0", (platform, group))

    def sync_status(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT platform,group_id,completed_to,upper_bound,inserted_count,last_success,last_error,truncated_hours FROM sync_state")]

    def summary_history(self, settings, through_id=None, apply=False):
        with self.lock, self.db:
            cutoff = self.db.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0] if through_id is None else through_id
            ids = [r["id"] for r in self.db.execute("SELECT id,platform,group_id FROM messages WHERE eligible=0 AND summary_job IS NULL AND id<=?", (cutoff,))
                   if settings.monitors(r["platform"], r["group_id"]) and settings.targets(r["platform"])
                   and settings.for_group(r["group_id"])["summary_enabled"]]
            if apply:
                self.db.executemany("UPDATE messages SET eligible=1 WHERE id=? AND eligible=0 AND summary_job IS NULL", [(i,) for i in ids])
            return {"count": len(ids), "through_id": cutoff}

    def _job(self, kind, group, ids, targets, settings, hits=()):
        if kind == "match" and settings["forward_format"] == "card":
            now = time.time()
            wait = settings["forward_context_wait"] if settings["forward_context_after"] else 0
            for old in self.db.execute("SELECT * FROM jobs WHERE kind='match' AND status='collecting' AND platform=? AND group_id=? AND targets=? AND next_at>? ORDER BY id", (group["platform"], group["group_id"], json.dumps(targets), now)):
                state = json.loads(old["context_state"])
                if state["sender_id"] == group["sender_id"] and len(state["matched_ids"]) < 20:
                    state["matched_ids"] = list(dict.fromkeys(state["matched_ids"] + ids))
                    combined_hits = list(dict.fromkeys(json.loads(old["hits"]) + list(hits)))
                    self.db.execute("UPDATE jobs SET context_state=?,hits=? WHERE id=?", (json.dumps(state, ensure_ascii=False), json.dumps(combined_hits, ensure_ascii=False), old["id"]))
                    return old["id"]
            state = dict(anchor_id=ids[0], anchor_time=group["source_time"], sender_id=group["sender_id"],
                         matched_ids=ids, before=settings["forward_context_before"], after=settings["forward_context_after"],
                         lower=group["source_time"] - settings["forward_context_minutes"] * 60,
                         upper=group["source_time"] + settings["forward_context_minutes"] * 60,
                         deadline=now + wait)
            return self.db.execute("INSERT INTO jobs(kind,platform,group_id,group_name,record_ids,targets,hits,status,instruction,input_limit,created_at,next_at,delivery_mode,context_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (kind,group["platform"],group["group_id"],group["group_name"],json.dumps(ids),json.dumps(targets),json.dumps(list(hits),ensure_ascii=False),"collecting",settings["summary_instruction"],settings["summary_input_chars"],now,now+wait,"card",json.dumps(state,ensure_ascii=False))).lastrowid
        return self.db.execute(
            "INSERT INTO jobs(kind,platform,group_id,group_name,record_ids,targets,hits,status,instruction,input_limit,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (kind, group["platform"], group["group_id"], group["group_name"], json.dumps(ids), json.dumps(targets), json.dumps(list(hits), ensure_ascii=False), "pending" if kind == "summary" else "ready", settings["summary_instruction"], settings["summary_input_chars"], time.time()),
        ).lastrowid

    def finish_context(self, settings, now=None):
        now = time.time() if now is None else now
        completed = 0
        with self.lock, self.db:
            for job in self.db.execute("SELECT * FROM jobs WHERE status='collecting' AND kind='match' ORDER BY id"):
                if not settings.monitors(job["platform"], job["group_id"]) or not settings.for_group(job["group_id"])["forward_enabled"] or not set(json.loads(job["targets"])).issubset(settings.targets(job["platform"])):
                    continue
                state = json.loads(job["context_state"])
                args = (job["platform"],job["group_id"],state["sender_id"])
                preceding = list(self.db.execute("SELECT id FROM messages WHERE platform=? AND group_id=? AND sender_id=? AND source_time>=? AND (source_time<? OR (source_time=? AND id<?)) ORDER BY source_time DESC,id DESC LIMIT ?", (*args,state["lower"],state["anchor_time"],state["anchor_time"],state["anchor_id"],state["before"])))
                following = list(self.db.execute("SELECT id FROM messages WHERE platform=? AND group_id=? AND sender_id=? AND received_at<=? AND (source_time>? OR (source_time=? AND id>?)) AND source_time<=? ORDER BY source_time,id LIMIT ?", (*args,state["deadline"],state["anchor_time"],state["anchor_time"],state["anchor_id"],min(state["deadline"],state["upper"]),state["after"])))
                if now < state["deadline"] and len(following) < state["after"]:
                    continue
                ids = list(dict.fromkeys([r[0] for r in reversed(preceding)] + state["matched_ids"] + [r[0] for r in following]))
                self.db.execute("UPDATE jobs SET record_ids=?,status='ready',next_at=0 WHERE id=? AND status='collecting'", (json.dumps(ids),job["id"]))
                completed += 1
        return completed

    def reserve_batches(self, settings):
        if not settings["enabled"]:
            return 0
        # 各群条数阈值可能不同，先按最小阈值取候选群，再逐群按该群规则判断
        lowest = min([settings["summary_threshold"]] + [r["summary_threshold"] for r in settings["group_rules"].values() if "summary_threshold" in r])
        created = 0
        with self.lock, self.db:
            groups = self.db.execute(
                "SELECT platform,group_id,MAX(group_name) group_name,COUNT(*) n FROM messages m WHERE eligible=1 AND summary_job IS NULL AND NOT EXISTS (SELECT 1 FROM sync_state s WHERE s.platform=m.platform AND s.group_id=m.group_id AND s.upper_bound>0) GROUP BY platform,group_id HAVING COUNT(*)>=?",
                (lowest,),
            ).fetchall()
            for group in groups:
                if not settings.monitors(group["platform"], group["group_id"]):
                    continue
                targets = settings.targets(group["platform"])
                gs = settings.for_group(group["group_id"])
                if not targets or not gs["summary_enabled"] or group["n"] < gs["summary_threshold"]:
                    continue
                # Limit reservations per pass, but let subsequent passes drain the durable backlog.
                for _ in range(min(8, group["n"] // gs["summary_threshold"])):
                    ids = [x[0] for x in self.db.execute(
                        "SELECT id FROM messages WHERE platform=? AND group_id=? AND eligible=1 AND summary_job IS NULL ORDER BY source_time,id LIMIT ?",
                        (group["platform"], group["group_id"], gs["summary_threshold"]),
                    )]
                    job_id = self._job("summary", group, ids, targets, gs)
                    self.db.executemany("UPDATE messages SET summary_job=? WHERE id=?", [(job_id, i) for i in ids])
                    created += 1
        return created

    def claim(self, phase, settings):
        source, dest = ("pending", "generating") if phase == "generate" else ("ready", "sending")
        with self.lock, self.db:
            for row in self.db.execute("SELECT * FROM jobs WHERE status=? AND next_at<=? ORDER BY id", (source, time.time())):
                if not settings.monitors(row["platform"], row["group_id"]):
                    continue
                if not settings.for_group(row["group_id"])["summary_enabled" if row["kind"] == "summary" else "forward_enabled"]:
                    continue
                # A changed recipient setting must not silently reroute old private records.
                targets = json.loads(row["targets"])
                if not targets or not set(targets).issubset(settings.targets(row["platform"])):
                    continue
                self.db.execute("UPDATE jobs SET status=? WHERE id=? AND status=?", (dest, row["id"], source))
                return dict(row)
        return None

    def update(self, job_id, **values):
        allowed = {"status", "result", "generation_state", "generation_calls", "generation_failures", "delivery_failures", "delivery_progress", "delivery_plan", "delivery_receipts", "next_at", "last_error", "completed_at"}
        if set(values) - allowed:
            raise ValueError("Invalid job update")
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", [*values.values(), job_id])

    def records(self, ids):
        with self.lock:
            rows = []
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                rows.extend(dict(r) for r in self.db.execute("SELECT * FROM messages WHERE id IN (" + ",".join("?" for _ in batch) + ")", batch))
            return sorted(rows, key=lambda r: (r["source_time"], r["id"]))

    def charge_call(self, day, maximum):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO usage VALUES(?,0)", (day,))
            return bool(self.db.execute("UPDATE usage SET calls=calls+1 WHERE day=? AND calls<?", (day, maximum)).rowcount)

    def retry(self, job_id):
        with self.lock, self.db:
            return bool(self.db.execute(
                "UPDATE jobs SET status=CASE WHEN result!='' OR kind='match' THEN 'ready' ELSE 'pending' END, next_at=0, last_error='',generation_failures=0,delivery_failures=0 WHERE id=? AND status='failed'",
                (job_id,),
            ).rowcount)

    def status(self, day):
        with self.lock:
            counts = dict(self.db.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status").fetchall())
            usage = self.db.execute("SELECT calls FROM usage WHERE day=?", (day,)).fetchone()
            kinds = dict(self.db.execute("SELECT kind,COUNT(*) FROM jobs GROUP BY kind").fetchall())
            failed = dict(self.db.execute("SELECT kind,COUNT(*) FROM jobs WHERE status='failed' GROUP BY kind").fetchall())
            joined = self.db.execute("SELECT COUNT(*) FROM group_directory WHERE joined=1").fetchone()[0]
            import datetime as dt
            tz = dt.timezone(dt.timedelta(hours=8))
            midnight = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(), tz).timestamp()
            today_count, yesterday_count = self.db.execute(
                "SELECT SUM(CASE WHEN received_at>=? THEN 1 ELSE 0 END),SUM(CASE WHEN received_at>=? AND received_at<? THEN 1 ELSE 0 END) FROM messages WHERE received_at>=?",
                (midnight, midnight - 86400, midnight, midnight - 86400)).fetchone()
            yesterday_usage = self.db.execute("SELECT calls FROM usage WHERE day=?", ((dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat(),)).fetchone()
            return {"failed_by_kind": failed, "joined_group_count": joined,
                    "messages_today": today_count or 0, "messages_yesterday": yesterday_count or 0,
                    "llm_calls_yesterday": yesterday_usage[0] if yesterday_usage else 0,
                    "message_count": self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], "group_count": self.db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM messages GROUP BY platform,group_id)").fetchone()[0], "pending_message_count": self.db.execute("SELECT COUNT(*) FROM messages WHERE eligible=1 AND summary_job IS NULL").fetchone()[0], "jobs": counts, "kind_counts": kinds, "llm_calls_today": usage[0] if usage else 0}

    def activity(self, day):
        """Seven calendar days in UTC+8, based on durable receive times."""
        import datetime as dt
        end = dt.date.fromisoformat(day)
        days = [(end - dt.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
        tz = dt.timezone(dt.timedelta(hours=8))
        start = dt.datetime.combine(end - dt.timedelta(days=6), dt.time(), tz).timestamp()
        stop = dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), tz).timestamp()
        with self.lock:
            counts = dict(self.db.execute("SELECT date(received_at,'unixepoch','+8 hours'),COUNT(*) FROM messages WHERE received_at>=? AND received_at<? GROUP BY 1", (start, stop)))
        return [{"day": d, "count": counts.get(d, 0)} for d in days]

    def groups(self):
        with self.lock:
            recorded = [dict(r) for r in self.db.execute("SELECT platform,group_id,MAX(group_name) group_name,COUNT(*) message_count,MAX(source_time) last_time,SUM(CASE WHEN eligible=1 AND summary_job IS NULL THEN 1 ELSE 0 END) pending_count,SUM(CASE WHEN eligible=0 AND summary_job IS NULL THEN 1 ELSE 0 END) excluded_count FROM messages GROUP BY platform,group_id")]
            result = {(r["platform"], r["group_id"]): {**r, "joined": None, "directory_updated_at": 0} for r in recorded}
            for r in self.db.execute("SELECT * FROM group_directory"):
                key = (r["platform"], r["group_id"])
                if not r["joined"] and key not in result:
                    continue  # Departed groups with no history need no empty row.
                item = result.setdefault(key, dict(platform=r["platform"], group_id=r["group_id"],
                                                   message_count=0, pending_count=0, excluded_count=0, last_time=None))
                item.update(group_name=r["group_name"], joined=bool(r["joined"]), directory_updated_at=r["updated_at"])
            # 会话列表预览：每群最后一条消息（走 platform,group_id,source_time 索引，每群一次）
            for (platform, group), item in result.items():
                last = self.db.execute("SELECT sender_name,text,outline FROM messages WHERE platform=? AND group_id=? "
                                       "ORDER BY source_time DESC,id DESC LIMIT 1", (platform, group)).fetchone()
                item["last_message"] = ({"sender_name": last["sender_name"], "text": (last["text"] or "")[:80],
                                         "outline": last["outline"]} if last else None)
            return sorted(result.values(), key=lambda r: (-(r["last_time"] or 0), r["group_name"], r["platform"]))

    def remember_groups(self, platform, groups):
        # Only a complete successful response is authoritative. Validation happens
        # before changing membership, so malformed/failed fetches keep the cache.
        if not isinstance(groups, list):
            raise ValueError("Invalid group directory response")
        entries = {}
        for group in groups:
            if not isinstance(group, dict) or not str(group.get("group_id") or "").strip():
                raise ValueError("Invalid group directory entry")
            gid = str(group["group_id"])
            entries[gid] = str(group.get("group_name") or gid)
        with self.lock, self.db:
            now = time.time()
            self.db.execute("UPDATE group_directory SET joined=0,updated_at=? WHERE platform=?", (now, platform))
            self.db.executemany("INSERT INTO group_directory(platform,group_id,group_name,joined,updated_at) VALUES(?,?,?,1,?) ON CONFLICT(platform,group_id) DO UPDATE SET group_name=excluded.group_name,joined=1,updated_at=excluded.updated_at",
                                [(platform, gid, name, now) for gid, name in entries.items()])

    @staticmethod
    def _like(term):
        return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    def page(self, table, page=1, group_id="", search="", status="", kind="", platform="",
             sender="", since=0, until=0):
        if table not in ("messages", "jobs"):
            raise ValueError("Invalid table")
        page = max(1, int(page))
        where, args = [], []
        if group_id:
            where.append("group_id=?"); args.append(group_id)
        if platform:
            where.append("platform=?"); args.append(platform)
        if search and table == "messages":
            self._text_filter(search, where, args)
        elif search:
            where.append("result LIKE ? ESCAPE '\\'")
            args.append(self._like(search))
        if table == "messages":
            if sender:
                where.append("(sender_name LIKE ? ESCAPE '\\' OR sender_id=?)"); args += [self._like(sender), sender]
            if since:
                where.append("source_time>=?"); args.append(float(since))
            if until:
                where.append("source_time<?"); args.append(float(until))
        if status and table == "jobs":
            where.append("status=?"); args.append(status)
        if kind and table == "jobs":
            if kind not in ("summary", "match"):
                raise ValueError("任务类型无效")
            where.append("kind=?"); args.append(kind)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM " + table + clause, args).fetchone()[0]
            order = "source_time DESC,id DESC" if table == "messages" else "id DESC"
            items = [dict(r) for r in self.db.execute("SELECT * FROM " + table + clause + " ORDER BY " + order + " LIMIT 30 OFFSET ?", [*args, (page - 1) * 30])]
            if table == "jobs":
                for item in items:
                    item["preview"] = self._job_preview(item)
        for item in items:
            if table == "jobs":
                item["hits"] = json.loads(item.get("hits") or "[]")
                item["record_count"] = len(json.loads(item.pop("record_ids")))
                item["recipient_count"] = len(json.loads(item.pop("targets")))
                item["receipt_count"] = len(json.loads(item.pop("delivery_receipts", "[]")))
                item.pop("context_state", None)
                item.pop("generation_state", None)
                item.pop("delivery_plan", None)
        return {"items": items, "total": total, "page": page, "page_size": 30}

    def _job_preview(self, job):
        """Short text for list rows: the first matched message, or the summary head."""
        if job["kind"] == "summary":
            text = re.sub(r"\s+", " ", job["result"] or "").strip()
            return {"sender_name": "", "text": text[:140]}
        ids = json.loads(job["record_ids"] or "[]")
        state = json.loads(job.get("context_state") or "{}")
        anchor = (state.get("matched_ids") or ids or [None])[0]
        if anchor is None:
            return {"sender_name": "", "text": ""}
        row = self.db.execute("SELECT sender_name,text,outline FROM messages WHERE id=?", (anchor,)).fetchone()
        if row is None:
            return {"sender_name": "", "text": ""}
        return {"sender_name": row["sender_name"], "text": (row["text"] or row["outline"] or "")[:140]}

    def job_detail(self, job_id):
        """One job with the exact messages it covers; no transport state or targets."""
        with self.lock:
            job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                return None
            job = dict(job)
            ids = json.loads(job["record_ids"] or "[]")
            matched = set(json.loads(job.get("context_state") or "{}").get("matched_ids") or
                          (ids if job["kind"] == "match" else []))
        records = self.records(ids[:500]) if job["kind"] == "match" else []
        return {"id": job["id"], "kind": job["kind"], "platform": job["platform"], "group_id": job["group_id"],
                "group_name": job["group_name"], "status": job["status"], "result": job["result"],
                "hits": json.loads(job["hits"] or "[]"), "record_count": len(ids),
                "delivery_mode": job["delivery_mode"], "delivery_progress": job["delivery_progress"],
                "generation_calls": job["generation_calls"], "last_error": job["last_error"],
                "created_at": job["created_at"], "completed_at": job["completed_at"], "next_at": job["next_at"],
                "records": [{"id": r["id"], "sender_name": r["sender_name"], "sender_id": r["sender_id"],
                             "source_time": r["source_time"], "text": r["text"], "outline": r["outline"],
                             "matched": r["id"] in matched} for r in records]}

    # —— 洞察、榜单、全文搜索：走只读连接，不占用入库写锁 ——
    def _read(self, sql, args=()):
        with self.read_lock:
            return [dict(r) for r in self.reader.execute(sql, args)]

    @staticmethod
    def _scope(platform="", group_id="", since=0, until=0):
        where, args = [], []
        if platform:
            where.append("m.platform=?"); args.append(platform)
        if group_id:
            where.append("m.group_id=?"); args.append(group_id)
        if since:
            where.append("m.source_time>=?"); args.append(float(since))
        if until:
            where.append("m.source_time<?"); args.append(float(until))
        return where, args

    def _text_filter(self, search, where, args, prefix=""):
        """多个关键词同时包含才算命中。三字及以上走 FTS5 trigram，更短的词退回 LIKE。"""
        terms = list(dict.fromkeys(t for t in search.split() if t))[:8]
        indexed = [t for t in terms if len(t) >= 3] if self.fts else []
        for term in terms:
            if term not in indexed:
                where.append(prefix + "text LIKE ? ESCAPE '\\'"); args.append(self._like(term))
        if indexed:
            where.append(prefix + "id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
            args.append(" ".join('"' + t.replace('"', '""') + '"' for t in indexed))

    def insight(self, platform="", group_id="", since=0, until=0):
        where, args = self._scope(platform, group_id, since, until)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        types = ",".join(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) t_{key}" for key, _, cond in TYPE_SQL)
        head = self._read(f"SELECT COUNT(*) total,MIN(m.source_time) first_time,MAX(m.source_time) last_time,"
                          f"COUNT(DISTINCT m.sender_id) members,SUM(CASE WHEN {HOUR}<6 THEN 1 ELSE 0 END) late_night,"
                          f"SUM(CASE WHEN {WEEKDAY} IN (0,6) THEN 1 ELSE 0 END) weekend,{types} FROM messages m{clause}", args)[0]
        days = self._read(f"SELECT {DAY} day,COUNT(*) n FROM messages m{clause} GROUP BY day ORDER BY day", args)
        hours = [0] * 24
        for r in self._read(f"SELECT {HOUR} h,COUNT(*) n FROM messages m{clause} GROUP BY h", args):
            hours[r["h"]] = r["n"]
        weekdays = [0] * 7  # 周一在前
        for r in self._read(f"SELECT {WEEKDAY} w,COUNT(*) n FROM messages m{clause} GROUP BY w", args):
            weekdays[(r["w"] + 6) % 7] = r["n"]
        total = head["total"] or 0
        span = ((dt.date.fromisoformat(days[-1]["day"]) - dt.date.fromisoformat(days[0]["day"])).days + 1) if days else 0
        busiest = max(days, key=lambda r: r["n"]) if days else None
        return {
            "total": total, "members": head["members"] or 0, "first_time": head["first_time"], "last_time": head["last_time"],
            "span_days": span, "active_days": len(days), "daily_avg": round(total / span) if span else 0,
            "active_rate": round(len(days) / span, 4) if span else 0,
            "late_night": head["late_night"] or 0, "weekend": head["weekend"] or 0,
            "busiest_day": busiest, "longest_streak": longest_streak(r["day"] for r in days),
            "busiest_weekday": max(range(7), key=lambda i: weekdays[i]) if total else None,
            "peak_hour": max(range(24), key=lambda i: hours[i]) if total else None,
            "days": days, "hours": hours, "weekdays": weekdays,
            "types": [{"key": key, "label": label, "n": head["t_" + key] or 0} for key, label, _ in TYPE_SQL],
        }

    def ranking(self, platform="", group_id="", since=0, until=0, limit=10):
        where, args = self._scope(platform, group_id, since, until)

        def top(extra=""):
            w = where + ([extra] if extra else [])
            clause = (" WHERE " + " AND ".join(w)) if w else ""
            return self._read(f"SELECT m.sender_id,COUNT(*) n FROM messages m{clause} GROUP BY m.sender_id ORDER BY n DESC,m.sender_id LIMIT ?", [*args, limit])

        lists = {"speakers": top(), "images": top("m.outline LIKE '%[Image]%'"), "faces": top("m.outline LIKE '%[Face]%'"),
                 "night": top(f"{HOUR}<6")}
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        per = {}
        for r in self._read(f"SELECT DISTINCT m.sender_id,{DAY} day FROM messages m{clause} ORDER BY m.sender_id,day", args):
            per.setdefault(r["sender_id"], []).append(r["day"])
        streaks = sorted(((longest_streak(v), len(v), k) for k, v in per.items()), key=lambda x: (-x[0], -x[1], x[2]))[:limit]
        lists["streak"] = [{"sender_id": k, "n": s, "active_days": d} for s, d, k in streaks]
        # 昵称取范围内最近一次使用的
        ids = list({i["sender_id"] for items in lists.values() for i in items})
        names = {}
        if ids:
            w = where + ["m.sender_id IN (" + ",".join("?" * len(ids)) + ")"]
            for r in self._read("SELECT m.sender_id,m.sender_name FROM messages m WHERE " + " AND ".join(w) + " ORDER BY m.source_time", [*args, *ids]):
                names[r["sender_id"]] = r["sender_name"]
        for items in lists.values():
            for i in items:
                i["sender_name"] = names.get(i["sender_id"], i["sender_id"])
        total = self._read(f"SELECT COUNT(*) n FROM messages m{clause}", args)[0]["n"]
        return {"total": total, "lists": lists}

    def context(self, message_id, before=20, after=20):
        cols = "id,platform,group_id,group_name,sender_id,sender_name,source_time,text,outline,attachments"
        with self.read_lock:
            row = self.reader.execute(f"SELECT {cols} FROM messages WHERE id=?", (message_id,)).fetchone()
            if row is None:
                return None
            row = dict(row)
            key = (row["platform"], row["group_id"], row["source_time"], row["source_time"], row["id"])
            # 多取一条，用来准确判断前后是否还有更多
            prev = [dict(r) for r in self.reader.execute(
                f"SELECT {cols} FROM messages WHERE platform=? AND group_id=? AND (source_time<? OR (source_time=? AND id<?)) ORDER BY source_time DESC,id DESC LIMIT ?", (*key, before + 1))]
            nxt = [dict(r) for r in self.reader.execute(
                f"SELECT {cols} FROM messages WHERE platform=? AND group_id=? AND (source_time>? OR (source_time=? AND id>?)) ORDER BY source_time,id LIMIT ?", (*key, after + 1))]
        return {"focus": message_id, "items": prev[:before][::-1] + [row] + nxt[:after],
                "more_before": len(prev) > before, "more_after": len(nxt) > after}

    def timeline(self, platform="", group_id="", sender="", since=0, until=0, before=0, after=0, around=0, limit=40):
        """聊天流：按 (发送时间, id) 排序的一段连续消息，供气泡视图上下翻页。
        before/after 是游标消息 id（取它之前/之后的一段），around 以某条消息为中心；都不传时取最新一段。"""
        limit = max(10, min(200, int(limit)))
        where, args = self._scope(platform, group_id, since, until)
        if sender:
            where.append("(m.sender_name LIKE ? ESCAPE '\\' OR m.sender_id=?)"); args += [self._like(sender), sender]
        cols = "m.id,m.platform,m.group_id,m.group_name,m.sender_id,m.sender_name,m.source_time,m.text,m.outline,m.attachments"
        older, newer = "(m.source_time<? OR (m.source_time=? AND m.id<?))", "(m.source_time>? OR (m.source_time=? AND m.id>?))"
        desc, asc = "m.source_time DESC,m.id DESC", "m.source_time,m.id"

        def fetch(extra, extra_args, order, n):
            w = where + extra
            clause = (" WHERE " + " AND ".join(w)) if w else ""
            return self._read(f"SELECT {cols} FROM messages m{clause} ORDER BY {order} LIMIT ?", [*args, *extra_args, n])

        def anchor(mid):
            rows = self._read(f"SELECT {cols} FROM messages m WHERE m.id=?", (int(mid),))
            if not rows:
                raise LookupError("消息不存在")
            return rows[0]

        result = {"items": [], "has_older": False, "has_newer": False, "focus": None}
        if around:
            focus, half = anchor(around), limit // 2
            key = [focus["source_time"], focus["source_time"], focus["id"]]
            prev, nxt = fetch([older], key, desc, half + 1), fetch([newer], key, asc, half + 1)
            result.update(items=prev[:half][::-1] + [focus] + nxt[:half], has_older=len(prev) > half,
                          has_newer=len(nxt) > half, focus=focus["id"])
        elif after:
            a = anchor(after)
            rows = fetch([newer], [a["source_time"], a["source_time"], a["id"]], asc, limit + 1)
            result.update(items=rows[:limit], has_newer=len(rows) > limit)
        else:
            extra, extra_args = [], []
            if before:
                b = anchor(before)
                extra, extra_args = [older], [b["source_time"], b["source_time"], b["id"]]
            rows = fetch(extra, extra_args, desc, limit + 1)
            result.update(items=rows[:limit][::-1], has_older=len(rows) > limit)
        return result

    def search_text(self, query, group="", since=0, limit=40):
        """给大肥鱼的检索工具：全部关键词同时命中，按时间倒序；群可填群号或群名片段。"""
        where, args = self._scope(since=since)
        if group:
            where.append("(m.group_id=? OR m.group_name LIKE ? ESCAPE '\\')"); args += [group, self._like(group)]
        self._text_filter(query, where, args, "m.")
        clause = " WHERE " + " AND ".join(where)
        total = self._read(f"SELECT COUNT(*) n FROM messages m{clause}", args)[0]["n"]
        rows = self._read(f"SELECT m.id,m.group_id,m.group_name,m.sender_name,m.source_time,m.text,m.outline FROM messages m{clause} "
                          "ORDER BY m.source_time DESC,m.id DESC LIMIT ?", [*args, limit])
        return {"total": total, "items": rows}

    def export(self, group_id="", platform=""):
        """Stream JSONL into the plugin data directory; no user-controlled file paths."""
        import uuid
        path = self.path.parent / "exports" / (uuid.uuid4().hex + ".jsonl")
        path.parent.mkdir(exist_ok=True)
        with self.lock, path.open("w", encoding="utf-8") as out:
            filters, args = [], []
            if group_id:
                filters.append("group_id=?"); args.append(group_id)
            if platform:
                filters.append("platform=?"); args.append(platform)
            cursor = self.db.execute("SELECT * FROM messages" + (" WHERE " + " AND ".join(filters) if filters else "") + " ORDER BY id", args)
            for row in cursor:
                out.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
        return path
