# NoNocode 0.3.0

本版围绕会话存储、任务中心、历史搜索和记忆管理展开。

## 可以直接使用的功能

- SQLite 持久化会话、消息、主执行任务和工具步骤，按工作目录隔离。
- 首次打开项目时导入旧版 `.nonocode/sessions/*.jsonl` 和 `.meta`；同一会话不重复导入，保留原文件。
- 历史支持关键词与中文短词搜索。FTS5 可用时使用全文索引，不可用时降级为子串搜索。
- `/runs` 查看主执行记录、工具状态、错误及 Token；`/runs resume <id>` 恢复关联会话，等待用户决定下一步，不自动重放工具。
- 网页新增“历史、任务中心、记忆”面板，支持恢复、重命名、导出对话，查看执行步骤，取消当前实例的任务，编辑或删除记忆。
- 记忆正文继续以 Markdown 为准。数据库中的记忆索引可随时重建，外部编辑文件后，下次搜索或打开记忆面板会同步索引。
- `--history`、`--runs`、`--doctor` 不需要模型密钥，也不发送模型请求。
- `/storage backup <新文件路径>` 或网页“备份数据”使用 SQLite backup API 创建一致性数据库备份。
- 默认网页监听 `127.0.0.1:18888`，拒绝跨来源 WebSocket 请求；对话 Markdown 经过 HTML 白名单处理。
- 执行中允许查历史和任务，但阻止切换会话等会破坏当前执行状态的管理操作。

## 启动

```bash
python -m pip install -e .
nonocode --version
nonocode --doctor
nonocode
# 或打开本机浏览器界面
nonocode --remote
```

## 数据位置

默认数据库：`~/.local/share/nonocode/nonocode.db`。项目目录位于共享文件系统时，数据库仍保留在本机用户目录。可以设置 `NONOCODE_DATA_DIR=/本机磁盘/目录` 改变位置；不要让多台机器共享读写同一个数据库文件。

程序按 SQLite 版本选择日志模式：包含 WAL-reset 修复的版本使用 WAL，其他版本使用回滚日志 DELETE。当前 llm3.11 环境的 SQLite 3.51.0 自动选择 DELETE，无需修改系统库。依据 [SQLite 官方 WAL-reset 说明](https://www.sqlite.org/wal.html#walreset)，修复版本包括 3.51.3+、3.50.7 和 3.44.6。`nonocode --doctor` 会显示实际日志模式。

数据库保存当前用户各项目的数据，每次查询按解析后的工作目录隔离。工作目录移动后会识别为另一个项目；如同时保留旧导出文件，新位置可再次导入这些文件。

项目配置、记忆、技能、进化账本及文件结果继续位于 `.nonocode/`。自动记忆通过现有提取链路写入 Markdown，未改为由模型直接写 SQL。数据库和项目文件都可能包含私人内容，应放在自己的账户目录。

数据库是迁移后会话和任务的权威来源。JSONL 和 `.meta` 继续作为兼容导出；编辑已有导出不会覆盖数据库。需要下载最新会话时使用网页“导出”。

## 升级与恢复

升级前结束旧实例中的任务，退出旧版，再安装并启动新版。不要让旧版与新版同时写同一项目：旧版在导入完成后追加的记录不会自动合并。

任务状态包括 `running`、`waiting_permission`、`completed`、`failed`、`cancelled`、`interrupted`。重新打开任务中心时，属于已退出本机进程的未结束任务会标记为 `interrupted`，可以恢复会话后手动继续。不会恢复任意 Python 调用栈，也不会重新执行已经发生的写入。

后台子 Agent 的管理继续使用 `/tasks`；`/runs` 记录的是终端、网页或 `-p` 发起的主执行。本版没有新增定时任务、在线搜索服务或模型配置 GUI。

`/storage backup` 只备份 SQLite：记忆正文、配置、进化账本、附件和输出文件需要另外备份。恢复数据库时先退出所有实例，保留当前数据库和 `-wal`、`-shm` 文件，再将完整备份放入数据目录后启动。不要向运行中的数据库文件覆盖备份。

## 新命令

```text
/history 中文关键词
/runs
/runs show <完整任务ID>
/runs cancel <完整任务ID>
/runs resume <完整任务ID>
/storage
/storage backup /本机磁盘/备份/nonocode.db
/memory search 中文
/memory show project/preferred-language
/memory add project/preferred-language 请使用中文
/memory update project/preferred-language 先给结论，再解释
/memory forget project/preferred-language
```

```bash
nonocode --history "以前的任务关键词"
nonocode --runs
nonocode --doctor
```

记忆范围为 `project` 或 `user`；用户级记忆跨项目使用。直接输入管理命令不会经过模型决定是否执行。

## 开发验证

Python 回归：`python -m pytest tests -q`。真实在线记忆整理测试需要独立的测试 API 配置，默认跳过。

网页 DOM 测试可额外安装 `jsdom@26`，从 `nonocode.web_content.INDEX_HTML` 导出 HTML 后运行 `node tests/web_product.cjs <HTML路径>`。该依赖仅用于开发验证，运行 NoNocode 不需要 Node.js。
