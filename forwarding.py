"""Native OneBot forward cards; original protocol content stays out of the DB."""


class OriginalMessageUnavailable(Exception):
    pass


class ForwardCardRejected(Exception):
    pass


def same_message(raw, record):
    if not isinstance(raw, dict) or raw.get("message_type") != "group":
        return False
    if str(raw.get("group_id")) != record["group_id"] or str(raw.get("user_id")) != record["sender_id"]:
        return False
    key = record.get("source_key", "")
    if key:
        return key == "seq:" + str(raw.get("real_seq", ""))
    # Legacy rows used receive time; the same short ID + sender + close time
    # prevents a reused short ID from forwarding a different person's content.
    return (str(raw.get("message_id")) == record["message_id"]
            and abs(float(raw.get("time", 0)) - record["source_time"]) <= 5)


async def resolve_originals(call, records):
    resolved = {}
    for record in records:
        try:
            raw = await call("get_msg", {"message_id": record["message_id"]})
            if same_message(raw, record):
                resolved[record["id"]] = str(raw["message_id"])
        except Exception:
            pass  # A short-ID cache may have expired after a reconnect.
    missing = [r for r in records if r["id"] not in resolved]
    if missing:
        oldest_needed = min(r["source_time"] for r in missing) - 5
        cursor = None
        for _ in range(10):
            params = dict(group_id=records[0]["group_id"], count=100, reverse_order=True,
                          disable_get_url=True, parse_mult_msg=False)
            if cursor:
                params["message_seq"] = cursor
            result = await call("get_group_msg_history", params)
            messages = result.get("messages", [])
            for raw in messages:
                for record in missing:
                    if same_message(raw, record):
                        resolved[record["id"]] = str(raw["message_id"])
            missing = [r for r in missing if r["id"] not in resolved]
            if not missing:
                break
            if not messages or min(float(m["time"]) for m in messages) < oldest_needed or len(messages) < 100:
                break
            next_cursor = str(min(messages, key=lambda m: (float(m["time"]), int(m.get("real_seq", 0))))["message_id"])
            if next_cursor == cursor:
                break
            cursor = next_cursor
    if len(resolved) != len(records):
        raise OriginalMessageUnavailable()
    return resolved


async def send_card(source_call, target_call, recipient, records, metadata):
    if not records or len({(r["platform"], r["group_id"], r["sender_id"]) for r in records}) != 1:
        raise ValueError("Card context must have one source group and sender")
    resolved = await resolve_originals(source_call, records)
    bot = await target_call("get_login_info", {})
    label = "卡片核验" if metadata.get("validation") else "命中转发"
    note = (f"【{label} #{metadata['job_id']}】\n来源群：{records[0]['group_name']} ({records[0]['group_id']})\n"
            f"原发言者：{records[0]['sender_name']} ({records[0]['sender_id']})\n"
            f"命中：{'、'.join(metadata['hits'])}\n"
            f"命中消息在下方第 {', '.join(str(i+1) for i,r in enumerate(records) if r['id'] in metadata['matched_ids'])} 条，"
            f"其余为同群同人上下文，共 {len(records)} 条。\n"
            "以下节点引用 QQ 原消息，保留可用的原文与媒体。")
    nodes = [{"type": "node", "data": {"user_id": str(bot["user_id"]),
              "nickname": str(bot.get("nickname") or "群聊情报站"),
              "content": [{"type": "text", "data": {"text": note}}]}}]
    nodes.extend({"type": "node", "data": {"id": resolved[r["id"]]}} for r in records)
    # No client-side reconstruction with fabricated sender identities. Original
    # reference IDs are validated above; cross-account sending is not supported.
    response = await target_call("send_private_forward_msg", {"user_id": recipient, "messages": nodes})
    if not isinstance(response, dict) or not response.get("message_id"):
        raise ForwardCardRejected()
    return {"message_id": str(response["message_id"])}
