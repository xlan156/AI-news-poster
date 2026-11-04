# 功能台账

状态依据当前代码和现有测试；“已实现”不等于已完成安装包端到端验收。开始新 Phase 前先核对本表、对应代码和测试，再形成 Gap Analysis。功能变更时更新状态、位置与验证证据。

| 功能 | 状态 | 实现位置 | 现有验证 / 待补 |
| --- | --- | --- | --- |
| 官方来源配置、RSS/Atom/网页抓取、来源隔离 | 已实现 | `config.py`、`ingestion.py`、`sources.toml` | `test_sources.py`、`test_pipeline.py`；真实网页结构可能变化 |
| URL 与机构/标题/日期去重、首次来源静默基线 | 已实现 | `storage.py`、`pipeline.py` | `test_pipeline.py`、`test_sources.py` |
| 类别过滤、时间与优先级、单次通知上限、失败重试 | 已实现 | `priorities.py`、`pipeline.py`、`storage.py` | `test_pipeline.py`、`test_sources.py` |
| 可选 LLM 分析、中文摘要、缓存、限额与回退 | 已实现 | `analysis.py`、`llm.py`、`storage.py` | `test_llm.py`；测试模拟外部 API |
| CLI 单次抓取、查看、通知及定时运行 | 已实现 | `__main__.py`、`pipeline.py` | `test_pipeline.py`、`test_llm.py` |
| 桌面六页、搜索筛选、已读、设置保存 | 已实现 | `desktop/src/App.tsx`、`api.ts`、`desktop_service.py` | `test_desktop_service.py`；既有打包窗口检查见 `phase4-desktop.md` |
| 桌面后台定时刷新、手动刷新共用单工作线程 | 已实现 | `desktop_service.py`、`pipeline.py` | `test_desktop_service.py` 覆盖单服务所有权；手动/定时交错的回归场景待补 |
| Windows 托盘、关闭隐藏、单实例与子进程退出 | 已实现，待完整验收 | `desktop/src-tauri/src/main.rs` | 需按 `test-strategy.md` 在打包程序中验收 |
| Windows 启动反馈、service 控制台抑制与连接重试 | 已实现，待完整安装验收 | `desktop/src-tauri/src/main.rs`、`desktop/src/App.tsx` | 0.5.0 NSIS 空数据安装、首次抓取、无独立控制台、关闭退出及旧库恢复通过；独立 Windows 账户仍待验证 |
| Windows 原生通知及原文链接 | 已实现，待打包验收 | `notifications.py`、`pipeline.py` | `test_news.py`、`test_llm.py`；打包后显示与点击待验收 |
| 旧库增量升级、已读状态 | 已实现，升级保障待补 | `storage.py` | `test_sources.py`、`test_desktop_service.py`；升级失败保留原库的验证待补 |
| 开机启动 | 已实现，待登录场景验收 | `desktop/src-tauri/src/main.rs`、`desktop/src/App.tsx` | Rust 与 NSIS 构建通过；实际 Windows 重新登录待验 |
| 每日摘要、日志查看、语言切换 | 未实现 | `desktop_service.py` 中有部分偏好字段 | 不属于 Phase 5 必需交付 |
| Phase 5 三视图、首页配额、推荐排序 | 已实现 | `storage.py`、`desktop_service.py`、`desktop/src/App.tsx` | `test_desktop_service.py`、TypeScript 构建、release 窗口检查 |
| Phase 5 时间元数据、来源状态及升级备份 | 已实现 | `ingestion.py`、`storage.py`、`pipeline.py` | Python 回归集含旧结构迁移及备份；真实用户库副本待验 |
| Google Research、Hugging Face 官方博客、arXiv cs.AI | 已配置为可选来源 | 两份 `sources.toml`、`ingestion.py` | 模拟 RSS/Atom 测试；实时站点健康待验 |
| Phase 5 分组通知、论文及来源偏好 | 已实现，待 Windows 通知验收 | `pipeline.py`、`notifications.py`、`desktop/src/App.tsx` | Python 回归集；打包 toast/点击待验 |

## 下一阶段审计入口

先核对 [architecture.md](architecture.md) 和 [data-contracts.md](data-contracts.md)；逐项说明“已有、可扩展、缺失、冲突”，再写实施计划。下一 Phase 的具体目标以确认后的需求文件为准；需求文件不代表已实现状态。
