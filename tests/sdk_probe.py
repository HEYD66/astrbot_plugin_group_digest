"""Run inside the real AstrBot image, with real SDK events and isolated storage.

These constructed events are NOT messages received from QQ.
"""
import asyncio
import importlib
import sys
import tempfile
from pathlib import Path

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, Group, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata

sys.path.insert(0, str(Path(__file__).parents[2]))
module = importlib.import_module("astrbot_plugin_group_digest.main")


async def check():
    with tempfile.TemporaryDirectory() as tmp:
        instance = module.GroupDigest.__new__(module.GroupDigest)
        instance.config = {}
        instance.store = module.Store(Path(tmp) / "isolated.sqlite3")
        instance.ingest_error = ""

        def event(n, post_type="message", platform="aiocqhttp", sender="test-user"):
            obj = AstrBotMessage()
            obj.type = MessageType.GROUP_MESSAGE
            obj.self_id = "test-self"
            obj.group = Group(group_id="test-group", group_name="SDK测试群")
            obj.sender = MessageMember(user_id=sender, nickname="SDK测试发言者")
            obj.message_id = str(n)
            obj.message_str = "SDK测试资料"
            obj.message = [Plain("SDK测试资料")]
            obj.raw_message = {"post_type": post_type, "real_seq": str(n)}
            meta = PlatformMetadata(platform, "isolated SDK test", "test-instance")
            return AstrMessageEvent(obj.message_str, obj, meta, "test-group")

        incoming = event(1)
        incoming.is_at_or_wake_command = True
        await instance.capture(incoming)
        assert not instance.ingest_error, instance.ingest_error
        assert incoming.is_stopped() and not incoming.is_at_or_wake_command
        await instance.capture(event(1))
        await instance.capture(event(2, post_type="notice"))
        await instance.capture(event(3, platform="webchat"))
        await instance.capture(event(4, sender="test-self"))
        rows = instance.store.page("messages")["items"]
        assert len(rows) == 1 and rows[0]["group_name"] == "SDK测试群"
        assert rows[0]["source_key"] == "seq:1"
        instance.config = {"silent_mode": False}
        normal = event(5)
        await instance.capture(normal)
        assert not normal.is_stopped()
        instance.store.close()
        print("SDK_EVENT_PROBE_OK: capture, dedup, notice/self/platform filtering, group name, silence")


if __name__ == "__main__":
    asyncio.run(check())
