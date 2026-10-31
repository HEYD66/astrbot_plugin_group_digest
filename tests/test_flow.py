"""Fault-injection tests use transport/model substitutes, never production messages."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from engine import Engine, today, summary_plaintext, split_summary, summary_messages
from settings import Settings
from storage import Store


def record(n, group="test-group", text="ordinary text", platform="test-instance"):
    return dict(platform=platform, group_id=group, group_name=group, message_id=str(n),
                sender_id="test-sender", sender_name="测试发言者", source_time=1700000000 + n,
                text=text, outline="")


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "messages.sqlite3"
        self.db = Store(self.path)
        self.settings = Settings.parse({"recipients": ["10001"], "summary_threshold": 2, "forward_format": "text",
                                        "keywords": ["interesting"], "retry_limit": 1})
        self.calls, self.sent, self.logs = [], [], []

        async def generate(prompt, system, platform, group, settings):
            self.calls.append((prompt, system, platform, group))
            return "资料摘要，含消息依据。"

        async def send(target, text):
            self.sent.append((target, text))
            return True

        self.engine = Engine(self.db, lambda: self.settings, generate, send, self.logs.append)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def jobs(self):
        return self.db.page("jobs")["items"]

    def insert(self, n=2, group="test-group", text="ordinary text"):
        for i in range(n):
            self.db.ingest(record(i, group, text), self.settings)

    async def test_exact_threshold_and_per_group_isolation(self):
        self.db.ingest(record(1, "A"), self.settings)
        self.db.ingest(record(1, "B"), self.settings)
        self.assertFalse(await self.engine.step("generate"))
        self.db.ingest(record(2, "A"), self.settings)
        self.assertTrue(await self.engine.step("generate"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][3], "A")
        self.assertEqual(self.jobs()[0]["record_count"], 2)
        self.assertEqual(self.db.status(today())["pending_message_count"], 1)

    async def test_duplicate_event_is_not_counted_or_forwarded_twice(self):
        r = record(1, text="interesting")
        self.db.ingest(r, self.settings)
        self.assertIsNone(self.db.ingest(r, self.settings))
        self.assertEqual(self.db.status(today())["message_count"], 1)
        self.assertEqual(len(self.jobs()), 1)
        await self.engine.step("deliver")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("interesting", self.sent[0][1])
        self.assertIn(":FriendMessage:", self.sent[0][0])

    async def test_no_recipient_records_without_model_or_jobs(self):
        self.settings = Settings.parse({"summary_threshold": 2, "keywords": ["interesting"]})
        self.insert(text="interesting")
        self.assertFalse(await self.engine.step("generate"))
        self.assertFalse(await self.engine.step("deliver"))
        self.assertEqual(len(self.jobs()), 0)
        # Configuring a recipient later must not unexpectedly send historical records.
        self.settings = Settings.parse({"recipients": ["10001"], "summary_threshold": 2})
        self.assertFalse(await self.engine.step("generate"))
        self.assertFalse(self.calls)

    async def test_send_failure_preserves_summary_and_retry_does_not_regenerate(self):
        self.insert()
        await self.engine.step("generate")
        real_send = self.engine.send

        async def fail(*args):
            raise ConnectionError("secret_token_should_not_appear")

        self.engine.send = fail
        await self.engine.step("deliver")
        job = self.jobs()[0]
        self.assertEqual(job["status"], "failed")
        self.assertTrue(job["result"])
        self.assertNotIn("secret_token", job["last_error"])
        self.assertTrue(self.db.retry(job["id"]))
        self.engine.send = real_send
        await self.engine.step("deliver")
        self.assertEqual(self.jobs()[0]["status"], "done")
        self.assertEqual(len(self.calls), 1)

    async def test_long_input_covers_tail_and_reduces(self):
        self.settings = Settings.parse({**self.settings.values, "summary_input_chars": 2000})
        self.db.ingest(record(1, text="HEAD" + "甲" * 5000 + "TAIL"), self.settings)
        self.db.ingest(record(2, text="FINALMESSAGE"), self.settings)
        await self.engine.step("generate")
        self.assertGreater(len(self.calls), 2)
        self.assertTrue(any("HEAD" in x[0] for x in self.calls))
        self.assertTrue(any("TAIL" in x[0] for x in self.calls))
        self.assertTrue(any("FINALMESSAGE" in x[0] for x in self.calls))
        self.assertEqual(self.jobs()[0]["status"], "ready")

    async def test_daily_budget_defers_instead_of_losing_batch(self):
        self.settings = Settings.parse({**self.settings.values, "max_llm_calls_per_day": 1, "summary_input_chars": 2000})
        self.insert(text="甲" * 2000)
        await self.engine.step("generate")
        job = self.jobs()[0]
        self.assertEqual(job["status"], "pending")
        self.assertIn("次日", job["last_error"])
        self.assertEqual(job["generation_calls"], 1)
        self.assertEqual(self.db.status(today())["llm_calls_today"], 1)

    async def test_changed_recipient_or_disabled_scope_pauses_old_jobs(self):
        self.insert(text="interesting")
        self.settings = Settings.parse({**self.settings.values, "recipients": ["10002"]})
        self.assertFalse(await self.engine.step("deliver"))
        self.assertFalse(self.sent)
        self.settings = Settings.parse({**self.settings.values, "enabled": False})
        self.assertFalse(await self.engine.step("generate"))

    async def test_restart_recovers_claims_and_preserves_batch_allocation(self):
        self.insert()
        self.db.reserve_batches(self.settings)
        job = self.db.claim("generate", self.settings)
        self.db.close()
        self.db = Store(self.path)
        self.engine.store = self.db
        self.assertEqual(self.db.page("jobs")["items"][0]["status"], "pending")
        self.assertEqual(self.db.reserve_batches(self.settings), 0)
        await self.engine.step("generate")
        self.assertEqual(self.jobs()[0]["id"], job["id"])
        self.assertEqual(len(self.jobs()), 1)
        with self.db.lock:
            self.assertEqual(self.db.db.execute("PRAGMA quick_check").fetchone()[0], "ok")

    async def test_partial_delivery_resume_skips_successful_parts(self):
        self.settings = Settings.parse({**self.settings.values, "delivery_message_chars": 300})
        self.db.ingest(record(1, text="interesting" + "字" * 700), self.settings)
        count = 0

        async def partial(target, text):
            nonlocal count
            count += 1
            if count == 2:
                raise ConnectionError()
            self.sent.append((target, text))
            return True

        self.engine.send = partial
        await self.engine.step("deliver")
        job = self.jobs()[0]
        self.assertEqual(job["delivery_progress"], 1)
        first = self.sent[0]
        self.assertTrue(self.db.retry(job["id"]))
        await self.engine.step("deliver")
        self.assertEqual(self.jobs()[0]["status"], "done")
        self.assertEqual(self.sent.count(first), 1)

    async def test_summary_delivery_resume_preserves_saved_layout_without_regeneration(self):
        self.settings = Settings.parse({**self.settings.values, "delivery_message_chars": 300})
        self.insert()
        await self.engine.step("generate")
        job = self.jobs()[0]
        result = "【速览】\n资料概述。[消息1]\n\n【主要话题】\n" + ("• 可核对的结论。[消息2]\n" * 45)
        self.db.update(job["id"], result=result)
        job = dict(self.db.db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
        plan = await self.engine.text_plan(job, self.db.records(json.loads(job["record_ids"])))
        self.assertGreater(len(plan), 1)
        self.assertTrue(all(len(p["text"]) <= 300 and "【群聊简报" in p["text"] for p in plan))
        original_send = self.engine.send
        attempts = 0

        async def failing_send(target, text):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise ConnectionError()
            return await original_send(target, text)

        self.engine.send = failing_send
        await self.engine.step("deliver")
        self.assertEqual(self.jobs()[0]["delivery_progress"], 1)
        self.assertTrue(self.db.retry(job["id"]))
        self.engine.send = original_send
        await self.engine.step("deliver")
        self.assertEqual([x[1] for x in self.sent], [x["text"] for x in plan])
        self.assertEqual(self.jobs()[0]["result"], result)
        self.assertEqual(len(self.calls), 1)

    async def test_existing_summary_delivery_plan_is_not_reformatted_or_resent(self):
        self.insert()
        await self.engine.step("generate")
        job = self.jobs()[0]
        old = [{"target": "test-instance:FriendMessage:10001", "text": "旧版已保存的投递内容"}]
        self.db.update(job["id"], delivery_plan=json.dumps(old))
        await self.engine.step("deliver")
        self.assertEqual(self.sent, [(old[0]["target"], old[0]["text"])])
        self.assertFalse(await self.engine.step("deliver"))

    async def test_export_and_escaped_search_use_real_database(self):
        text = "原文%_\\'\n第二行<img onerror=alert(1)>"
        self.db.ingest(record(1, text=text), self.settings)
        self.assertEqual(self.db.page("messages", search="%_\\'")["total"], 1)
        self.assertEqual(self.db.page("messages", search="' OR 1=1 --")["total"], 0)
        path = self.db.export()
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["text"], text)

    async def test_dashboard_kind_and_platform_filters_and_scoped_export(self):
        self.db.ingest(record(1, text="interesting"), self.settings)
        self.db.ingest(record(1, text="interesting", platform="second-instance"), self.settings)
        self.db.ingest(record(2), self.settings)
        self.db.reserve_batches(self.settings)
        self.assertEqual(self.db.page("jobs", kind="match")["total"], 2)
        self.assertEqual(self.db.page("jobs", kind="summary")["total"], 1)
        self.assertEqual(self.db.page("messages", platform="second-instance")["total"], 1)
        self.assertEqual(self.db.page("jobs", kind="match", platform="second-instance")["total"], 1)
        path = self.db.export("test-group", "second-instance")
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "second-instance")
        self.assertEqual(self.db.status(today())["kind_counts"], {"match": 2, "summary": 1})
        with self.assertRaises(ValueError):
            self.db.page("jobs", kind="untrusted")

    async def test_activity_uses_china_calendar_and_excludes_future(self):
        import datetime as dt
        self.insert(n=3)
        tz = dt.timezone(dt.timedelta(hours=8))
        stamps = [dt.datetime(2026, 10, 24, 23, 59, tzinfo=tz).timestamp(),
                  dt.datetime(2026, 10, 25, 0, 0, tzinfo=tz).timestamp(),
                  dt.datetime(2026, 10, 26, 0, 0, tzinfo=tz).timestamp()]
        with self.db.lock, self.db.db:
            self.db.db.executemany("UPDATE messages SET received_at=? WHERE id=?", [(ts, i+1) for i, ts in enumerate(stamps)])
        result = self.db.activity("2026-10-25")
        self.assertEqual(len(result), 7)
        self.assertEqual(result[-2:], [{"day": "2026-10-24", "count": 1}, {"day": "2026-10-25", "count": 1}])
        self.assertEqual(sum(r["count"] for r in result), 2)


class SummaryLayoutTests(unittest.TestCase):
    def test_plaintext_preserves_references_links_and_fenced_code(self):
        source = "## **主要话题**\r\n- **结论** [消息12、15]\r\nhttps://example.org/a?q=x\r\n```python\r\n# keep\r\na = '**literal**'\r\n```"
        result = summary_plaintext(source)
        self.assertTrue(result.startswith("主要话题\n• 结论 [消息12、15]"))
        self.assertIn("https://example.org/a?q=x", result)
        self.assertIn("# keep\na = '**literal**'", result)

    def test_paragraph_and_line_boundaries_are_lossless(self):
        text = "甲" * 40 + "\n\n" + "乙" * 35 + "\n" + "丙" * 35
        parts = split_summary(text, 60)
        self.assertEqual(parts[0], "甲" * 40 + "\n\n")
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(x) <= 60 for x in parts))

    def test_urls_stay_whole_when_they_fit_a_part(self):
        url = "https://example.org/" + "a" * 30
        text = "资料" * 23 + url + "\n结束"
        parts = split_summary(text, 70)
        self.assertTrue(any(url in p for p in parts))
        self.assertEqual("".join(parts), text)

    def test_long_unbroken_text_and_oversized_url_keep_the_tail(self):
        for text in ("甲乙丙" * 700 + "尾部", "https://example.org/" + "a" * 500):
            parts = split_summary(text, 83)
            self.assertEqual("".join(parts), text)
            self.assertTrue(all(0 < len(p) <= 83 for p in parts))
        with self.assertRaises(ValueError):
            split_summary("a", 0)

    def test_section_heading_does_not_dangle_on_previous_part(self):
        text = "甲" * 70 + "\n【资源链接】\n" + "乙" * 70
        parts = split_summary(text, 82)
        self.assertEqual("".join(parts), text)
        self.assertFalse(parts[0].rstrip().endswith("【资源链接】"))

    def test_header_pagination_and_minimum_limit_cover_cross_day_sources(self):
        job = {"id": 12345, "group_name": "群名" * 100 + "\n注入", "group_id": "9" * 20,
               "result": "【主要话题】\n" + "• 内容。[消息11、12]\n" * 80}
        rows = [record(1), record(2)]
        rows[1]["source_time"] += 86400
        parts = summary_messages(job, rows, 300)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 300 for p in parts))
        self.assertTrue(all("北京时间" in p and "任务#12345" in p for p in parts))
        self.assertIn(f"{len(parts)}/{len(parts)}", parts[-1])
        bodies = [p.split("\n\n",1)[1].rsplit("\n〔任务#",1)[0] for p in parts]
        self.assertEqual("".join(bodies), summary_plaintext(job["result"]))


class SettingsTests(unittest.TestCase):
    def test_keywords_and_scope(self):
        s = Settings.parse({"keywords": ["ABC", "中文"], "match_mode": "all", "exclude_keywords": ["忽略"], "group_mode": "allowlist", "groups": ["A"], "excluded_groups": ["B"]})
        self.assertEqual(s.matches("abc中文"), ["ABC", "中文"])
        self.assertEqual(s.matches("abc中文忽略"), [])
        self.assertEqual(s.matches("abc"), [])
        self.assertTrue(s.monitors("p", "A"))
        self.assertFalse(s.monitors("p", "B"))
        self.assertFalse(s.monitors("p", "C"))

    def test_rejects_invalid_configuration(self):
        for data in [{"summary_threshold": True}, {"summary_threshold": 0}, {"recipients": ["group:123"]}, {"keywords": "bad"}, {"enabled": "false"}, {"unknown": 1}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                Settings.parse(data)


if __name__ == "__main__":
    unittest.main()
