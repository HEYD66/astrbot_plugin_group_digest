"""控制台 v1.4.0 新增的查询接口：使用临时真实 SQLite，不涉及 QQ 或模型。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from engine import today
from settings import Settings
from storage import Store


def record(n, text="ordinary text", sender="s1", name="甲", t=None, group="g1"):
    return dict(platform="p", group_id=group, group_name="测试群", message_id=str(n),
                sender_id=sender, sender_name=name, source_time=t if t is not None else 1700000000 + n,
                text=text, outline="")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "messages.sqlite3")
        self.s = Settings.parse({"recipients": ["10001"], "keywords": ["链接"], "forward_format": "text",
                                 "summary_threshold": 100})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_message_filters_by_sender_and_time(self):
        self.store.ingest(record(1, sender="s1", name="甲", t=100), self.s)
        self.store.ingest(record(2, sender="s2", name="乙", t=200), self.s)
        self.store.ingest(record(3, sender="s1", name="甲", t=300), self.s)
        self.assertEqual(self.store.page("messages", sender="甲")["total"], 2)
        self.assertEqual(self.store.page("messages", sender="s2")["total"], 1)
        self.assertEqual(self.store.page("messages", since=150, until=300)["total"], 1)
        # 发言人筛选中的通配符按普通字符处理
        self.assertEqual(self.store.page("messages", sender="%")["total"], 0)

    def test_job_list_includes_hits_and_preview(self):
        self.store.ingest(record(1, text="这里有个链接 https://example.com", name="甲"), self.s)
        item = self.store.page("jobs")["items"][0]
        self.assertEqual(item["hits"], ["链接"])
        self.assertEqual(item["preview"]["sender_name"], "甲")
        self.assertIn("https://example.com", item["preview"]["text"])
        for hidden in ("targets", "record_ids", "context_state", "delivery_plan", "generation_state"):
            self.assertNotIn(hidden, item)

    def test_job_detail_lists_records_and_marks_matched(self):
        self.store.ingest(record(1, text="分享链接"), self.s)
        job_id = self.store.page("jobs")["items"][0]["id"]
        detail = self.store.job_detail(job_id)
        self.assertEqual(detail["hits"], ["链接"])
        self.assertEqual(len(detail["records"]), 1)
        self.assertTrue(detail["records"][0]["matched"])
        self.assertNotIn("targets", detail)
        self.assertIsNone(self.store.job_detail(9999))

    def test_card_job_detail_marks_only_matched_among_context(self):
        s = Settings.parse({"recipients": ["10001"], "keywords": ["链接"], "forward_format": "card",
                            "forward_context_before": 2, "forward_context_after": 0, "forward_context_wait": 0})
        self.store.ingest(record(1, text="前文一", t=100), s)
        self.store.ingest(record(2, text="前文二", t=101), s)
        self.store.ingest(record(3, text="发个链接", t=102), s)
        self.store.finish_context(s)
        job_id = self.store.page("jobs")["items"][0]["id"]
        detail = self.store.job_detail(job_id)
        self.assertEqual([r["text"] for r in detail["records"]], ["前文一", "前文二", "发个链接"])
        self.assertEqual([r["matched"] for r in detail["records"]], [False, False, True])

    def test_summary_preview_uses_result_head(self):
        with self.store.lock, self.store.db:
            self.store.db.execute(
                "INSERT INTO jobs(kind,platform,group_id,group_name,record_ids,targets,status,result,created_at) VALUES('summary','p','g1','测试群','[1,2]','[]','done',?,1)",
                ("【速览】\n今天   讨论了部署",))
        item = self.store.page("jobs", kind="summary")["items"][0]
        self.assertEqual(item["preview"]["text"], "【速览】 今天 讨论了部署")
        self.assertEqual(self.store.job_detail(item["id"])["records"], [])

    def test_status_counts_failures_joined_groups_and_daily_delta(self):
        self.store.remember_groups("p", [{"group_id": 1111, "group_name": "一"}, {"group_id": 2222, "group_name": "二"}])
        self.store.ingest(record(1, text="链接"), self.s)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE jobs SET status='failed'")
        status = self.store.status(today())
        self.assertEqual(status["failed_by_kind"], {"match": 1})
        self.assertEqual(status["joined_group_count"], 2)
        # received_at 为当前时间，因此计入今日
        self.assertEqual(status["messages_today"], 1)
        self.assertEqual(status["messages_yesterday"], 0)
        self.assertEqual(status["llm_calls_yesterday"], 0)

    def test_draft_rules_match_without_saving(self):
        draft = Settings.parse({**self.s.values, "keywords": ["路由"]})
        self.assertEqual(draft.matches("改一下路由"), ["路由"])
        self.assertEqual(self.s.matches("改一下路由"), [])


if __name__ == "__main__":
    unittest.main()
