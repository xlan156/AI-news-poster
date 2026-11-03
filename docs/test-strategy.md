# 测试与 Phase 验收策略

按 [AGENTS.md](../AGENTS.md) 选择最小有效反馈环；以下是每个 Phase 的回归范围和发布门槛。每次验收记录命令、环境、结果与未验证项，开发窗口结果不能代替打包验收。

| 层级 | 重点 | 当前入口 |
| --- | --- | --- |
| 单元 | URL/配置校验、解析、分类过滤、去重键、排序、LLM 输出校验与限额 | `tests/test_news.py`、`test_sources.py`、`test_llm.py` |
| 集成 | 来源抓取→SQLite→刷新→通知；首次基线、重启、失败重试、旧库升级、桌面 IPC 和设置持久化 | `tests/test_pipeline.py`、`test_sources.py`、`test_desktop_service.py`、`test_llm.py`；网络和付费 API 用桩替代，数据库用临时目录 |
| Windows 端到端 | 安装与升级、首次启动、后台定时、托盘、单实例、通知展示/点击、退出与子进程清理、显示缩放 | 按 `phase4-desktop.md` 在 Windows 11 实际运行对应打包产物并记录结果 |

## 固定回归集

Phase 验收时至少覆盖：来源解析与失败隔离；相同文章跨来源/重启去重；首次静默和后续通知；新旧文章时间过滤及优先级；通知上限与发送失败重试；禁用通知/LLM 时的回退；缓存、额度与配置校验；旧库升级；桌面搜索/已读/设置和 JSON 行 IPC。当前自动回归入口：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Set-Location desktop
pnpm build
```

`pnpm build` 包含 TypeScript 检查。前端交互变更可追加有意义的组件测试与开发窗口检查；打包或 Windows 行为变更按 [phase4-desktop.md](phase4-desktop.md) 构建并运行程序。CI 的 `windows-desktop.yml` 已运行 Python 测试并产出 NSIS 安装包，但产出安装包不等于自动完成 Windows 端到端验收。

Windows 启动行为变更还应在 release EXE 中记录冷/热启动到可见加载提示及首页内容的时间，确认后台 service 无控制台窗口、连接失败可重试、最小化启动不显示主窗口，并在正常退出时检查子进程清理。`AI_NEWS_STARTUP_TRACE=1` 可在桌面进程 stderr 输出不含用户数据的启动时间点；不要将 stdout 用作诊断日志，以免干扰 IPC。

## 下一阶段需补的回归风险

Phase 5 回归入口：`python -m pytest -q --basetemp .pytest-phase5`（测试临时库放工作区，避免受限临时目录）、`desktop/node_modules/.bin/tsc -b`、`desktop/node_modules/.bin/vite build`、`cargo check --locked`。手动与定时请求由同一个服务工作线程串行；来源状态、官方/论文/历史视图、配额、旧库升级和来源作者筛选由模拟数据测试。Windows 开机启动仍需真实登录后检查 Run 项、隐藏窗口与单实例；原生 toast 的出现和点击需要打包应用验收。

- 手动刷新与定时触发交错时，仅产生一个有效抓取/通知结果；桌面单线程串行处理是现状，还应验证结果与去重。若 CLI 与桌面同时运行，需另测跨进程访问。
- 数据库升级：用上一版本真实结构的副本测试保留文章、通知及已读数据；模拟迁移失败并核对原始数据仍可读取，再决定是否需要备份或事务调整。
- 打包程序：验证隐藏窗口时的原生通知及原文链接、托盘菜单、单实例、退出无残留、干净 Windows 账户上的安装与启动。

## 每个 Phase 的五步门槛

1. **Requirements**：独立写目标、可观察验收标准、范围外事项；需求不直接充当编码指令。
2. **Audit & Gap Analysis**：核对本台账、当前代码、测试、未提交改动；列出已有、复用、缺失与冲突。实施计划据此生成。
3. **Incremental Implementation**：小任务逐项实现、针对性验证和提交。引入依赖、数据库迁移、公共接口变更或较大重构时单列原因与风险。
4. **Regression & Release Validation**：运行受影响检查与既有回归集；发布候选或打包相关变更运行实际 Windows 产物，记录未覆盖项。
5. **Documentation & Baseline**：同步四份项目文档与构建说明；验收通过后才合并、创建版本标签。开始阶段前记录当前提交并建立阶段分支；可用 `phase-N-baseline` 和 `v0.N.0` 作为命名约定，避免覆盖已有标签。数据库升级需单独验证备份与失败恢复，Git 回退不回退用户数据库。
