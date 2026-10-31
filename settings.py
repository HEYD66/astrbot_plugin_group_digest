"""Validated settings shared by the WebUI, event handler and durable workers."""

from dataclasses import dataclass, field


DEFAULTS = {
    "enabled": True,
    "silent_mode": True,
    "group_mode": "all",
    "groups": [],
    "excluded_groups": [],
    "platform_ids": [],
    "recipients": [],
    "recipient_platform_id": "",
    "forward_enabled": True,
    "forward_format": "card",
    "forward_context_before": 3,
    "forward_context_after": 3,
    "forward_context_wait": 10,
    "forward_context_minutes": 5,
    "keywords": [],
    "match_mode": "any",
    "exclude_keywords": [],
    "case_sensitive": False,
    "summary_enabled": True,
    "summary_threshold": 100,
    "summary_provider_id": "",
    "summary_instruction": "用中文简明总结主要话题、重要结论、待办和有价值的信息。区分事实与猜测，保留消息编号作为依据。",
    "summary_format": "image",
    "summary_input_chars": 16000,
    "summary_max_calls_per_batch": 16,
    "max_llm_calls_per_day": 100,
    "summary_timeout": 120,
    "delivery_message_chars": 1200,
    "retry_limit": 3,
    "backfill_enabled": True,
    "backfill_interval": 60,
    "backfill_max_hours": 72,
    "backfill_pages_per_cycle": 5,
    "backfill_summary_enabled": True,
    "chat_search_enabled": True,
    "group_rules": {},
}

# 可以按群单独设置的项：转发规则与总结规则。其余（接收人、预算、补录等）只有全局。
GROUP_OVERRIDABLE = ("forward_enabled", "keywords", "match_mode", "exclude_keywords", "case_sensitive",
                     "forward_format", "forward_context_before", "forward_context_after", "forward_context_wait",
                     "forward_context_minutes", "summary_enabled", "summary_format", "summary_threshold",
                     "summary_provider_id", "summary_instruction")


@dataclass
class Settings:
    values: dict = field(default_factory=dict)
    _group_cache: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict):
            raise ValueError("配置必须是对象")
        extra = set(values) - set(DEFAULTS)
        if extra:
            raise ValueError("包含不支持的配置字段")
        v = {**DEFAULTS, **values}
        for key, default in DEFAULTS.items():
            if isinstance(default, bool) and not isinstance(v[key], bool):
                raise ValueError(f"{key} 必须是开关")
            if isinstance(default, list):
                if not isinstance(v[key], list) or not all(isinstance(x, str) for x in v[key]):
                    raise ValueError(f"{key} 必须是字符串列表")
                v[key] = list(dict.fromkeys(x.strip() for x in v[key] if x.strip()))
                if len(v[key]) > 1000 or any(len(x) > 300 for x in v[key]):
                    raise ValueError(f"{key} 内容过长")
            if isinstance(default, str):
                if not isinstance(v[key], str):
                    raise ValueError(f"{key} 必须是文本")
                v[key] = v[key].strip()
                if len(v[key]) > 4000:
                    raise ValueError(f"{key} 内容过长")
        for key, low, high in [
            ("summary_threshold", 2, 10000),
            ("summary_input_chars", 2000, 60000),
            ("summary_max_calls_per_batch", 1, 100),
            ("max_llm_calls_per_day", 1, 10000),
            ("summary_timeout", 10, 600),
            ("delivery_message_chars", 300, 3000),
            ("retry_limit", 1, 10),
            ("backfill_interval", 15, 3600),
            ("backfill_max_hours", 1, 720),
            ("backfill_pages_per_cycle", 1, 50),
            ("forward_context_before", 0, 10),
            ("forward_context_after", 0, 10),
            ("forward_context_wait", 0, 300),
            ("forward_context_minutes", 1, 60),
        ]:
            if type(v[key]) is not int or not low <= v[key] <= high:
                raise ValueError(f"{key} 必须是 {low}～{high} 的整数")
        if v["group_mode"] not in ("all", "allowlist") or v["match_mode"] not in ("any", "all"):
            raise ValueError("群范围或关键词匹配模式无效")
        if v["summary_format"] not in ("image", "text"):
            raise ValueError("总结形式必须为image或text")
        if v["forward_format"] not in ("card", "text"):
            raise ValueError("转发形式必须为card或text")
        if any(not x.isdecimal() or not 5 <= len(x) <= 20 for x in v["recipients"]):
            raise ValueError("接收人应填写QQ号，不要填写群号或会话前缀")
        if ":" in v["recipient_platform_id"]:
            raise ValueError("接收平台只填写机器人实例ID")
        v["group_rules"] = cls._parse_group_rules(v)
        return cls(v)

    @classmethod
    def _parse_group_rules(cls, v):
        """按群号保存的覆盖项。每群按全局规则合并后整体校验；保存的项固定为该群的值，不随全局变化。"""
        rules = v["group_rules"]
        if not isinstance(rules, dict) or len(rules) > 500:
            raise ValueError("单群规则必须是按群号分组的对象")
        base = {k: v[k] for k in DEFAULTS if k != "group_rules"}
        clean = {}
        for gid, override in rules.items():
            if not isinstance(gid, str) or not gid.isdecimal() or not 5 <= len(gid) <= 20:
                raise ValueError("单群规则的群号无效")
            if not isinstance(override, dict) or set(override) - set(GROUP_OVERRIDABLE):
                raise ValueError(f"群 {gid} 的单独规则包含不支持的字段")
            try:
                parsed = cls.parse({**base, **override}).values
            except ValueError as exc:
                raise ValueError(f"群 {gid}：{exc}") from None
            if override:
                clean[gid] = {k: parsed[k] for k in GROUP_OVERRIDABLE if k in override}
        return clean

    def for_group(self, group):
        """该群生效的配置：单群规则覆盖全局同名项，其余跟随全局。"""
        override = self.values["group_rules"].get(str(group))
        if not override:
            return self
        cached = self._group_cache.get(str(group))
        if cached is None:
            cached = self._group_cache[str(group)] = Settings({**self.values, **override})
        return cached

    def __getitem__(self, key):
        return self.values[key]

    def monitors(self, platform, group):
        return (
            self["enabled"]
            and (not self["platform_ids"] or platform in self["platform_ids"])
            and group not in self["excluded_groups"]
            and (self["group_mode"] == "all" or group in self["groups"])
        )

    def targets(self, platform):
        platform = self["recipient_platform_id"] or platform
        return [f"{platform}:FriendMessage:{qq}" for qq in self["recipients"]]

    def matches(self, text):
        norm = (lambda x: x) if self["case_sensitive"] else str.casefold
        haystack = norm(text)
        if any(norm(word) in haystack for word in self["exclude_keywords"]):
            return []
        hits = [word for word in self["keywords"] if norm(word) in haystack]
        if self["match_mode"] == "all" and len(hits) != len(self["keywords"]):
            return []
        return hits
