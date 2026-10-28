from __future__ import annotations

from nonocode.commands.registry import Command, CommandContext, CommandType
from nonocode.conversation import ConversationManager
from nonocode.commands.session_state import reset_agent_session


async def handle_clear(ctx: CommandContext) -> None:
    new_session = None
    if ctx.session:
        ctx.session.close()

    if ctx.session_manager:
        new_session = ctx.session_manager.create()
        ctx.config["set_session"](new_session)

    ctx.config["set_conversation"](ConversationManager())

    if ctx.agent:
        reset_agent_session(ctx.agent, new_session.session_id if new_session else None)

    ctx.config["clear_chat"]()
    ctx.ui.refresh_status()
    ctx.ui.add_system_message("对话已清除，新会话已创建")


CLEAR_COMMAND = Command(
    name="clear",
    description="清除对话历史",
    usage="/clear",
    type=CommandType.LOCAL_UI,
    handler=handle_clear,
)

