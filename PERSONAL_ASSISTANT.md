# NoNocode 个人助手：记忆、反思与改进

NoNocode 可以处理编程、写作、研究、规划和文件整理任务，并通过长期记忆适应你的工作方式。“自进化”流程是：日常对话 → 提取记忆和改进候选 → 跨会话积累反馈证据 → 为能力候选执行对照验证 → 你审核采纳 → 后续任务使用 → 继续收集反馈 → 随时撤回。普通偏好按用户意愿采纳；能力候选必须先满足固定任务的执行检查。改进作用于记忆和工作流程规则，不训练模型权重，也不自主修改核心程序。

使用模型 API 时，需要 Python 3.11 或更新版本。首次安装：

```bash
cd /owenbhe/buddy1/lqszchen/learn_claude/harness/NoNocode
python -m pip install -e .
```

安装后可使用 `nonocode` 命令启动，也可运行 `python -m nonocode`。项目指令文件为 `NONOCODE.md`。

项目配置位于 `.nonocode/config.yaml`。使用本项目已实测的 DeepSeek Anthropic 兼容接口：

```yaml
providers:
  - name: deepseek
    protocol: anthropic
    base_url: https://api.deepseek.com/anthropic
    model: deepseek-flash
    api_key: ""
    thinking: false
    max_output_tokens: 4096

permission_mode: default
mcp_servers: []
```

`api_key` 留空时，`anthropic` 协议从环境变量 `ANTHROPIC_API_KEY` 读取密钥。在 Bash 终端中输入（密钥不会回显，也不直接写入命令历史）：

```bash
read -r -s -p "ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY
printf '\n'
export ANTHROPIC_API_KEY
python -m nonocode
```

新终端需要重新设置环境变量。模型名和接口地址由 YAML 中的 `model`、`base_url` 决定；目前不会自动读取 `MODEL_ID`、`ANTHROPIC_BASE_URL` 或 `.env` 文件。不要把 `api_key` 写成 `${ANTHROPIC_API_KEY}`，provider 配置目前不替换这种占位符。

只运行一次提示词，可在同一个终端执行：

```bash
python -m nonocode -p "请只回复 OK"
```

其他程序需要逐行 JSON 事件时，可加 `--output-format stream-json`。这是命令行调用方式；`--remote` 提供的是浏览器界面和 WebSocket 服务。

配置按 `~/.nonocode/config.yaml`、当前工作目录的 `.nonocode/config.yaml`、`.nonocode/config.local.yaml` 的顺序合并，后面的 provider 列表覆盖前面的列表。保持在项目根目录启动即可使用这里的项目配置和进化记录。

你可以直接说“以后请先给结论，再解释原因”。每轮正常回答完成后，记忆提取会尝试从明确的用户纠正、长期偏好和用户报告的可复用做法中提炼至多三条改进候选，保存用户原话作为证据。没有匹配到本轮新增用户原话的候选不会保存；注入的系统提醒和环境信息不作为证据。提炼依赖所用模型，不能保证每次反馈都会被正确识别，因此也提供手动记录命令。

之后继续正常聊天即可积累反馈。例如第二天说“这次开头先给结论很清楚，继续保持”，模型会尝试关联既有候选，而非重复创建；又如“虽然检查了行数，这次导出还是丢了一行”，可以作为对应做法的失败反馈。记录包含适用场景，以及 `preference`（偏好）、`correction`（纠正）、`success`（成功反馈）、`failure`（失败反馈）四类信号。每个候选保留最近 20 条不同原话，重复的同一句话不增加证据数。抽取时会提供最多 40 条、共不超过 16,000 字符的既有候选供模型关联；已采纳候选优先，然后按更新时间排列。关联仍可能出现遗漏或误判。

自动候选保持 `pending`，反馈次数不会触发自动采纳；已采纳规则和适用场景也不会因新反馈被自动改写。相反的偏好或修订过的做法应生成新候选，采纳新规则时可以撤回旧规则。拒绝或撤回后，相同规则、相同证据或明确关联到该 ID 的反馈不会重新启用它。普通的“谢谢”、用户沉默、助手自称成功不能证明改进有效；成功／失败计数表示保存的用户报告，不是独立测试结果。

候选分为 `preference`（语言、篇幅、表达等偏好）和 `capability`（任务步骤、方法或效果主张）。抽取器会尝试区分两者，仍可能误判；旧记录兼容为偏好。已有规则可以用 `/evolve promote <完整ID>` 升级为能力候选，包括已采纳的规则。升级后规则进入待验证状态，暂不使用，验证通过并重新采纳后生效。

在交互界面使用：

1. `/evolve feedback 回答时先给结论，再解释原因`：记录一条待审核的改进候选。
2. `/evolve status`：查看待审核、已采纳等状态，以及保存的证据和成功／失败反馈数量。
3. `/evolve list`：查看自动提炼及手动录入的候选、完整 ID 和证据数；`/evolve show <完整ID>` 查看来源、适用场景、反馈记录和状态。
4. `/evolve apply <完整ID>`：采纳候选，之后的对话将按相关性和适用场景使用该项目偏好。也可以用 `/evolve reject <完整ID>` 拒绝。
5. `/evolve revoke <完整ID>`：撤销已采纳的偏好；后续对话不再注入该偏好。撤销不会删除已有聊天内容或模型已收到的上下文。

能力改进的完整操作示例（在项目根目录的交互界面中执行）：

```text
/evolve capability 汇总带符号金额时，将负数与正数一起计入总和，避免过滤负数。
/evolve task examples/evolution/signed-ledger.task.json
/evolve task examples/evolution/positive-ledger.task.json
/evolve tasks
/evolve verify <候选完整ID> signed-ledger positive-ledger
/evolve report <候选完整ID>
/evolve apply <候选完整ID>
/evolve revoke <候选完整ID>
```

`task` 冻结任务提示词、输入文件和检查条件，之后修改原 CSV 不会改变已登记的输入。需要更新验证集时重新登记任务；旧报告会在采纳检查中失效。每个任务 2–3 轮，每轮分别执行当前规则和候选规则，并交替执行顺序；每次执行使用新的临时工作区。可一次指定 1–3 个任务，并把旧能力的回归任务一并加入。上述两个任务默认共执行 8 次任务，每次任务可能包含多轮模型调用；`verify` 会真实调用当前配置的模型服务，记录 token 用量，日常聊天不会自动触发这些试跑。

检查由固定的 Python 评估代码读取产物完成，期望答案和报告不提供给试跑助手。支持 `file_exists`、`text_contains`（非空子串）、`json_equals`（精确值，可用 `/total` 这样的 JSON Pointer）和 `csv_rows`（数据行数，不含表头）。至少要有一个内容检查，不能仅凭文件存在认定提升。任务 JSON 格式可参考 `examples/evolution/`；输入限于项目内显式列出的普通文件，单文件最多 1 MB、总量最多 8 MB。每次试跑默认最多 8 次模型迭代、60 秒，任务定义可分别设置至多 20 次迭代、120 秒。

`passed` 要求候选在所有任务、所有重复中通过全部检查，且每一轮至少有一项原先未通过的检查得到改善。任何任务退化得到 `failed`；模型错误、超时或未完成得到 `error`；当前版本也已全部通过，或改善不稳定得到 `inconclusive`。只有 `passed` 才允许采纳能力候选。模型说“通过了”、用户表扬和对话证据数都不能替代这些检查。报告保存原始工具轨迹、产物快照、每项检查结果、规则／任务／运行环境摘要和模型用量；`/evolve report` 可查看对照摘要。

采纳时会重新核对候选、任务快照、模型与端点、项目指令、当前已采纳规则和验证引擎版本。发生变化需重新验证；最近一次验证失败或取消后不能沿用先前的通过结果。撤回移除后续对话中的规则，保留历史报告。验证通过只表示在登记任务和检查条件下观察到改善，不能证明普遍提升，也不保证不同模型、真实外部工具或未覆盖任务上的效果。已采纳规则后续仍可依据反馈撤回。

试跑使用专用 `ReadFile`、`WriteFile`、`EditFile` 工具，每次操作都限制在自己的临时工作区，拒绝符号链接及路径越界。它不加载 MCP、shell、子代理、用户记忆或项目 hooks，也不运行模型生成的程序；检查器本身不接受任意命令。这个边界依赖应用层文件检查，不是操作系统沙箱。当前能力验证覆盖文件产物和流程规则；任意代码测试、浏览器／邮件／日历等带外部操作的验证尚未接入。

无需模型凭据的流水线演示：

```bash
python -m nonocode.evolution.demo --output /tmp/nonocode-verified-demo
```

演示使用脚本化模型响应，但会调用真实 Agent 文件工具、执行正负金额与仅正金额两个任务、验证固定产物，并完成采纳和撤回。它验证执行流水线，不代表真实模型已获得同样收益。

记录保存在当前工作目录的 `.nonocode/evolution/`，包含反馈、来源、证据、状态和时间，切换工作目录会使用另一份记录。请勿在反馈中输入不希望保存在本地的敏感信息。命令用于交互界面；`python -m nonocode -p "整理今天的工作计划"` 会读取当前项目已采纳的偏好并提炼新的候选，但不提供这些斜杠命令。采纳的偏好按相关性使用，当前用户请求优先。

既有自动记忆仍会从对话中提取用户、反馈和项目相关信息：用户级记忆存于 `~/.nonocode/memory/`，项目级记忆存于 `.nonocode/memory/`。`/memory list` 可查看索引，`/memory edit` 显示目录，`/memory clear` 清空自动记忆；这些操作与 `/evolve` 记录互相独立。非交互 `-p` 也使用自动记忆并等待本轮提取完成，因此运行时间可能增加。模型服务会收到对话和加载的相关上下文，请按使用的服务及其隐私政策管理敏感内容。

后台记忆整理只能通过限定的文件工具访问记忆 Markdown 和只读会话记录，不会执行 shell 命令。这个限制是应用层检查，不是操作系统沙箱。现有工具权限配置仍适用于普通助手操作。

本地测试覆盖候选提炼、证据核对、去重、跨会话积累、双臂重复执行、回归拒绝、结果不确定、错误处理、快照过期、验证门槛、采纳和撤回。真实模型的服务连通性、提炼质量与能力收益需要在填写服务配置后运行验证。当前助手按需启动；本版未包含常驻服务、定时任务或邮件／日历集成。

## 0.3.0 数据与任务中心

会话和主执行记录改用本机 SQLite；旧会话首次启动导入，Markdown 记忆保持兼容。使用 `/history <关键词>`、`/runs` 和 `/storage` 管理。网页新增历史、任务、记忆面板。升级、备份和存储边界详见 [RELEASE_NOTES_0.3.md](RELEASE_NOTES_0.3.md)。
