# Phase 5 实施与验收记录

## 基线与差距

起点为 `743e5a5`，工作区原本干净；在 `codex/phase5` 分支开发。`phase5.md` 定义目标与验收条件，现有实现与四份项目文档是代码审计依据。

- 已有：SQLite 文章去重和静默基线、手动及定时共用刷新链路、Windows toast、托盘与单实例、旧库补列、AI 三种模式。
- 可扩展：`Store` 增加时间和来源状态；`FeedFetcher` 保持 RSS/Atom 通用解析；`get_news` 添加本地视图查询；现有 React 设置页承载新偏好。
- 缺失：官方默认视图、配额与推荐规则、修订/抓取时间、持久来源健康、Google Research/Hugging Face/arXiv 可选来源、开机启动、通知来源及论文开关。
- 风险：SQLite 迁移影响用户库，升级前增加一次备份；`get_news` 和配置字段变更须两端同步；Windows Run 项属于用户级系统状态，需在打包程序中验收。无新运行依赖。

## 当前实现

新增来源默认关闭，用户可独立启用。停用来源不会删除历史记录。Hugging Face 按官方署名/组织路径筛选。RSS/Atom 与网页结构异常会报错，来源失败彼此隔离。来源状态持久保存在 SQLite。首页每来源配额和轮流展示只影响展示，历史查询不受限。通知在入库后发送，可分组；研究论文和来源通知独立控制。Tauri 使用当前用户 Run 项开机启动，启动时隐藏窗口。

## 验证与剩余项

- Python：`50 passed`，另有 `15 subtests passed`；使用工作区临时测试目录、模拟网络与 API。
- 前端：TypeScript 检查与 Vite 生产构建通过。
- Windows：`cargo check --locked --offline` 通过；开发窗口启动后 WebView 对本地 Vite 地址显示 `ERR_NETWORK_ACCESS_DENIED`。随后打开 release 可执行文件，实际检查首页、三种新闻视图、来源列表、通知页及设置页。现有用户库中官方视图 1461 篇、论文视图 407 篇、历史视图 2859 篇；没有触发网络抓取。重复启动后仍只有一个窗口；关闭主窗口后进程保持运行且窗口隐藏，符合托盘行为。
- 打包：重建 PyInstaller sidecar 并通过独立 IPC 冒烟测试；Tauri release 和 NSIS 安装包构建成功，产物见 `desktop/src-tauri/target/release/bundle/nsis/AI News Desktop_0.4.0_x64-setup.exe`。安装、登录后开机启动、托盘菜单退出、通知出现和点击、休眠恢复及不同缩放比例仍需要实际 Windows 验收；尚不能据此创建版本标签或宣称 Phase 完成。

## 启动 Hotfix 合并后回归（2026-11-03）

已将 CI workspace 修复和 Windows 启动 Hotfix 合入 Phase 5 分支；冲突解决保留 `--minimized` 开机启动、设置失败回滚、无控制台 service 与异步首页加载。Python 回归为 50 passed、15 subtests passed，TypeScript、Vite 生产构建及 `cargo check --locked --offline` 通过。重建 Phase 5 的 PyInstaller service 后，Tauri release EXE 编译成功；本地 NSIS 阶段因无法解析 NSIS 下载主机而停止，需以该提交的 Windows CI 安装包构建结果为准。

合并后的 release EXE 在本机显示首页和新闻视图，已有用户库中官方动态 1477 篇、研究论文 407 篇、全部历史 2875 篇，后台 service 无独立控制台窗口。未在本轮重新验证全新账户安装、登录后开机启动、托盘菜单退出、原生通知出现与点击、休眠恢复及不同缩放比例。

## 0.5.0 发布候选验证（2026-11-04）

发布前将 Python、Tauri、Rust、前端及界面显示的版本统一为 `0.5.0`。在原 Windows 11 账户中备份并移开原有约 4.7 MB 用户数据，安装由 GitHub Actions 提交 `83278c7` 生成的 0.4.0 安装包作为首次安装基线。空库首次启动、默认官方视图、7 个预置来源（4 个启用、3 个关闭）、设置页及偏好持久化通过；窗口退出后没有遗留 service 进程。卸载后程序文件和卸载记录移除，SQLite 与偏好文件的 SHA-256 保持一致。

0.5.0 安装包由 Windows CI 提交 `5bf8457` 生成，后端测试、Python service 打包、桌面构建、NSIS 和 artifact 上传全部通过，下载 ZIP 的 SHA-256 与 GitHub 摘要一致。移开原数据后的首次安装和启动成功，前端版本显示 0.5.0，后台 service 无独立控制台；首次手动抓取从 4 个默认启用来源写入 1480 篇，发送 0 条通知，官方动态有内容。再次抓取新增 0 篇，库总数保持 1480。关闭主窗口后应用留在后台，重新启动 EXE 恢复同一实例；关闭托盘模式后退出，无残留桌面或 service 进程。测试库已另存，原数据逐文件恢复并核对 SHA-256；重新启动 0.5.0 后显示原有 2875 篇与 13 篇待关注。当前机器保留 0.5.0 安装及原用户数据。

本轮在有开发工具的同一 Windows 账户上进行，不能代替全新机器验收。真实 Windows 重新登录后的开机启动、托盘菜单“退出”、新文章原生通知显示与点击、休眠恢复、不同显示缩放和升级失败恢复仍未验收；因此 0.5.0 仅作为发布候选，不标记为 Phase 5 完整验收通过。
