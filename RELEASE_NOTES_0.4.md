# NoNocode 0.4.0

本版完成执行可靠性修补和内置联网工具，保留 SQLite 会话、任务记录与 Markdown 记忆。

## 执行可靠性

- Bash 按退出码和命令语义区分成功、失败；`grep` 无匹配、`diff` 有差异、`test` 条件为假仍是可解释的正常结果。普通命令失败不会再显示成功。
- Linux/macOS 上，Bash 与命令 Hook 在独立进程组运行；取消或超时会终止同组子进程并回收管道。Windows 提供直接子进程终止，尚未验证完整进程树终止。已自行脱离进程组的守护进程不在此保证内。
- 流式工具任务在模型断流、取消、超时、生成器关闭时统一取消并等待清理；保留工具 ID，避免异常结果无法对应原调用。写入和命令按顺序执行，显式支持并发的只读工具才会提前启动。
- `ReadFile`、`Glob`、`Grep` 在可终止的独立进程中执行，单次最长 30 秒，避免网络文件系统或正则搜索阻塞界面。ReadFile 最多读取 4 MB 文本。
- 输出被截断时，已完成的工具不会自动重放；持续截断或执行限额会成为失败记录。任务遇到错误后不会仅因收到结束事件就变成成功。
- 网页重连会同步当前会话、最近输出、工具状态和仍在等待的权限请求。重连不批准权限、不重启任务。快照保留最近 50 条文本消息及最多 1 MB / 3000 条当前执行事件；超出时提示到任务中心查看持久化记录。服务重启后的未完成任务仍标为 interrupted，等待用户恢复会话。

在 `.nonocode/config.local.yaml` 或主配置中添加：

```yaml
run_limits:
  max_seconds: 900
  max_iterations: 50
  max_tool_calls: 100
  max_tokens: 200000
  max_repeated_calls: 4
```

每项可独立调整，显式 `0` 关闭该限制。上限按每次用户执行/子代理调用重新计算；子代理继承配置，但不是整个代理团队共用一个总预算。`max_tokens` 包含该调用主循环返回的输入、缓存及输出用量，在 API 返回用量后检查；不是预付费金额硬上限，不包括独立后台记忆整理的模型用量。等待权限也计入墙钟时间。已经提交给外部服务的请求、已经完成的文件修改无法通过取消撤销。

## 联网工具

默认启用，无需安装新依赖：

- `WebSearch(query, max_results=5)`：返回标题、来源 URL、摘要、搜索服务和 UTC 抓取时间。
- `WebFetch(url, max_chars=20000)`：返回最终 URL、标题、正文、UTC 抓取时间和截断标记。支持 HTML、纯文本、JSON/XML，不运行 JavaScript，不处理登录、PDF 或图片。

直接在终端或网页对话中说：

```text
搜索 Python asyncio 的官方文档，读取相关原文，再用中文解释取消机制，并附来源链接。
读取 https://www.python.org/，总结主要内容并附原网页链接。
```

默认搜索使用 Bing 公开 RSS，不需要额外密钥。它是尽力提供的公开入口，不是有可用性承诺的付费 API；服务可能限流，结果质量、地区和排序可能变化。验证码、非法响应、超时会返回明确错误，不会被包装成检索成功。需要独立搜索 API 时可切换到 [Brave Search API](https://api-dashboard.search.brave.com/api-reference/web/search/get)：

```yaml
web:
  enabled: true
  search_provider: brave
  search_api_key_env: BRAVE_SEARCH_API_KEY
```

通过环境变量提供 `BRAVE_SEARCH_API_KEY`。设置 `web.enabled: false` 会从内置工具注册表移除这两个工具；此开关不限制 Bash 或另行配置的 MCP 网络能力。

请求仅允许公网 HTTP/HTTPS 的 80/443 端口。每跳重定向重新检查 URL 与 DNS，连接固定到已检查的 IP，同时保持原域名的 Host 与 TLS 验证；不会继承环境代理。总请求最长 25 秒，最多 5 次重定向，解压后正文最多 2 MB。解析到私网、回环、链路本地地址的请求会拒绝。环境必须允许直接访问目标公网；本版未提供代理支持。

搜索摘要与网页正文作为不可信资料处理。系统提示词要求核对原文、引用实际返回的来源链接，不得把网页内容当成修改权限、执行命令或写入记忆的指令。网页本身陈述的真实性仍需核对。

`WebFetch(https://example.com/*)` 和 `WebSearch(*)` 可使用现有权限规则设为 ask/deny；规划模式默认允许只读联网。

## 完整数据备份与隔离恢复

原 `/storage backup path.db` 仍只备份 SQLite。新增：

```bash
nonocode --backup /absolute/path/nonocode-backup.zip
nonocode --restore-backup /absolute/path/nonocode-backup.zip --restore-to /absolute/path/new-restore
```

交互模式：`/storage backup-full /absolute/path/backup.zip`；网页“备份”按钮改为生成同类 ZIP。创建备份前请结束当前执行；跨进程使用时也应退出其他实例，避免文件与数据库在不同时刻发生变动。

备份包含当前项目的数据库记录、项目/用户记忆、进化账本、会话导出、权限配置、项目指令和脱敏配置，带 SHA-256 文件清单。配置内明确的密钥/口令字段清空；会话文本仍保留用户原始内容。包为本机私有文件（0600）。不包含项目源代码、Git 仓库、技能安装文件、日志、外部生成产物或环境变量中的密钥。

恢复仅接受不存在的新目录，先检查路径、文件清单、校验和、SQLite 完整性和关联，再生成：

```text
new-restore/
├── project/         # 恢复的工作区配置、记忆和会话
├── user/.nonocode/  # 隔离的用户记忆和配置
├── database/        # 重映射到新工作区的 SQLite
└── RESTORE.txt      # 启动说明
```

恢复不会自动运行模型或工具，不覆盖原项目、原用户目录或原数据库。未结束的任务恢复为 interrupted。重新提供 API 密钥后：

```bash
cd /absolute/path/new-restore/project
HOME=/absolute/path/new-restore/user \
NONOCODE_DATA_DIR=/absolute/path/new-restore/database nonocode
```

需要继续操作原项目文件时，另行复制所需源码到恢复工作区。旧对话内的绝对路径作为原始记录保留，不能视为新工作区的路径。

## 升级

退出旧实例后，在项目目录执行 `python -m pip install -e .`，再重新启动。原配置无需迁移，新增字段使用上述默认值。`nonocode --version` 应显示 0.4.0；`nonocode --doctor` 可查看当前限额和联网开关，不显示密钥。

## 本次验证（2026-10-28）

- 自动回归：779 passed、1 skipped、0 failed。跳过项需要独立的在线记忆整理测试密钥。
- 真实系统调用：子进程树取消/超时、隔离文件操作、HTTP/WebSocket 对话与取消、断线后的权限恢复。
- 真实公网：免密钥搜索返回 3 条来源，成功读取 Python 官网及 asyncio 官方文档。
- 浏览器脚本：jsdom 验证产品面板、HTML 清理、重连和停止后的界面状态；未执行完整浏览器自动化。
- 发行：构建 wheel 并从独立解包目录验证三个文件工具与联网工具注册。
- Brave API 使用模拟响应验证协议、鉴权和跨域密钥隔离；未提供独立 Brave 密钥，未做该付费 API 的真实调用。本次未调用真实聊天模型。
