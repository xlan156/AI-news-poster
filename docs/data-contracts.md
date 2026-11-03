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

Phase 5 扩展：文章新增可空 `updated_at`（来源修订时间）和 `fetched_at`（本地首次抓取时间）。旧行的 `fetched_at` 从 `first_seen_at` 补齐，旧库升级前生成一次 `.pre-phase5.bak` 备份。缺少来源日期的条目会记录解析警告并跳过，不会把抓取时间伪装成发布日期。只提供更新时间的非 arXiv Atom/RSS 条目使用该来源时间作排序日期，并同时保留 `updated_at`；arXiv 要求初次发布日期，修订只更新 `updated_at`。来源可配置 `official_authors`；Hugging Face 默认只接受 `huggingface` 署名或组织博客路径。

`source_status` 表以来源 URL 为键保存 `last_attempt`、`last_success`、`state`、`last_error`、`articles_added`。`get_news` 增加 `view`（`official`、`research`、`history`）和 `recommended` 排序。官方视图仅返回当前启用的非 arXiv 来源，论文视图返回 arXiv，历史视图不受停用影响。首页按 `home_source_quota` 轮流展示来源；完整查询无配额。推荐分数为类别权重（模型发布 4、API 更新 3、研究 2、产品更新 1、其他 0）乘 7，再加至多 30 天的新近分；同分按发布时间和 ID 倒序。`newest` 按发布时间倒序，缺失时回退到 `fetched_at`。

配置新增 `home_source_quota`、`default_view`、`default_sort`；通知设置新增 `sources`（空数组表示所有启用来源，`["__none__"]` 表示都不提醒）和 `research`。偏好 `start_at_login` 由 Tauri 同步到当前用户的 Windows Run 项。旧设置和文章保留。

每条请求：`{"id": number, "method": string, "params": object}`；响应：`{"id": number, "result": value}` 或 `{"id": number, "error": string}`。方法为 `get_state`、`get_news`、`refresh`、`set_monitoring`、`set_read`、`save_config`、`save_preferences`、`test_connection`。具体请求/响应由 `desktop_service.py` 定义，前端类型在 `desktop/src/api.ts`；新增字段须两端同步。`refresh` 返回状态，状态中的 `last_result` 包含 `feeds_ok`、`feeds_failed`、`articles_added`、`notified`。`source_status` 从 SQLite 读取并跨进程重启保留。

启动流程仍使用相同的 JSON 行协议。Tauri 不在 setup 中等待 `get_state`；前端首次异步请求期间显示加载状态，连接失败时展示错误并可重试。窗口初始可见性和关闭到托盘偏好由 Tauri 直接读取同一份 `preferences.json`，字段名与 Python 服务保持一致；`--minimized` 启动参数也会隐藏主窗口。

内部通知边界以 `NotificationService.send(NewsItem) -> None` 为基础；Windows 实现还支持多篇新闻分组发送。单条 toast 使用标题、摘要前 180 字和原始 URL 作为点击链接；分组 toast 点击第一篇的原文。当前没有持久化的通用“通知事件”总线或事件表。
