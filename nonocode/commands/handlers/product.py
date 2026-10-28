from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from nonocode.commands.registry import Command, CommandContext, CommandType
from nonocode.product import ProductService


def service(ctx: CommandContext) -> ProductService:
    if ctx.session_manager is None or ctx.memory_manager is None:
        raise ValueError('会话或记忆管理器未初始化')
    return ProductService(ctx.session_manager,ctx.memory_manager)


async def handle_history(ctx: CommandContext) -> None:
    try:
        items = service(ctx).history(ctx.args)
        lines = ['历史搜索' if ctx.args.strip() else '最近会话']
        for item in items:
            lines.append(f"[{item['session_id']}] {item['title'] or '(未命名)'}\n  {item['snippet'][:250]}")
        ctx.ui.add_system_message('\n'.join(lines) if items else '没有匹配的历史记录')
    except ValueError as exc:
        ctx.ui.add_system_message(str(exc))


async def handle_runs(ctx: CommandContext) -> None:
    parts = ctx.args.split(maxsplit=1)
    sub = parts[0] if parts else 'list'
    run_id = parts[1].strip() if len(parts)>1 else ''
    database = ctx.session_manager.database
    if sub == 'list':
        runs = database.list_runs()
        lines = ['任务中心']
        lines.extend(f"[{run['id']}] {run['status']} · {run['source']} · {run['prompt'][:100]}" for run in runs)
        ctx.ui.add_system_message('\n'.join(lines) if runs else '暂无执行记录')
        return
    run = database.get_run(run_id)
    if run is None:
        ctx.ui.add_system_message('任务不存在；用 /runs 查看完整 ID')
        return
    if sub == 'show':
        lines = [f"任务 {run_id}",f"状态: {run['status']}",f"请求: {run['prompt']}",
                 f"会话: {run['session_id']}",f"Tokens: ↑{run['input_tokens']} ↓{run['output_tokens']}"]
        if run['error']:
            lines.append('错误: '+run['error'])
        lines.extend(f"  {step['tool_name']}: {step['status']} ({step['elapsed']:.1f}s)" for step in run['steps'])
        ctx.ui.add_system_message('\n'.join(lines))
    elif sub == 'cancel':
        cancel = getattr(ctx.ui,'cancel_run',None)
        ctx.ui.add_system_message('已请求取消任务' if cancel and cancel(run_id) else '任务已结束或不属于当前实例')
    elif sub == 'resume':
        if getattr(ctx.ui,'_streaming',False):
            ctx.ui.add_system_message('请先取消或等待当前任务完成')
            return
        from nonocode.commands.handlers.session import handle_session
        await handle_session(replace(ctx,args='resume '+run['session_id']))
        ctx.ui.add_system_message('已恢复任务会话。请说明下一步；已执行的工具不会自动重放。')
    else:
        ctx.ui.add_system_message('用法: /runs [list | show <id> | cancel <id> | resume <id>]')


async def handle_storage(ctx: CommandContext) -> None:
    database = ctx.session_manager.database
    parts = ctx.args.split(maxsplit=1)
    if parts and parts[0] == 'backup':
        if len(parts)<2:
            ctx.ui.add_system_message('用法: /storage backup <新备份文件路径>')
            return
        try:
            path = database.backup(Path(parts[1]))
            ctx.ui.add_system_message(f'数据库已备份: {path}')
        except (ValueError,OSError) as exc:
            ctx.ui.add_system_message(str(exc))
    else:
        ctx.ui.add_system_message(f'SQLite: {database.path}\n会话、任务保存在数据库；Markdown 记忆使用可重建索引。\n/storage backup <路径> 可创建一致性备份。')


PRODUCT_COMMANDS = [
    Command(name='history',description='搜索历史对话',usage='/history [关键词]',type=CommandType.LOCAL,handler=handle_history),
    Command(name='runs',description='任务中心：执行记录、取消和恢复会话',usage='/runs [list | show|cancel|resume <id>]',type=CommandType.LOCAL,handler=handle_runs),
    Command(name='storage',description='数据库状态及备份',usage='/storage [backup <路径>]',type=CommandType.LOCAL,handler=handle_storage),
]
