from __future__ import annotations

from nonocode.commands.registry import Command, CommandContext, CommandType


async def handle_memory(ctx: CommandContext) -> None:
    mm = ctx.memory_manager
    if mm is None:
        ctx.ui.add_system_message("记忆管理器未初始化")
        return


    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "":
        display = mm.get_display_text()
        ctx.ui.add_system_message(display)

    elif sub == "list":
        display = mm.get_display_text()
        ctx.ui.add_system_message(display)

    elif sub in {"search", "show", "update", "add", "forget"}:
        from nonocode.commands.handlers.product import service
        try:
            product = service(ctx)
            argument = parts[1].strip() if len(parts)>1 else ''
            if sub == 'search':
                items = product.memory_entries(argument)
                ctx.ui.add_system_message('\n'.join(f"{item['scope']}/{item['name']} [{item['type']}] — {item['description']}" for item in items) or '没有匹配的记忆')
                return
            key, _, body = argument.partition(' ')
            scope, separator, name = key.partition('/')
            if not separator:
                raise ValueError('请指定 user/名称 或 project/名称')
            if sub == 'show':
                item = product.memory(scope,name)
                ctx.ui.add_system_message(f"{scope}/{name}\n{item['body']}")
            elif sub == 'forget':
                product.forget_memory(scope,name)
                ctx.ui.add_system_message(f'已删除记忆: {scope}/{name}')
            else:
                product.update_memory(scope,name,body,create=sub=='add')
                ctx.ui.add_system_message(f'已保存记忆: {scope}/{name}')
        except (ValueError,OSError) as exc:
            ctx.ui.add_system_message(str(exc))

    elif sub == "clear":
        mm.clear()
        ctx.ui.add_system_message("所有自动记忆已清空。")

    elif sub == "edit":
        ctx.ui.add_system_message(
            f"编辑记忆文件：\n"
            f"  用户级目录: {mm.user_mem_dir}\n"
            f"  项目级目录: {mm.project_mem_dir}"
        )

    else:
        ctx.ui.add_system_message(
            "用法: /memory [list | search <关键词> | show <范围/名称> | add|update <范围/名称> <内容> | forget <范围/名称> | clear | edit]"
        )


MEMORY_COMMAND = Command(
    name="memory",
    description="记忆管理",
    usage="/memory [list | search <关键词> | show <范围/名称> | add|update <范围/名称> <内容> | forget <范围/名称> | clear | edit]",
    type=CommandType.LOCAL,
    handler=handle_memory,
)

