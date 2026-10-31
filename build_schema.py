"""Generate the committed schema from the same defaults used by the workers."""
import json
from pathlib import Path
from settings import DEFAULTS

LABELS = {
    "enabled": ("启用记录与任务", "关闭后暂停新记录及任务处理，历史数据保留。"),
    "silent_mode": ("监控群静默模式", "开启会阻止监控群后续插件及默认聊天回复，也包括其他插件的群指令；私聊不受影响。"),
    "group_mode": ("监控范围", "all：所有收到消息的QQ群；allowlist：仅下方指定群。"),
    "groups": ("指定监控群", "一行一个群号，仅 allowlist 模式使用。"),
    "excluded_groups": ("排除群", "一行一个群号，优先于所有群/指定群。"),
    "platform_ids": ("来源机器人实例ID", "留空为所有 OneBot v11 实例；填写机器人页面的实例ID，一行一个。"),
    "recipients": ("接收QQ", "一行一个QQ号，以私聊接收。未配置时只记录，不产生转发或总结任务。"),
    "recipient_platform_id": ("发送机器人实例ID", "留空使用消息来源实例；跨实例时填机器人页面实例ID。"),
    "forward_enabled": ("启用关键词转发", "命中后私聊给接收人；卡片模式可附带同群同人上下文，不向来源群回复。"),
    "forward_format": ("转发形式", "card：QQ合并转发聊天记录卡片，引用原消息；text：普通文字通知。原消息无法获取时卡片任务保留为失败，不伪造或静默改成文字。"),
    "forward_context_before": ("命中前同人消息条数", "0～10，只取同群同一人的前文，受下方前后文时间范围限制。"),
    "forward_context_after": ("命中后同人消息条数", "0～10，卡片模式等待后文；收够后提前发送，未收够也会到时发送。"),
    "forward_context_wait": ("等待后文秒数", "0～300，默认10秒；0表示不等待，只发送已收到的上下文。仅卡片模式使用。"),
    "forward_context_minutes": ("前后文最大时间范围（分钟）", "1～60，默认5分钟；不把相隔很久的同一人消息混在同张卡片。"),
    "keywords": ("关键词", "一行一个，普通文本子串匹配，不使用正则。留空不触发转发。"),
    "match_mode": ("匹配模式", "any：包含任一关键词；all：必须包含全部关键词。"),
    "exclude_keywords": ("排除关键词", "包含任意排除词则不转发，一行一个。"),
    "case_sensitive": ("区分大小写", "默认不区分英文字母大小写。"),
    "summary_enabled": ("启用自动AI总结", "仅在接收人已配置时收到的消息计数；每群独立，不补发未配置阶段的旧消息。"),
    "summary_threshold": ("每群总结条数", "2～10000；每凑齐这一数量即创建一批，剩余消息等待下一批。"),
    "summary_provider_id": ("总结模型", "留空跟随来源群当前聊天模型；只选择文本生成模型。"),
    "summary_instruction": ("总结要求", "自定义关注主题、摘要结构和长度；不会调用插件工具或共享聊天历史。"),
    "summary_format": ("总结发送形式", "image：排版成图片发送，链接另发一条文字，出图失败自动改发文字；text：纯文字分段发送。"),
    "summary_input_chars": ("单段资料字符上限", "2000～60000。长批次分段整理后汇总；应小于模型上下文限制。"),
    "summary_max_calls_per_batch": ("每批AI调用上限", "1～100，包含失败尝试。达到后任务失败并保留原始资料，可调高后手动重试。"),
    "max_llm_calls_per_day": ("每日AI调用上限", "按北京时间日历日，包含失败尝试；达到后暂停总结，次日继续。"),
    "summary_timeout": ("单次AI超时（秒）", "10～600；失败按重试次数处理。"),
    "delivery_message_chars": ("每条私聊字符上限", "300～3000，长内容拆为多条并附任务号。"),
    "retry_limit": ("自动失败次数上限", "1～10，指数退避，达到后在任务页手动重试。发送失败不会重新生成已有摘要。"),
    "backfill_enabled": ("断线自动补录", "使用现有OneBot连接检查群历史；重连或插件重启后按持久化进度补录，重复消息不重复入库。"),
    "backfill_interval": ("补录检查间隔（秒）", "15～3600；定期核对可覆盖短暂断连，离线时等待恢复。默认60秒。"),
    "backfill_max_hours": ("最大回溯小时数", "1～720；超出此范围的缺口会显示受限状态。仅能获取QQ历史接口返回的消息。"),
    "backfill_pages_per_cycle": ("每群每轮最大页数", "1～50，每页最多100条；进度保存在数据库，下轮继续，避免长时间占用。"),
    "backfill_summary_enabled": ("补录消息计入总结", "补回的新消息在接收人已配置时计入每群总结；重复记录不重复计数。补录不会触发关键词转发。"),
    "group_rules": ("单群规则", "按群号保存的转发/总结规则覆盖项，在控制台群工作区的「设置」标签里编辑。"),
    "chat_search_enabled": ("允许私聊大肥鱼查群记录", "开启后，接收QQ在私聊里问大肥鱼群里聊过什么时，它可以检索群聊原文作答；群聊和其他人调用一律拒绝。每次提问会消耗聊天模型调用。"),
}

schema = {}
for key, value in DEFAULTS.items():
    kind = "bool" if isinstance(value, bool) else "int" if isinstance(value, int) else "list" if isinstance(value, list) else "string"
    label, hint = LABELS[key]
    schema[key] = {"description": label, "hint": hint, "type": kind, "default": value}
schema["group_mode"]["options"] = ["all", "allowlist"]
schema["match_mode"]["options"] = ["any", "all"]
schema["forward_format"]["options"] = ["card", "text"]
schema["summary_format"]["options"] = ["image", "text"]
schema["summary_provider_id"]["_special"] = "select_provider"
# dict 类型：AstrBot 校验配置时保留用户添加的群号键；invisible：不在 AstrBot 自带的插件配置页显示
schema["group_rules"].update(type="dict", invisible=True)
Path(__file__).with_name("_conf_schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
