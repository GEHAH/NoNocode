from __future__ import annotations

import json
import shlex

from nonocode.commands.registry import Command, CommandContext, CommandType
from nonocode.evolution.store import EvolutionStore, observations

_USAGE = ("用法：/evolve feedback <偏好> | capability <能力规则> | promote <完整ID> | "
          "task <任务JSON路径> | tasks | verify <完整ID> <任务ID...> | report <完整ID> | "
          "status | list | show <完整ID> | apply <完整ID> | reject <完整ID> | revoke <完整ID>")


async def handle_evolve(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("助手尚未初始化。")
        return
    store = EvolutionStore(ctx.agent.work_dir)
    parts = ctx.args.strip().split(None, 1)
    action = parts[0].lower() if parts else "list"
    arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        if action in {"feedback", "capability"}:
            item = store.create(arg, kind="capability" if action == "capability" else "preference")
            message = (f"已保存待审核候选 {item['id']}：\n{item['feedback']}\n"
                       f"查看：/evolve show {item['id']}；采纳：/evolve apply {item['id']}")
            if action == "capability":
                message += "\n能力候选须先 /evolve verify 通过执行验证，才能采纳。"
        elif action == "promote" and arg:
            item = store.promote(arg)
            message = f"{item['id']} 已升级为待验证能力候选。原规则暂停使用；验证通过并采纳后生效。"
        elif action in {"task", "tasks"}:
            from nonocode.evolution.verification import TaskStore

            tasks = TaskStore(ctx.agent.work_dir)
            if action == "task":
                paths = shlex.split(arg)
                if len(paths) != 1:
                    raise ValueError("用法：/evolve task <任务JSON路径>，含空格的路径请加引号")
                record = tasks.register(paths[0])
                message = f"已冻结验证任务 {record['spec']['id']} 及输入快照；摘要 {record['fingerprint'][:12]}。"
            else:
                if arg:
                    raise ValueError("用法：/evolve tasks")
                message = "\n".join(f"{row['spec']['id']}：{len(row['spec']['checks'])} 项检查，"
                                    f"{row['spec']['repetitions']} 轮对照" for row in tasks.list()) or "尚未登记验证任务。"
        elif action == "verify":
            from nonocode.evolution.verification import VerificationEngine

            if getattr(ctx.ui, "_streaming", False):
                raise ValueError("请等待当前回答结束后再运行验证")
            args = shlex.split(arg)
            if len(args) < 2:
                raise ValueError("用法：/evolve verify <候选完整ID> <任务ID...>")
            from nonocode.evolution.lab import EvolutionLab
            report = await EvolutionLab(ctx.agent,getattr(getattr(ctx,"session_manager",None),"database",None),getattr(ctx.agent,"session_id","")).verify(args[0],args[1:],ctx.ui.add_system_message)
            message = (f"执行验证：{report['outcome']}\n{report['reason']}\n"
                       f"任务执行 {len(report['runs'])} 次；模型用量 {report['input_tokens']} 输入 / "
                       f"{report['output_tokens']} 输出 tokens。\n"
                       f"报告：{store.directory / 'runs' / (report['id'] + '.json')}")
        elif action == "report" and arg:
            item = store.get(arg)
            verification = item.get("verification")
            if not verification:
                raise ValueError("该候选尚无执行验证报告")
            path = store.directory / "runs" / f"{verification['run_id']}.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            lines = [f"执行验证：{report['outcome']} — {report['reason']}"]
            for run in report["runs"]:
                lines.append(f"{run['task_id']} 第 {run['repeat'] + 1} 轮 {run['arm']}："
                             f"{sum(row['passed'] for row in run['checks'])}/{len(run['checks'])} 项通过"
                             + (f"；运行错误：{run['error']}" if run['error'] else ""))
            message = "\n".join(lines) + f"\n完整报告：{path}"
        elif action == "status" and not arg:
            stats = store.stats()
            message = (f"对话学习：待审核 {stats['pending']}，已采纳 {stats['applied']}，"
                       f"已拒绝 {stats['rejected']}，已撤回 {stats['revoked']}。\n"
                       f"已保存原话证据 {stats['observations']} 条，其中成功反馈 {stats['successes']} 条，"
                       f"失败反馈 {stats['failures']} 条。\n"
                       f"能力候选 {stats['capabilities']} 条，其中最近执行报告通过 {stats['verified']} 条。\n"
                       "对话反馈用于积累候选，不等同于效果验证；/evolve list 查看。")
        elif action == "list" and not arg:
            items = store.list()
            message = "\n".join(f"{item['id']} [{item['status']}] [{item.get('kind', 'preference')}] "
                                f"[验证 {item.get('verification', {}).get('outcome', '未运行')}] "
                                f"[证据 {len(observations(item))}] {item['feedback']}"
                                for item in items) if items else "尚无改进候选。"
        elif action == "show" and arg:
            item = store.get(arg)
            message = (f"ID：{item['id']}\n状态：{item['status']}\n反馈：{item['feedback']}\n"
                       f"类别：{item.get('kind', 'preference')}\n"
                       f"最近执行报告：{item.get('verification', {}).get('outcome', '未运行')}\n"
                       f"适用场景：{item.get('cue') or '按相关性使用'}\n"
                       f"来源：{item.get('source', 'manual')}\n证据：{item.get('evidence') or '用户直接录入'}\n"
                       f"创建：{item.get('created_at', '')}\n更新：{item.get('updated_at', '')}")
            history = observations(item)
            if history:
                message += "\n反馈记录（成功／失败均为用户报告）：\n" + "\n".join(
                    f"- [{obs['signal']}] {obs['quote']} ({obs.get('created_at', '')})" for obs in history)
        elif action in {"apply", "reject", "revoke"} and arg:
            context_fingerprint = ""
            if action == "apply" and store.get(arg).get("kind") == "capability":
                from nonocode.evolution.verification import digest, runtime_context

                context_fingerprint = digest(runtime_context(ctx.agent, store))
            item = store.transition(arg, action, context_fingerprint=context_fingerprint)
            message = f"候选 {item['id']} 已更新为 {item['status']}。后续对话会使用当前已采纳的项目规则。"
        else:
            message = _USAGE
    except (ValueError, OSError) as exc:
        message = f"操作失败：{exc}"
    ctx.ui.add_system_message(message)


EVOLVE_COMMAND = Command(name="evolve", description="积累经验、对照验证、采纳或撤回改进", usage=_USAGE,
                         type=CommandType.LOCAL, handler=handle_evolve)
