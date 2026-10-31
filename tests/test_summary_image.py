"""总结出图：排版与投递计划。cairosvg 只在容器内有，这里用替身渲染函数测试引擎流程。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import summary_image as si  # noqa: E402
from engine import Engine  # noqa: E402
from settings import Settings  # noqa: E402
from storage import Store  # noqa: E402

SAMPLE = """【速览】本批讨论模型接入和路由器刷机 😄。

【主要话题】
1. 新版模型接入
• 响应更快，但长上下文偶尔截断 [消息12、15]
• 平台价格对比 [消息21]
2. 路由器刷机
• 刷机前先备份 ART 分区 [消息40]

【结论与待办】
• 小蓝鲸整理配置模板 [消息58]

【资源链接】
OpenWrt 固件 https://downloads.openwrt.org/releases/
示例文档 https://docs.example.org/guide
"""


def rows(n=3):
    return [dict(id=i, source_time=1700000000 + i * 60, sender_name="甲", text="x", outline="") for i in range(1, n + 1)]


class LayoutTests(unittest.TestCase):
    def test_parse_sections_topics_and_bullets(self):
        secs = si.parse(SAMPLE)
        self.assertEqual([s["title"] for s in secs], ["速览", "主要话题", "结论与待办", "资源链接"])
        kinds = [k for k, _ in secs[1]["items"]]
        self.assertEqual(kinds.count("topic"), 2)
        self.assertEqual(kinds.count("bullet"), 3)

    def test_emoji_removed_and_svg_escaped(self):
        job = {"id": 7, "group_name": "<群&名>", "result": SAMPLE + "\n• 注入 <script>alert(1)</script>", "created_at": 1700000300}
        svgs, _ = si.build_svgs(job, rows())
        joined = "".join(svgs)
        self.assertNotIn("😄", joined)
        self.assertNotIn("<script>", joined)
        self.assertIn("&lt;群&amp;名&gt;", joined)

    def test_links_sent_separately_in_full(self):
        _, links = si.build_svgs({"id": 1, "group_name": "g", "result": SAMPLE}, rows())
        self.assertEqual(len(links), 2)
        self.assertIn("https://downloads.openwrt.org/releases/", links[0])
        # 正文里散落的链接也会补进去，且不重复
        _, links = si.build_svgs({"id": 1, "group_name": "g", "result": SAMPLE + "\n• 另见 https://a.example/x"}, rows())
        self.assertEqual(len(links), 3)

    def test_unstructured_text_still_renders(self):
        svgs, links = si.build_svgs({"id": 2, "group_name": "g", "result": "就是一段没有区块的总结。"}, rows())
        self.assertEqual(len(svgs), 1)
        self.assertEqual(links, [])

    def test_empty_result_rejected(self):
        with self.assertRaises(ValueError):
            si.build_svgs({"id": 3, "group_name": "g", "result": "   "}, rows())

    def test_long_summary_paginates_with_bounded_height(self):
        long = "【主要话题】\n" + "".join(f"{i}. 话题{i}\n• " + "很长的要点内容" * 12 + f" [消息{i}]\n" for i in range(1, 60))
        svgs, _ = si.build_svgs({"id": 4, "group_name": "g", "result": long}, rows())
        self.assertGreater(len(svgs), 1)
        for s in svgs:
            height = int(s.split('height="', 1)[1].split('"', 1)[0])
            self.assertLess(height, si.PAGE_MAX + 600)
        self.assertIn(f"第 {len(svgs)}/{len(svgs)} 页", svgs[-1])

    def test_ref_chip_never_alone_on_line(self):
        width = si.INNER - 76
        text = "多" * 40 + " [消息21]"
        lines = si.wrap(si.tokens(text, width=width), width)
        for line in lines:
            if any(k == "ref" for _, k, _ in line):
                self.assertTrue(any(k != "ref" for _, k, _ in line))


class EngineImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / "m.sqlite3")
        self.s = Settings.parse({"recipients": ["10001"], "summary_threshold": 2, "forward_format": "text", "retry_limit": 1})
        self.texts, self.images, self.renders = [], [], 0

        async def generate(*_):
            return SAMPLE

        async def send(target, text):
            self.texts.append(text)
            return True

        async def send_image(target, png):
            self.images.append(png)
            return True

        def render(job, rs):
            self.renders += 1
            if getattr(self, "broken", False):
                raise RuntimeError("cairo missing")
            svgs, links = si.build_svgs(job, rs)
            return {"pages": [f"PNG{i}".encode() for i in range(len(svgs))], "links": links}

        self.engine = Engine(self.db, lambda: self.s, generate, send, lambda _: None, None, render, send_image)
        for i in range(2):
            self.db.ingest(dict(platform="p", group_id="g", group_name="测试群", message_id=str(i), sender_id="u",
                                sender_name="甲", source_time=1700000000 + i, text="hi", outline=""), self.s)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    async def run_all(self):
        await self.engine.step("generate")
        while await self.engine.step("deliver"):
            pass

    async def test_image_then_link_text(self):
        await self.run_all()
        self.assertEqual(self.images, [b"PNG0"])
        self.assertEqual(len(self.texts), 1)
        self.assertIn("资源链接", self.texts[0])
        self.assertIn("https://docs.example.org/guide", self.texts[0])
        job = self.db.page("jobs")["items"][0]
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["generation_calls"], 1)

    async def test_render_failure_falls_back_to_text(self):
        self.broken = True
        await self.run_all()
        self.assertEqual(self.images, [])
        self.assertTrue(any("群聊简报" in t for t in self.texts))
        self.assertEqual(self.db.page("jobs")["items"][0]["status"], "done")

    async def test_text_format_setting_skips_render(self):
        self.s = Settings.parse({**self.s.values, "summary_format": "text"})
        await self.run_all()
        self.assertEqual(self.renders, 0)
        self.assertEqual(self.images, [])

    async def test_reload_redraws_from_saved_result_without_ai(self):
        await self.engine.step("generate")
        job = self.db.claim("deliver", self.s)
        plan = await self.engine.text_plan(job, self.db.records(json.loads(job["record_ids"])))
        self.db.update(job["id"], status="ready", delivery_plan=json.dumps(plan, ensure_ascii=False))
        self.engine.images.clear()  # 模拟插件重载丢失内存缓存
        while await self.engine.step("deliver"):
            pass
        self.assertEqual(self.images, [b"PNG0"])
        self.assertEqual(self.renders, 2)
        self.assertEqual(self.db.page("jobs")["items"][0]["generation_calls"], 1)


if __name__ == "__main__":
    unittest.main()
