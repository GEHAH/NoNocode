# NoNocode

NoNocode 是一个跑在终端里的个人 AI 助手。它能写代码、查资料、做规划、整理文件，并把你的工作方式留在项目里：长期记忆、经过审核的偏好，以及用固定任务验证过的做法。

改进记在规则和记忆里。它不训练模型权重，也不自己改核心程序。

## 能力

- **编码代理循环。** 读取、搜索、编辑文件，运行命令，多轮调用工具，直到任务做完。上下文接近上限时自动摘要，也可以手动压缩。
- **三种使用方式。** Textual 交互界面、`nonocode -p` 单次提示（可输出逐行 JSON），以及浏览器里的远程界面。
- **多模型接口。** 同一套助手可接 Anthropic、OpenAI，以及 OpenAI 兼容服务。上下文窗口按配置、服务端元数据、内置映射和默认值逐级确定。
- **子代理与团队。** 只读探索、方案规划和通用执行可以交给独立子代理。需要协作时可以建团队，用消息和共享任务板分工；队友可以在当前进程、tmux 或 iTerm2 里运行。
- **技能、MCP 与 Hook。** 项目或用户目录里的 Skill 按需加载。MCP 工具延迟发现，避免一开始塞满工具列表。会话、工具和文件变更可以挂自己的 Hook。
- **权限与隔离。** 默认读取直接放行，写文件和执行命令需要确认。另有危险命令检测、路径限制，以及可选的操作系统沙箱（Linux bubblewrap，macOS Seatbelt）。Git worktree 用来把一次改动放进独立工作区。
- **可撤回的长期记忆。** 用户偏好跟你走，项目事实跟仓库走。你可以直接查看、编辑或清空。
- **审核后的自进化。** 对话里的明确纠正会变成待审核候选。语言和表达类偏好由你决定是否采纳。涉及步骤或效果的能力主张，必须先在冻结的任务上对照当前规则试跑，检查通过后才能进入后续对话，并且随时可以撤回。

## 优势

- **当前任务优先。** 已采纳的偏好会按场景注入，但和这次请求冲突时，以你现在的要求为准。
- **证据留在你说过的话里。** 自动候选要能对上用户原话。系统提醒、助手自称成功和一句“谢谢”不会变成规则。
- **能力改进靠对照，不靠感觉。** 试跑使用冻结的输入和固定检查，期望答案不交给执行中的助手。只有稳定变好且没有退步，才允许采纳。
- **采纳和撤回都由你决定。** 反馈次数不会自动启用一条规则，新反馈也不会悄悄改写已经采纳的条文。
- **本地状态和密钥留在本机。** 会话、记忆、进化记录和 `config.yaml` 不进入版本库。密钥从环境变量读取。

## 开始使用

需要 Python 3.11 或更新版本。

```bash
python -m pip install -e .
cp .nonocode/config.yaml.example .nonocode/config.yaml
```

把 `.nonocode/config.yaml` 里的 `api_key` 留空，再在当前终端设置对应环境变量。Anthropic 协议读取 `ANTHROPIC_API_KEY`，OpenAI 与 OpenAI 兼容协议读取 `OPENAI_API_KEY`。

```bash
read -r -s -p "ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY
printf '\n'
export ANTHROPIC_API_KEY
nonocode
```

配置按 `~/.nonocode/config.yaml`、当前目录 `.nonocode/config.yaml`、`.nonocode/config.local.yaml` 的顺序合并，后面的 provider 列表覆盖前面的列表。项目指令写在 `NONOCODE.md`。更完整的记忆与进化用法见 `PERSONAL_ASSISTANT.md`。

只跑一次提示：

```bash
nonocode -p "请只回复 OK"
```

## 开发

```bash
python -m pytest
```

不连接真实模型、只检查进化流水线：

```bash
python -m nonocode.evolution.demo --output /tmp/nonocode-verified-demo
```

## 许可

尚未指定开源许可证。使用或再分发前请先与维护者确认。
