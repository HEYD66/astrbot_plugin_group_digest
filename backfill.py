"""Bounded, durable reconciliation over the existing authenticated OneBot client."""
import asyncio
import itertools
import json
import time


class HistoryEmpty(Exception):
    pass


def source_key(raw):
    # NapCat's real_seq is the native per-group sequence. message_seq/real_id
    # are short IDs in v4.18.41 and must NOT be used as native sequence keys.
    seq = str(raw.get("real_seq", ""))
    return "seq:" + seq if seq.isdecimal() and int(seq) > 0 else ""


def attachments(segments):
    """消息里的文件段 → JSON 字符串，只保留文件名和大小；带签名的下载地址不保存。"""
    out = []
    for seg in segments if isinstance(segments, list) else []:
        if not isinstance(seg, dict) or seg.get("type") != "file":
            continue
        data = seg.get("data") or {}
        name = str(data.get("name") or data.get("file_name") or data.get("file") or "").replace("\n", " ").strip()[:200]
        try:
            size = max(0, int(float(data.get("file_size") or 0)))
        except (TypeError, ValueError):
            size = 0
        if name:
            out.append({"name": name, "size": size})
    return json.dumps(out[:10], ensure_ascii=False) if out else ""


def normalize(raw, platform, group, name):
    if raw.get("message_type") != "group" or str(raw.get("group_id")) != str(group):
        return None
    if str(raw.get("user_id")) == str(raw.get("self_id")):
        return None
    segments = raw.get("message")
    if not isinstance(segments, list):
        raise ValueError("Unsupported history message format")
    text, outline = [], []
    kinds = {"at": "At", "face": "Face", "image": "Image", "record": "Record",
             "video": "Video", "file": "File", "reply": "Reply", "forward": "Nodes",
             "json": "Json", "xml": "Xml", "dice": "Dice", "rps": "RPS"}
    for typ, batch in itertools.groupby(segments, key=lambda x: x.get("type", "")):
        batch = list(batch)
        if typ == "text":
            text.append("".join(str(x.get("data", {}).get("text", "")) for x in batch).strip())
        elif typ == "markdown":
            text.extend(str(x.get("data", {}).get("markdown") or x.get("data", {}).get("content", "")) for x in batch)
        elif typ == "mface":
            continue  # Matches the installed AstrBot adapter.
        else:
            for item in batch:
                outline.append("[" + kinds.get(typ, typ.title() or "Unknown") + "]")
                if typ == "at":
                    qq = str(item.get("data", {}).get("qq", ""))
                    if qq not in (str(raw.get("self_id")), "all"):
                        text.append(" @" + qq + " ")
    sender = raw.get("sender") or {}
    return dict(platform=platform, group_id=str(group), group_name=str(raw.get("group_name") or name),
                message_id=str(raw["message_id"]), sender_id=str(raw["user_id"]),
                sender_name=str(sender.get("card") or sender.get("nickname") or "N/A"),
                source_time=float(raw["time"]), text="".join(text).strip(), outline=" ".join(outline),
                source_key=source_key(raw), attachments=attachments(segments))


class Backfill:
    def __init__(self, store, settings, platforms, call, log):
        self.store, self.settings, self.platforms, self.call, self.log = store, settings, platforms, call, log
        self.task = None
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.directory_health = {}
        self.directory_lock = asyncio.Lock()
        self.health = "未启动"

    def start(self):
        self.store.reset_sync_cursors()
        self.task = asyncio.create_task(self.loop(), name="group-digest-backfill")

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def loop(self):
        while True:
            self.wake.clear()
            try:
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.health = "补录异常：" + type(exc).__name__
                self.log(self.health)
            try:
                await asyncio.wait_for(self.wake.wait(), self.settings()["backfill_interval"])
            except asyncio.TimeoutError:
                pass

    async def request(self, platform, action, **params):
        return await asyncio.wait_for(self.call(platform, action, params), 35)

    async def discover(self, platform):
        groups = await self.request(platform, "get_group_list")
        await asyncio.to_thread(self.store.remember_groups, platform, groups)
        self.directory_health[platform] = "已同步"
        return groups

    async def refresh_groups(self):
        # Dashboard discovery is independent of monitoring/recording switches.
        # Users must be able to see and configure a new group before it speaks.
        async with self.directory_lock:
            for platform in self.platforms():
                try:
                    status = await self.request(platform, "get_status")
                    if not status.get("online", False):
                        self.directory_health[platform] = "QQ离线，显示上次群列表"
                        continue
                    await self.discover(platform)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.directory_health[platform] = "群列表同步失败：" + type(exc).__name__

    async def run(self):
        async with self.lock:
            self.health = "正在检查历史消息"
            s = self.settings()
            if not s["enabled"] or not s["backfill_enabled"]:
                self.health = "已暂停"
                return {"inserted": 0, "paused": True}
            inserted, failures, online = 0, 0, 0
            for platform in self.platforms():
                if s["platform_ids"] and platform not in s["platform_ids"]:
                    continue
                try:
                    status = await self.request(platform, "get_status")
                    if not status.get("online", False):
                        continue
                    online += 1
                    groups = await self.discover(platform)
                    for group in groups:
                        gid = str(group["group_id"])
                        if not self.settings().monitors(platform, gid):
                            continue
                        try:
                            inserted += await self.sync_group(platform, gid, str(group.get("group_name") or gid))
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            failures += 1
                            await asyncio.to_thread(self.store.sync_error, platform, gid, type(exc).__name__)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures += 1  # Connection/auth details must not enter dashboard logs.
            self.health = ("等待QQ连接" if not online else
                           f"部分补录待重试（{failures}）" if failures else "运行中")
            return {"inserted": inserted, "errors": failures, "online_platforms": online}

    async def sync_group(self, platform, group, name):
        s = self.settings()
        state = await asyncio.to_thread(self.store.sync_window, platform, group,
                                       time.time(), s["backfill_max_hours"])
        total = 0
        for _ in range(s["backfill_pages_per_cycle"]):
            if not self.settings().monitors(platform, group) or not self.settings()["backfill_enabled"]:
                return total
            params = dict(group_id=group, count=100, disable_get_url=True,
                          parse_mult_msg=False, reverse_order=True)
            if state["cursor"]:
                params["message_seq"] = state["cursor"]
            try:
                result = await self.request(platform, "get_group_msg_history", **params)
                messages = result["messages"]
            except HistoryEmpty:
                if state["cursor"]:
                    await asyncio.to_thread(self.store.reset_sync_cursors, platform, group)
                    raise RuntimeError("HistoryCursorExpired") from None
                messages = []
            if not isinstance(messages, list):
                raise ValueError("Invalid history response")
            if not self.settings().monitors(platform, group) or not self.settings()["backfill_enabled"]:
                return total
            records = []
            for raw in messages:
                if state["lower_bound"] <= float(raw["time"]) <= state["upper_bound"]:
                    rec = normalize(raw, platform, group, name)
                    if rec:
                        records.append(rec)
            oldest = min((float(m["time"]) for m in messages), default=0)
            done = not messages or oldest <= state["lower_bound"] or len(messages) < 100
            cursor = "" if done else str(min(messages, key=lambda m: (float(m["time"]), int(m.get("real_seq", 0))))["message_id"])
            if not done and cursor == state["cursor"]:
                raise RuntimeError("HistoryCursorStalled")
            total += await asyncio.to_thread(self.store.sync_page, platform, group, records,
                                             self.settings(), cursor, done)
            if done:
                break
            state["cursor"] = cursor
            await asyncio.sleep(0.25)
        return total
