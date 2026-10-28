from __future__ import annotations

from typing import TYPE_CHECKING

from nonocode.context import CompactCircuitBreaker, RecoveryState
from nonocode.filehistory import FileHistory

if TYPE_CHECKING:
    from nonocode.agent import Agent


def reset_agent_session(agent: Agent, session_id: str | None = None) -> None:
    """Start fresh per-session state when clearing, creating, or resuming a session."""
    if session_id is not None:
        history = FileHistory(agent.work_dir, session_id)
        agent.session_id = session_id
        agent.file_history = history
        for tool in agent.registry.list_tools():
            if hasattr(tool, "file_history"):
                tool.file_history = history
    agent._loop_count = 0
    agent.clear_active_skills()
    agent.total_input_tokens = 0
    agent.total_output_tokens = 0
    agent.recovery_state = RecoveryState()
    agent.compact_breaker = CompactCircuitBreaker()
    agent._plan_path_cache = None

