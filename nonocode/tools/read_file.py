from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from nonocode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from nonocode.tools.file_state_cache import FileStateCache


class Params(BaseModel):
    file_path: str = Field(description="Absolute or relative path to the file to read")
    offset: int = Field(default=0, ge=0, description="Line offset to start reading from (0-based)")
    limit: int = Field(default=2000, ge=1, le=5000, description="Maximum number of lines to read")


class ReadFile(Tool):
    name = "ReadFile"
    description = "Read a file and return its contents with line numbers."
    params_model = Params
    category = "read"
    is_concurrency_safe = True


    def __init__(self, file_state_cache: FileStateCache | None = None) -> None:
        self._state_cache = file_state_cache


    async def execute(self, params: Params) -> ToolResult:
        from nonocode.tools.isolated import filesystem_worker
        data = await filesystem_worker('_file_reader.py', params.file_path, str(params.offset), str(params.limit))
        if self._state_cache and not data.get('is_error'):
            self._state_cache.record(data['resolved'], data['mtime_ns'])
        return ToolResult(output=data['output'], is_error=data.get('is_error', False))
