"""v1.6.0 群洞察、榜单、全文搜索与上下文：使用临时真实 SQLite，不涉及 QQ 或模型。"""
import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from settings import Settings
from storage import Store, longest_streak

TZ = dt.timezone(dt.timedelta(hours=8))


def bj(y, m, d, h=12, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=TZ).timestamp()


def record(n, text="普通消息", sender="s1", name="甲", t=None, group="g1", outline="", platform="p"):
    return dict(platform=platform, group_id=group, group_name="群" + group, message_id=str(n),
                sender_id=sender, sender_name=name, source_time=t if t is not None else bj(2026, 10, 20) + n,
                text=text, outline=outline)


class InsightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "messages.sqlite3"
        self.store = Store(self.path)
        self.s = Settings.parse({})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, *records):
        for r in records:
            self.store.ingest(r, self.s)

    def test_longest_streak(self):
        self.assertEqual(longest_streak([]), 0)
        self.assertEqual(longest_streak(["2026-10-01", "2026-10-02", "2026-10-04", "2026-10-05", "2026-10-06"]), 3)

    def test_insight_counts_time_buckets_and_types_in_beijing_time(self):
        # 2026-10-24 是周六；北京时间 02:00 算深夜，跨 UTC 日期也按北京时间归日
        self.add(record(1, t=bj(2026, 10, 24, 2)), record(2, t=bj(2026, 10, 24, 14), outline="[Image]"),
                 record(3, t=bj(2026, 10, 26, 9), sender="s2", outline="[Face] [Reply]"),
                 record(4, t=bj(2026, 10, 27, 9), group="g2"))
        r = self.store.insight(group_id="g1")
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["members"], 2)
        self.assertEqual(r["span_days"], 3)
        self.assertEqual(r["active_days"], 2)
        self.assertEqual(r["late_night"], 1)
        self.assertEqual(r["weekend"], 2)
        self.assertEqual(r["longest_streak"], 1)
        self.assertEqual(r["busiest_day"], {"day": "2026-10-24", "n": 2})
        self.assertEqual(r["hours"][2], 1)
        self.assertEqual(r["weekdays"][5], 2)  # 周一在前，索引 5 为周六
        types = {t["key"]: t["n"] for t in r["types"]}
        self.assertEqual((types["text"], types["image"], types["face"], types["reply"]), (1, 1, 1, 1))
        self.assertEqual(self.store.insight()["total"], 4)
        self.assertEqual(self.store.insight(since=bj(2026, 10, 26, 0))["total"], 2)

    def test_insight_on_empty_scope(self):
        r = self.store.insight(group_id="none")
        self.assertEqual((r["total"], r["span_days"], r["daily_avg"], r["busiest_day"]), (0, 0, 0, None))

    def test_ranking_lists_and_latest_nickname(self):
        self.add(record(1, sender="a", name="旧名"), record(2, sender="a", name="新名", outline="[Image]"),
                 record(3, sender="b", name="乙", t=bj(2026, 10, 21, 3)),
                 record(4, sender="a", name="新名", t=bj(2026, 10, 21, 12)))
        lists = self.store.ranking(group_id="g1")["lists"]
        self.assertEqual([(i["sender_id"], i["n"], i["sender_name"]) for i in lists["speakers"]],
                         [("a", 3, "新名"), ("b", 1, "乙")])
        self.assertEqual([i["sender_id"] for i in lists["images"]], ["a"])
        self.assertEqual([i["sender_id"] for i in lists["night"]], ["b"])
        self.assertEqual(lists["streak"][0], {"sender_id": "a", "n": 2, "active_days": 2, "sender_name": "新名"})

    def test_fulltext_search_short_terms_and_injection_safety(self):
        self.add(record(1, text="OpenWrt 刷机后 5G 掉速"), record(2, text="刷机成功了"),
                 record(3, text='带引号的 "刷机" 讨论'))
        self.assertTrue(self.store.fts)
        self.assertEqual(self.store.page("messages", search="刷机后")["total"], 1)
        self.assertEqual(self.store.page("messages", search="刷机")["total"], 3)       # 两字退回 LIKE
        self.assertEqual(self.store.page("messages", search="openwrt 掉速")["total"], 1)  # 多词同时命中，不区分大小写
        self.assertEqual(self.store.page("messages", search='"刷机"')["total"], 1)  # 引号按字面匹配
        self.assertEqual(self.store.page("messages", search='"刷机" OR')["total"], 0)
        self.assertEqual(self.store.page("messages", search="NEAR( AND")["total"], 0)

    def test_fulltext_index_built_for_existing_database(self):
        self.add(record(1, text="旧库里已有的消息"))
        self.store.close()
        db = sqlite3.connect(self.path)  # 模拟 v1.5.0 及以前的库：没有全文索引
        db.executescript("DROP TRIGGER messages_fts_ai; DROP TRIGGER messages_fts_ad; DROP TRIGGER messages_fts_au; DROP TABLE messages_fts;")
        db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.page("messages", search="已有的消息")["total"], 1)
        self.add(record(2, text="升级后新来的消息"))
        self.assertEqual(self.store.page("messages", search="新来的消息")["total"], 1)

    def test_context_returns_neighbours_in_same_group_only(self):
        self.add(*[record(i, text=f"第{i}条") for i in range(1, 8)], record(99, group="g2", text="别的群"))
        focus = self.store.page("messages", search="第4条")["items"][0]["id"]
        r = self.store.context(focus, 2, 2)
        self.assertEqual([m["text"] for m in r["items"]], ["第2条", "第3条", "第4条", "第5条", "第6条"])
        self.assertTrue(r["more_before"] and r["more_after"])
        self.assertIsNone(self.store.context(123456, 2, 2))

    def test_search_text_for_chat_tool(self):
        self.add(record(1, text="路由器刷机教程", group="111", t=bj(2026, 10, 1)),
                 record(2, text="路由器刷机翻车", group="222", t=bj(2026, 10, 25)))
        self.assertEqual(self.store.search_text("路由器 刷机")["total"], 2)
        self.assertEqual(self.store.search_text("路由器", group="群222")["total"], 1)
        self.assertEqual(self.store.search_text("路由器", group="111")["items"][0]["text"], "路由器刷机教程")
        self.assertEqual(self.store.search_text("路由器", since=bj(2026, 10, 20))["total"], 1)
        self.assertEqual(self.store.search_text("不存在的内容")["total"], 0)


    def fts_counts(self, path=None):
        db = sqlite3.connect(path or self.path)
        try:
            return (db.execute("SELECT COUNT(*) FROM messages_fts_docsize").fetchone()[0],
                    db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                    sorted(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")))
        finally:
            db.close()

    def test_fulltext_repairs_missing_triggers_without_duplicates(self):
        self.add(record(1, text="触发器在时写入的消息"))
        self.store.close()
        db = sqlite3.connect(self.path)  # 表还在、触发器丢了，期间又入库一条
        db.executescript("DROP TRIGGER messages_fts_ai; DROP TRIGGER messages_fts_ad; DROP TRIGGER messages_fts_au;")
        db.execute("INSERT INTO messages(platform,group_id,group_name,message_id,sender_id,sender_name,source_time,received_at,text,outline,eligible) "
                   "VALUES('p','g1','群g1','x','s1','甲',1,1,'触发器缺失时写入的消息','',0)")
        db.commit()
        db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.page("messages", search="缺失时写入")["total"], 1)
        self.assertEqual(self.store.page("messages", search="写入的消息")["total"], 2)
        indexed, total, triggers = self.fts_counts()
        self.assertEqual((indexed, total), (2, 2))
        self.assertEqual(triggers, ["messages_fts_ad", "messages_fts_ai", "messages_fts_au"])
        self.store.close()
        self.store = Store(self.path)  # 再次启动：行数一致，不重复建索引
        self.assertEqual(self.fts_counts()[:2], (2, 2))
        self.assertEqual(self.store.page("messages", search="写入的消息")["total"], 2)

    def test_fulltext_unavailable_falls_back_to_like_and_ingest_still_works(self):
        self.store.close()

        class NoFts(Store):
            FTS_TOKENIZER = "no_such_tokenizer"

        path = Path(self.tmp.name) / "nofts.sqlite3"
        self.store = NoFts(path)
        self.assertFalse(self.store.fts)
        self.add(record(1, text="OpenWrt 刷机后 5G 掉速"))
        self.assertEqual(self.store.page("messages", search="刷机后 openwrt")["total"], 1)
        self.assertEqual(self.store.search_text("刷机后")["total"], 1)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0], 0)
        db.close()

    def test_mixed_short_and_long_terms(self):
        self.add(record(1, text="OpenWrt 刷机后 5G 掉速"), record(2, text="OpenWrt 刷机后一切正常"),
                 record(3, text="5G 掉速但没刷机"))
        self.assertEqual(self.store.page("messages", search="5G openwrt")["total"], 1)
        self.assertEqual(self.store.page("messages", search="掉速 刷机后")["total"], 1)
        self.assertEqual(self.store.page("messages", search="  5G   掉速  ")["total"], 2)
        self.assertEqual(self.store.page("messages", search="100%")["total"], 0)  # LIKE 通配符按字面

    def test_context_more_flags_are_exact_at_boundaries(self):
        self.add(*[record(i, text=f"第{i}条") for i in range(1, 6)])
        first = self.store.page("messages", search="第1条")["items"][0]["id"]
        third = self.store.page("messages", search="第3条")["items"][0]["id"]
        r = self.store.context(third, 2, 2)
        self.assertEqual(len(r["items"]), 5)
        self.assertEqual((r["more_before"], r["more_after"]), (False, False))
        r = self.store.context(first, 2, 2)
        self.assertEqual([m["text"] for m in r["items"]], ["第1条", "第2条", "第3条"])
        self.assertEqual((r["more_before"], r["more_after"]), (False, True))

    def test_insight_and_ranking_respect_platform_scope(self):
        self.add(record(1, platform="p1"), record(2, platform="p2", sender="s2", name="乙"))
        self.assertEqual(self.store.insight(platform="p1")["total"], 1)
        self.assertEqual([i["sender_id"] for i in self.store.ranking(platform="p2")["lists"]["speakers"]], ["s2"])
        r = self.store.ranking(group_id="none")
        self.assertEqual(r["total"], 0)
        self.assertTrue(all(v == [] for v in r["lists"].values()))


    def test_timeline_latest_older_newer_and_around(self):
        self.add(*[record(i, text=f"第{i}条", t=1000 + i) for i in range(1, 26)])
        self.add(record(99, text="别的群", group="g2", t=1010))
        latest = self.store.timeline("p", "g1", limit=10)
        self.assertEqual([r["text"] for r in latest["items"]], [f"第{i}条" for i in range(16, 26)])  # 升序，最新在末尾
        self.assertTrue(latest["has_older"])
        older = self.store.timeline("p", "g1", before=latest["items"][0]["id"], limit=10)
        self.assertEqual([r["text"] for r in older["items"]], [f"第{i}条" for i in range(6, 16)])
        oldest = self.store.timeline("p", "g1", before=older["items"][0]["id"], limit=10)
        self.assertEqual(len(oldest["items"]), 5)
        self.assertFalse(oldest["has_older"])
        mid = next(r for r in older["items"] if r["text"] == "第10条")
        around = self.store.timeline("p", "g1", around=mid["id"], limit=10)
        self.assertEqual(around["focus"], mid["id"])
        self.assertEqual([r["text"] for r in around["items"]], [f"第{i}条" for i in range(5, 16)])
        self.assertTrue(around["has_older"] and around["has_newer"])
        newer = self.store.timeline("p", "g1", after=around["items"][-1]["id"], limit=10)
        self.assertEqual([r["text"] for r in newer["items"]], [f"第{i}条" for i in range(16, 26)])
        self.assertFalse(newer["has_newer"])
        self.assertTrue(all(r["group_id"] == "g1" for r in latest["items"] + older["items"] + around["items"]))
        with self.assertRaises(LookupError):
            self.store.timeline("p", "g1", around=99999)

    def test_timeline_sender_filter_and_group_last_message(self):
        self.add(record(1, text="甲说", sender="s1", name="甲", t=100), record(2, text="乙说", sender="s2", name="乙", t=200),
                 record(3, text="", sender="s1", name="甲", t=300, outline="[Image]"))
        only = self.store.timeline("p", "g1", sender="s1")
        self.assertEqual([r["sender_id"] for r in only["items"]], ["s1", "s1"])
        last = self.store.groups()[0]["last_message"]
        self.assertEqual(last["sender_name"], "甲")
        self.assertEqual(last["outline"], "[Image]")

if __name__ == "__main__":
    unittest.main()
