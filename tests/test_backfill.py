"""Real SQLite + controlled protocol responses; production QQ is checked separately."""
import asyncio
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from backfill import Backfill, HistoryEmpty, normalize, source_key
from settings import Settings
from storage import Store


def raw(seq, ts, group="g", text="hello"):
    return dict(group_id=group, self_id=100, user_id=200, message_type="group", time=ts,
                real_seq=str(seq), message_id=seq + 10000,
                sender={"nickname": "tester"}, message=[{"type": "text", "data": {"text": text}}])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "real.sqlite3"
        self.store = Store(self.path)
        self.s = Settings.parse({"recipients": ["10001"], "summary_threshold": 2, "keywords": ["hello"]})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def rec(self, seq, ts=100):
        return normalize(raw(seq, ts), "p", "g", "Group")

    def test_native_sequence_dedup_survives_changed_short_id(self):
        a = self.rec(1)
        self.store.ingest(a, self.s)
        self.store.ingest({**a, "message_id": "changed"}, self.s, history=True)
        self.assertEqual(self.store.page("messages")["total"], 1)
        self.assertEqual(self.store.page("jobs")["total"], 1)

    def test_same_short_id_different_native_sequence_is_not_lost(self):
        a, b = self.rec(1), self.rec(2)
        b["message_id"] = a["message_id"]
        self.store.ingest(a, self.s)
        self.store.ingest(b, self.s, history=True)
        self.store.ingest(b, self.s)
        self.assertEqual(self.store.page("messages")["total"], 2)

    def test_legacy_receive_time_differs_from_protocol_time_no_duplicate(self):
        a = self.rec(1, 100)
        self.store.ingest({**a, "source_key": "", "source_time": 101}, self.s)
        self.assertIsNone(self.store.ingest(a, self.s, history=True))
        self.assertEqual(self.store.page("messages")["total"], 1)
        self.assertEqual(self.store.page("messages")["items"][0]["source_time"], 101)

    def test_legacy_row_bound_once_repeated_identical_text_not_collapsed(self):
        a, b = self.rec(1), self.rec(2)
        self.store.ingest({**a, "source_key": "", "message_id": "old"}, self.s)
        self.assertIsNone(self.store.ingest(a, self.s, history=True))
        self.assertIsNotNone(self.store.ingest(b, self.s, history=True))
        self.assertEqual(self.store.page("messages")["total"], 2)

    def test_historical_messages_never_forward_but_count_toward_summary(self):
        self.store.ingest(self.rec(1), self.s, history=True)
        self.store.ingest(self.rec(2), self.s, history=True)
        self.assertEqual(self.store.page("jobs")["total"], 0)
        self.assertEqual(self.store.reserve_batches(self.s), 1)
        self.assertEqual(self.store.page("jobs")["items"][0]["kind"], "summary")
        self.assertEqual(self.store.reserve_batches(self.s), 0)

    def test_summary_history_explicit_preview_scoped_idempotent(self):
        old = Settings.parse({})
        self.store.ingest(self.rec(1), old)
        preview = self.store.summary_history(self.s)
        self.store.ingest(self.rec(2), old)
        self.assertEqual(self.store.summary_history(self.s, preview["through_id"], True)["count"], 1)
        self.assertEqual(self.store.summary_history(self.s, preview["through_id"], True)["count"], 0)
        self.assertEqual(self.store.summary_history(self.s)["count"], 1)
        excluded = Settings.parse({**self.s.values, "excluded_groups": ["g"]})
        self.assertEqual(self.store.summary_history(excluded)["count"], 0)

    def test_history_summary_can_be_disabled(self):
        s = Settings.parse({**self.s.values, "backfill_summary_enabled": False})
        self.store.ingest(self.rec(1), s, history=True)
        self.assertEqual(self.store.summary_history(s)["count"], 1)

    def test_reservation_waits_until_group_scan_completed_and_orders_source_time(self):
        self.store.ingest(self.rec(1, 100), self.s, history=True)
        self.store.sync_window("p", "g", 200, 72)
        self.store.ingest(self.rec(2, 50), self.s, history=True)
        self.assertEqual(self.store.reserve_batches(self.s), 0)
        self.store.sync_page("p", "g", [], self.s, "", True)
        self.assertEqual(self.store.reserve_batches(self.s), 1)
        ids = json.loads(self.store.page("jobs")["items"][0].get("record_ids", "[]"))
        with self.store.lock:
            ids = json.loads(self.store.db.execute("select record_ids from jobs").fetchone()[0])
        self.assertEqual(ids, [2, 1])
        self.assertEqual([r["source_time"] for r in self.store.records(ids)], [50, 100])

    def test_committed_rows_replayed_after_crash_do_not_duplicate(self):
        state = self.store.sync_window("p", "g", 200, 72)
        self.store.ingest(self.rec(1), self.s, history=True)  # crash before advancing cursor
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.sync_window("p", "g", 900, 72)["upper_bound"], state["upper_bound"])
        self.assertEqual(self.store.sync_page("p", "g", [self.rec(1)], self.s, "", True), 0)
        self.assertEqual(self.store.page("messages")["total"], 1)

    def test_window_uses_persisted_checkpoint_not_new_live_message(self):
        self.store.sync_window("p", "g", 200, 72)
        self.store.sync_page("p", "g", [], self.s, "", True)
        self.store.ingest(self.rec(1, 999), self.s)
        self.assertEqual(self.store.sync_window("p", "g", 1000, 72)["lower_bound"], 80)

    def test_max_lookback_exposes_truncation(self):
        self.store.sync_window("p", "g", 100, 1)
        self.store.sync_page("p", "g", [], self.s, "", True)
        state = self.store.sync_window("p", "g", 20000, 1)
        self.assertEqual(state["lower_bound"], 16400)
        self.assertEqual(state["truncated_hours"], 1)

    def test_new_native_column_migrates_legacy_database_without_data_loss(self):
        self.store.ingest({**self.rec(1), "source_key": ""}, self.s)
        with self.store.lock, self.store.db:
            self.store.db.execute("DROP INDEX messages_native_key")
            self.store.db.execute("ALTER TABLE messages DROP COLUMN source_key")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.page("messages")["total"], 1)
        self.assertEqual(self.store.db.execute("pragma quick_check").fetchone()[0], "ok")

    def test_joined_groups_without_messages_are_visible_without_creating_messages(self):
        self.store.remember_groups("p", [{"group_id": "g", "group_name": "Empty Group"}])
        groups = self.store.groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["message_count"], groups[0]["pending_count"], groups[0]["last_time"]), (0, 0, None))
        self.assertTrue(groups[0]["joined"])
        self.assertEqual(self.store.page("messages")["total"], 0)
        self.assertEqual(self.store.page("jobs")["total"], 0)

    def test_directory_merges_by_instance_and_renames_preserving_history(self):
        self.store.ingest(self.rec(1), self.s)
        self.store.remember_groups("p", [{"group_id": "g", "group_name": "Renamed"}])
        self.store.remember_groups("other", [{"group_id": "g", "group_name": "Other"}])
        groups = self.store.groups()
        self.assertEqual(len(groups), 2)
        self.assertEqual(next(g for g in groups if g["platform"] == "p")["group_name"], "Renamed")
        self.assertEqual(self.store.page("messages")["items"][0]["group_name"], "Group")

    def test_directory_departed_group_retains_archive_but_no_empty_ghost(self):
        self.store.ingest(self.rec(1), self.s)
        self.store.remember_groups("p", [{"group_id": "g", "group_name": "Archive"}, {"group_id": "empty"}])
        self.store.remember_groups("p", [])
        groups = self.store.groups()
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["joined"])
        self.assertEqual(groups[0]["message_count"], 1)

    def test_directory_survives_restart_and_malformed_response_does_not_clear(self):
        self.store.remember_groups("p", [{"group_id": "g", "group_name": "Empty"}])
        with self.assertRaises(ValueError):
            self.store.remember_groups("p", [{"group_name": "missing id"}])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.groups()[0]["group_name"], "Empty")
        self.assertTrue(self.store.groups()[0]["joined"])


class BackfillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "messages.sqlite3")
        self.s = Settings.parse({"recipients": ["10001"], "backfill_pages_per_cycle": 1})
        self.now = int(time.time())
        self.store.sync_window("p", "g", self.now - 1000, 72)
        self.store.sync_page("p", "g", [], self.s, "", True)
        self.offline = False
        self.calls = []
        self.pages = [[raw(i, self.now - 1000 + i) for i in range(151, 251)],
                      [raw(i, self.now - 1000 + i) for i in range(51, 152)],
                      [raw(i, self.now - 1000 + i) for i in range(1, 52)]]
        self.page = 0

        async def call(platform, action, params):
            self.calls.append((action, params))
            if action == "get_status":
                return {"online": not self.offline}
            if action == "get_group_list":
                return [{"group_id": "g", "group_name": "Group"}]
            page = self.pages[self.page]
            self.page += 1
            return {"messages": page}
        self.b = Backfill(self.store, lambda: self.s, lambda: ["p"], call, lambda x: None)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_offline_reconnect_paged_recovery_is_durable_and_deduplicated(self):
        self.offline = True
        self.assertEqual((await self.b.run())["inserted"], 0)
        self.assertEqual(self.page, 0)
        self.offline = False
        for _ in range(3):
            await self.b.run()
        self.assertEqual(self.store.page("messages")["total"], 250)
        self.assertEqual(self.store.sync_status()[0]["upper_bound"], 0)
        self.assertEqual(self.calls[-1][1]["reverse_order"], True)
        self.page = 0
        for _ in range(3):
            await self.b.run()
        self.assertEqual(self.store.page("messages")["total"], 250)

    async def test_history_error_does_not_advance_checkpoint_or_expose_credentials(self):
        async def fail(*args):
            raise ConnectionError("secret-token-test")
        self.b.request = fail
        self.assertGreater((await self.b.run())["errors"], 0)
        self.assertNotIn("secret", self.b.health)
        self.assertEqual(self.store.sync_status()[0]["completed_to"], self.now - 1000)

    async def test_expired_cursor_is_reset_and_window_preserved(self):
        await self.b.run()
        upper = self.store.sync_status()[0]["upper_bound"]
        async def empty(*args, **kwargs):
            raise HistoryEmpty()
        self.b.request = empty
        with self.assertRaises(RuntimeError):
            await self.b.sync_group("p", "g", "Group")
        state = self.store.sync_window("p", "g", self.now + 99, 72)
        self.assertEqual(state["cursor"], "")
        self.assertEqual(state["upper_bound"], upper)

    async def test_excluded_group_makes_no_history_request(self):
        self.s = Settings.parse({**self.s.values, "excluded_groups": ["g"]})
        await self.b.run()
        self.assertEqual(self.page, 0)

    async def test_disabling_during_history_request_prevents_late_ingest(self):
        original = self.b.call
        async def disabling(platform, action, params):
            data = await original(platform, action, params)
            if action == "get_group_msg_history":
                self.s = Settings.parse({**self.s.values, "backfill_enabled": False})
            return data
        self.b.call = disabling
        await self.b.run()
        self.assertEqual(self.store.page("messages")["total"], 0)

    async def test_refresh_directory_even_when_recording_and_backfill_disabled(self):
        self.s = Settings.parse({**self.s.values, "enabled": False, "backfill_enabled": False})
        await self.b.refresh_groups()
        self.assertEqual(len(self.store.groups()), 1)
        self.assertEqual(self.page, 0)
        self.assertEqual(self.b.directory_health["p"], "已同步")

    async def test_offline_directory_keeps_last_successful_membership(self):
        await self.b.refresh_groups()
        self.offline = True
        await self.b.refresh_groups()
        self.assertTrue(self.store.groups()[0]["joined"])
        self.assertIn("离线", self.b.directory_health["p"])

    async def test_directory_request_failure_keeps_cache_and_redacts_error(self):
        await self.b.refresh_groups()
        async def fail(*args, **kwargs):
            raise ConnectionError("token-test-secret")
        self.b.call = fail
        await self.b.refresh_groups()
        self.assertTrue(self.store.groups()[0]["joined"])
        self.assertNotIn("secret", self.b.directory_health["p"])


class NormalizationTests(unittest.TestCase):
    def test_signed_media_urls_never_persist_and_self_is_filtered(self):
        message = raw(1, 100)
        message["message"].append({"type": "image", "data": {"url": "https://invalid/?token=secret"}})
        rec = normalize(message, "p", "g", "Group")
        self.assertEqual(rec["outline"], "[Image]")
        self.assertNotIn("secret", json.dumps(rec))
        message["user_id"] = message["self_id"]
        self.assertIsNone(normalize(message, "p", "g", "Group"))

    def test_only_native_sequence_is_used_as_stable_key(self):
        self.assertEqual(source_key({"message_seq": 123, "real_id": 123}), "")
        self.assertEqual(source_key({"real_seq": 45}), "seq:45")


if __name__ == "__main__":
    unittest.main()
