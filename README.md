# 群聊情报站

AstrBot 插件，支持群消息记录、关键词转发和 AI 总结。

**适用环境**：AstrBot 4.29.1 + OneBot v11（NapCat）

## 功能

- **群消息记录**：持久化保存群聊消息到 SQLite，支持全文搜索、按时间/群/发送者筛选
- **关键词转发**：监控关键词命中，收集上下文后以 QQ 原生合并转发卡片发送给指定接收人
- **自动总结**：按群分批凑够条数后自动调用 AI 生成总结，分段投递给接收人
- **自动补录**：断线重连后自动检查缺口并补录历史消息
- **独立控制台**：在「扩展功能 → 群聊情报站 → 控制台」管理配置、查看记录、重试任务

## 安装

1. 从 [Releases](../../releases) 下载最新版本的 zip 包
2. 在 AstrBot 管理界面上传并启用插件
3. 打开「扩展功能 → 群聊情报站 → 控制台」进行配置

## 使用

### 基本配置

1. **接收人**：在「插件设置 → 接收与投递」填写接收 QQ 号（一行一个）
2. **监控范围**：在「群管理」选择监控所有群或指定群，可配置排除群
3. **关键词规则**：在「关键词规则」添加关键词和排除词，支持任一/全部匹配模式
4. **总结设置**：在「总结任务 → 总结设置」配置总结模型和每群消息条数阈值

### 工作流程

- 监控群收到新消息后自动记录
- 命中关键词时收集同人上下文（前后各 0-10 条可配置），通过合并转发卡片发送
- 每个群凑够 N 条消息自动创建总结任务，调用 AI 生成并分段发送

**初始配置**：监控所有群、静默模式开启、每群 100 条总结、接收人和关键词为空

> ⚠️ 静默模式会阻止监控群内其他插件和默认聊天回复；可关闭或排除特定群

## 数据存储

- **插件源码**：`/opt/astrbot/data/plugins/astrbot_plugin_group_digest`
- **数据库**：`/opt/astrbot/data/plugin_data/astrbot_plugin_group_digest/messages.sqlite3`
- **配置备份**：插件数据目录下的 `config_backups/`
- **导出文件**：`exports/` 目录（JSONL 格式）

### 备份数据库

使用 SQLite backup API 备份运行中的数据库：

```python
import sqlite3
with sqlite3.connect('/opt/astrbot/data/plugin_data/astrbot_plugin_group_digest/messages.sqlite3') as source:
    with sqlite3.connect('/备份目录/messages.sqlite3') as target:
        source.backup(target)
```

## 开发

### 运行测试

```bash
python -m unittest discover -s tests -v
```

### 打包

```bash
python build_schema.py  # 生成配置 schema
python build.py         # 打包 zip
```

### 项目结构

- `main.py` - 插件主入口
- `storage.py` - 数据库操作
- `forwarding.py` - 关键词转发
- `engine.py` - AI 总结引擎
- `backfill.py` - 自动补录
- `group_files.py` - 群文件接口
- `views/dashboard/` - Web 控制台前端
- `tests/` - 单元测试

## 许可证

MIT License

## 相关链接

- [AstrBot 官方文档](https://docs.astrbot.app/)
- [插件开发指南](https://docs.astrbot.app/dev/star/guides/plugin-pages.html)
