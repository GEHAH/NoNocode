from __future__ import annotations

from nonocode.commands.handlers.clear import CLEAR_COMMAND
from nonocode.commands.handlers.compact import COMPACT_COMMAND
from nonocode.commands.handlers.help import HELP_COMMAND
from nonocode.commands.handlers.mcp import MCP_COMMAND
from nonocode.commands.handlers.memory import MEMORY_COMMAND
from nonocode.commands.handlers.evolve import EVOLVE_COMMAND
from nonocode.commands.handlers.permission import PERMISSION_COMMAND
from nonocode.commands.handlers.plan import PLAN_COMMAND
from nonocode.commands.handlers.sandbox import SANDBOX_COMMAND
from nonocode.commands.handlers.session import SESSION_COMMAND
from nonocode.commands.handlers.skill import SKILL_COMMAND
from nonocode.commands.handlers.rewind import REWIND_COMMAND
from nonocode.commands.handlers.status import STATUS_COMMAND
from nonocode.commands.registry import CommandRegistry
from nonocode.commands.handlers.product import PRODUCT_COMMANDS
from nonocode.commands.handlers.workbench import WORKBENCH_COMMANDS


ALL_COMMANDS = [
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    SESSION_COMMAND,
    MCP_COMMAND,
    MEMORY_COMMAND,
    EVOLVE_COMMAND,
    PERMISSION_COMMAND,
    SANDBOX_COMMAND,
    REWIND_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
    *PRODUCT_COMMANDS,
    *WORKBENCH_COMMANDS,
]


def register_all_commands(registry: CommandRegistry) -> None:
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)

