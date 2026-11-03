# 项目架构（当前实现）

本文件描述当前代码，不把下一阶段的需求当成已实现功能。修改组件职责、跨进程接口或数据流时同步更新。

## 组件与职责

| 组件 | 代码位置 | 职责与边界 |
| --- | --- | --- |
| 来源与抓取 | `src/ai_news_desktop/config.py`、`ingestion.py`、`priorities.py` | 读取并验证来源配置；RSS、Atom、网页适配器抓取并标准化 `NewsItem`；规则分类与通知过滤。网页抓取遵守 robots 检查。 |
| 持久化 | `src/ai_news_desktop/storage.py` | 唯一的文章、首次抓取、通知状态、已读状态、LLM 缓存与用量存储；SQLite 去重和旧库增量补列。数据默认在 `%LOCALAPPDATA%\AI News Desktop`。 |
| 刷新与调度 | `src/ai_news_desktop/pipeline.py`、`desktop_service.py`、`__main__.py` | `refresh()` 是 CLI 和桌面共用的单次抓取链路。CLI 的 `run_periodically()` 与桌面服务各自提供运行入口；桌面服务在同一个工作线程中串行处理手动请求和定时触发，stdin 读取线程只入队。 |
| 分析 | `src/ai_news_desktop/analysis.py`、`llm.py` | 可选云端分析、结果校验、缓存和本地用量限制；关闭或失败时保留规则与原始摘要。 |
| 通知 | `src/ai_news_desktop/notifications.py` | `NotificationService.send(NewsItem)` 边界及 Windows toast 实现；只由 Python 刷新链路调用。提交成功后写入 `notified`，不代表用户实际看见或点击。 |
| 桌面界面 | `desktop/src/App.tsx`、`api.ts`、`desktop/src-tauri/src/main.rs` | React 展示和操作；Tauri 管理窗口、托盘、单实例和 Python 子进程。前端经 Tauri 命令发送请求，不直接访问 SQLite、抓取来源或发送新闻通知。 |

## 数据流与进程边界

```text
React 页面 → Tauri backend_command → 单个 Python desktop_service → refresh()
                                                     ↓
来源配置 → 抓取/解析 → NewsItem → SQLite 去重/状态 → 规则及可选 LLM → Windows toast
                                                     ↓
React 页面 ← Tauri ← 按 id 对应的 JSON 响应 ← 查询/状态/设置
```

Tauri 通过子进程 stdin/stdout 传输每行一条 JSON 消息；日志走 stderr，不开放本地 HTTP 端口。桌面服务拥有 SQLite 连接，手动抓取和定时抓取使用同一个 `refresh()`。单个长抓取会延后后续请求的响应。CLI 是另一种运行入口；若与桌面程序同时运行，当前没有跨进程调度锁，下一阶段涉及并发抓取时要专门审计。

Windows release 启动 Python 子进程时使用 `CREATE_NO_WINDOW`，保留 stdin/stdout 管道。Tauri setup 只创建子进程、读取本地窗口偏好并设置托盘；首个 `get_state` 由 React 在窗口显示后异步请求。子进程启动失败时保留空状态，前端显示错误并允许重试；进程退出后下一次命令可重新启动单个服务。

## 演进原则

先审计并扩展现有 `FeedFetcher`、`Store`、`refresh()`、`DesktopService` 与 `NotificationService`。只有职责或现有接口确实无法覆盖需求时才增加服务、抽象或依赖。新增依赖、数据库结构变更、公共接口变更和较大重构，应在实施计划中逐项写明理由、兼容性与回退风险。Windows 打包与既有验证记录见 [phase4-desktop.md](phase4-desktop.md)。
