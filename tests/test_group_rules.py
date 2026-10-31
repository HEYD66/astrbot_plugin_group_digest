"""v1.7.0 单群规则：按群覆盖转发规则和总结规则。使用临时真实 SQLite，不涉及 QQ 或模型。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from settings import Settings
from storage import Store


def record(n, group="10001", text="普通消息"):
    return dict(platform="p", group_id=group, group_name="群" + group, message_id=f"{group}-{n}",
                sender_id="s1", sender_name="甲", source_time=1700000000 + n, text=text, outline="")


class GroupRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "messages.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_for_group_overrides_only_listed_keys(self):
        s = Settings.parse({"keywords": ["全局"], "summary_threshold": 100,
                            "group_rules": {"20002": {"keywords": ["本群"], "summary_threshold": 5}}})
        g = s.for_group("20002")
        self.assertEqual(g["keywords"], ["本群"])
        self.assertEqual(g["summary_threshold"], 5)
        self.assertEqual(g["match_mode"], "any")       # 没覆盖的项跟随全局
        self.assertIs(s.for_group("30003"), s)          # 没有单独规则的群直接用全局
        self.assertEqual(s["keywords"], ["全局"])

    def test_invalid_group_rules_rejected(self):
        for bad in ({"abc": {}}, {"20002": {"recipients": ["123456"]}}, {"20002": {"summary_threshold": 1}},
                    {"20002": "x"}, []):
            with self.assertRaises(ValueError):
                Settings.parse({"group_rules": bad})
        # 空的覆盖项不保存，等于跟随全局
        self.assertEqual(Settings.parse({"group_rules": {"20002": {}}})["group_rules"], {})

    def test_ingest_uses_group_keywords_and_summary_switch(self):
        s = Settings.parse({"recipients": ["123456"], "keywords": ["全局词"], "forward_format": "text",
                            "group_rules": {"20002": {"keywords": ["本群词"], "summary_enabled": False}}})
        self.store.ingest(record(1, "10001", "含全局词"), s)
        self.store.ingest(record(2, "20002", "含全局词"), s)   # 本群改了关键词，全局词不再命中
        self.store.ingest(record(3, "20002", "含本群词"), s)
        jobs = self.store.page("jobs", kind="match")["items"]
        self.assertEqual(sorted((j["group_id"], tuple(j["hits"])) for j in jobs),
                         [("10001", ("全局词",)), ("20002", ("本群词",))])
        eligible = dict(self.store.db.execute("SELECT group_id,SUM(eligible) FROM messages GROUP BY group_id").fetchall())
        self.assertEqual(eligible, {"10001": 1, "20002": 0})   # 本群关闭了总结，不计入

    def test_reserve_batches_uses_group_threshold(self):
        s = Settings.parse({"recipients": ["123456"], "summary_threshold": 10,
                            "group_rules": {"20002": {"summary_threshold": 3, "summary_instruction": "只看技术内容"}}})
        for i in range(4):
            self.store.ingest(record(i, "10001"), s)
            self.store.ingest(record(i, "20002"), s)
        self.assertEqual(self.store.reserve_batches(s), 1)
        jobs = self.store.page("jobs", kind="summary")["items"]
        self.assertEqual([(j["group_id"], j["record_count"], j["instruction"]) for j in jobs], [("20002", 3, "只看技术内容")])

    def test_claim_skips_jobs_of_group_with_summary_turned_off(self):
        s = Settings.parse({"recipients": ["123456"], "summary_threshold": 2})
        for i in range(2):
            self.store.ingest(record(i, "20002"), s)
        self.store.reserve_batches(s)
        off = Settings.parse({"recipients": ["123456"], "summary_threshold": 2,
                              "group_rules": {"20002": {"summary_enabled": False}}})
        self.assertIsNone(self.store.claim("generate", off))
        self.assertIsNotNone(self.store.claim("generate", s))


if __name__ == "__main__":
    unittest.main()
