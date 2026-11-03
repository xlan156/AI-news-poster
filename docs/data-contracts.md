# 数据契约（当前实现）

以 `models.py`、`config.py`、`storage.py`、`desktop_service.py` 和 `desktop/src/api.ts` 为准。改变字段、含义或兼容性时同步修改本文件和相应测试。

## 文章与状态

`NewsItem`：`id`、`title`、`summary`、`source`、`url` 为非空字符串；`url` 必须是无空白及凭据的 HTTP(S) URL；`published_at` 必须带时区；`organization` 可空，展示时回退到 `source`；`category` 为下列五种之一。入库后 URL 字段名为 `canonical_url`，时间写为 UTC ISO 8601；另存 `first_seen_at`、`status`、`notified_at`。已读时间 `read_at` 来自独立的 `article_read` 表，不改变通知状态。桌面查询可附加缓存的 `chinese_summary`、`importance_score`。

类别：`research`、`model_release`、`api_update`、`product_update`、`company_news`。来源类别是默认值，`priorities.classify()` 可按标题规则改写。旧 arXiv 文章在桌面查询时显示为 `research`。

文章状态：`baseline`（该来源首次成功抓取时入库，不通知）、`pending`（后续新文章）、`notified`（通知接口提交成功）。失败发送保持 `pending`。`canonical_url` 唯一；另以机构、规范化标题、UTC 发布日的散列去重。首次抓取标记按来源 URL 保存在 `initialized_feeds`。

## 来源与设置

TOML 的 `[[sources]]` 必填 `name`、`organization`、`type`、`url`、`enabled`、`category`；`type` 为 `rss`、`atom`、`webpage`。网页来源还需 `card_tag`、`card_attribute` 或 `card_classes`，以及 `link_prefix`。来源 URL 必须唯一。旧 JSON 的 `feeds` 仍可由 `load_config()` 读取。

`[settings]`：`refresh_minutes`（默认 60）、`max_notifications_per_cycle`（3）、`max_article_age_hours`（48）、`notifications_enabled`（true）。`[notifications]`：`categories`、`exclude_keywords`。`[llm]` 使用 `LLMConfig` 的模式、服务商、模型、密钥引用、请求限制、估算价格与缓存设置；`[news_filter]` 使用相关性/重要性阈值、语言与标题翻译设置。完整字段和校验范围见 `config.py`。桌面设置保存在用户数据目录的 `desktop.json`，窗口与安静时段偏好保存在 `preferences.json`；API 只返回 `has_api_key`，不返回密钥引用。

## 桌面 IPC 与通知

每条请求：`{"id": number, "method": string, "params": object}`；响应：`{"id": number, "result": value}` 或 `{"id": number, "error": string}`。方法为 `get_state`、`get_news`、`refresh`、`set_monitoring`、`set_read`、`save_config`、`save_preferences`、`test_connection`。具体请求/响应由 `desktop_service.py` 定义，前端类型在 `desktop/src/api.ts`；新增字段须两端同步。`refresh` 返回状态，状态中的 `last_result` 包含 `feeds_ok`、`feeds_failed`、`articles_added`、`notified`。`source_status` 记录本进程内的来源尝试结果，重启后不保留。

内部通知边界只有 `NotificationService.send(NewsItem) -> None`。Windows toast 使用标题、摘要前 180 字和原始 URL 作为点击链接。当前没有持久化的通用“通知事件”总线或事件表；若未来需要每日摘要或通知历史，应先明确事件语义和迁移方式，再扩展现有边界。
