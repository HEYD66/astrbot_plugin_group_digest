"""v1.8.0 群文件：接口返回整理、文件段只存文件名和大小。临时真实 SQLite，不涉及 QQ。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from backfill import attachments, normalize
from group_files import FILE_LIMIT, error_reason, file_url, listing, usage, with_name
from settings import Settings
from storage import Store


class GroupFileTests(unittest.TestCase):
    def test_listing_sorts_cleans_and_skips_invalid(self):
        raw = {"folders": [{"folder_id": "/b", "folder_name": "资料\n第二", "total_file_count": "3"}, {"folder_name": "无编号"}],
               "files": [{"file_id": "/1", "file_name": "旧.pdf", "size": "1024", "upload_time": 100, "busid": 102},
                         {"file_id": "/2", "file_name": "新.zip", "file_size": 2048, "upload_time": 200},
                         {"file_name": "缺编号"}, "坏数据"]}
        out = listing({"data": raw})
        self.assertEqual([f["name"] for f in out["folders"]], ["资料 第二"])
        self.assertEqual(out["folders"][0]["count"], 3)
        self.assertEqual([f["name"] for f in out["files"]], ["新.zip", "旧.pdf"])  # 新的在前
        self.assertEqual(out["files"][1]["size"], 1024)
        self.assertEqual(out["files"][0]["size"], 2048)
        self.assertFalse(out["truncated"])
        with self.assertRaises(ValueError):
            listing([1, 2])

    def test_listing_caps_and_marks_truncated(self):
        files = [{"file_id": str(i), "file_name": f"{i}.txt", "upload_time": i} for i in range(FILE_LIMIT + 5)]
        out = listing({"files": files})
        self.assertEqual(len(out["files"]), FILE_LIMIT)
        self.assertTrue(out["truncated"])

    def test_usage_and_url_validation(self):
        self.assertEqual(usage({"file_count": 5, "limit_count": 10000, "used_space": 9, "total_space": 99})["file_count"], 5)
        self.assertIsNone(usage(None))
        self.assertEqual(file_url({"url": " https://ftn.qq.com/x?a=1 "}), "https://ftn.qq.com/x?a=1")
        self.assertEqual(file_url({"data": {"url": "javascript:alert(1)"}}), "")
        self.assertEqual(file_url(None), "")

    def test_with_name_adds_fname(self):
        self.assertEqual(with_name("https://ftn.qq.com/ftn_handler/abc/", "测试 a.txt"),
                         "https://ftn.qq.com/ftn_handler/abc/?fname=%E6%B5%8B%E8%AF%95%20a.txt")
        self.assertEqual(with_name("https://ftn.qq.com/x?a=1", "a&b=c.zip"), "https://ftn.qq.com/x?a=1&fname=a%26b%3Dc.zip")
        self.assertEqual(with_name("https://ftn.qq.com/x?fname=old.txt", "new.txt"), "https://ftn.qq.com/x?fname=old.txt")
        # NapCat 实际返回的链接末尾是空的 fname=
        self.assertEqual(with_name("https://ftn.qq.com/ftn_handler/abc/?fname=", "a.zip"), "https://ftn.qq.com/ftn_handler/abc/?fname=a.zip")
        self.assertEqual(with_name("https://ftn.qq.com/x?fname=&k=1", "a.zip"), "https://ftn.qq.com/x?k=1&fname=a.zip")
        self.assertEqual(with_name("https://ftn.qq.com/x?sig=a+b%2Fc&fname=", "a.zip"), "https://ftn.qq.com/x?sig=a+b%2Fc&fname=a.zip")
        self.assertEqual(with_name("https://ftn.qq.com/x", "../dir/\x01evil.txt"), "https://ftn.qq.com/x?fname=..direvil.txt")
        self.assertEqual(with_name("https://ftn.qq.com/x", ""), "https://ftn.qq.com/x")
        self.assertEqual(with_name("", "a.txt"), "")

    def test_error_reason_hides_links(self):
        class ActionFailed(Exception):
            def __init__(self):
                super().__init__("failed")
                self.result = {"wording": "文件不存在 https://ftn.qq.com/secret?sign=abc"}
        self.assertEqual(error_reason(ActionFailed()), "文件不存在 [链接]")
        self.assertEqual(error_reason(RuntimeError("")), "RuntimeError")

    def test_attachments_keep_name_and_size_only(self):
        segs = [{"type": "text", "data": {"text": "看这个"}},
                {"type": "file", "data": {"file": "部署.pdf", "file_size": "4096", "url": "https://secret/sign"}},
                {"type": "file", "data": {}}]
        saved = json.loads(attachments(segs))
        self.assertEqual(saved, [{"name": "部署.pdf", "size": 4096}])
        self.assertEqual(attachments([{"type": "text"}]), "")
        self.assertEqual(attachments("bad"), "")

    def test_backfill_normalize_and_ingest_store_attachments(self):
        raw = {"message_type": "group", "group_id": 1, "user_id": 2, "self_id": 3, "message_id": 9, "time": 100,
               "real_seq": "5", "sender": {"nickname": "甲"}, "message": [{"type": "file", "data": {"file": "a.zip", "file_size": 10}}]}
        rec = normalize(raw, "p", "1", "群")
        self.assertEqual(json.loads(rec["attachments"]), [{"name": "a.zip", "size": 10}])
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.sqlite3")
            try:
                store.ingest(rec, Settings.parse({}))
                item = store.timeline(group_id="1")["items"][0]
                self.assertEqual(json.loads(item["attachments"]), [{"name": "a.zip", "size": 10}])
                self.assertIn("attachments", store.context(item["id"])["items"][0])
                # 没有文件的旧式记录也能入库
                store.ingest(dict(platform="p", group_id="1", group_name="群", message_id="10", sender_id="2",
                                  sender_name="甲", source_time=101, text="普通消息", outline=""), Settings.parse({}))
                self.assertEqual(store.page("messages")["total"], 2)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
