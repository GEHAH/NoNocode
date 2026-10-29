"""Run filesystem work outside the UI loop, with a killable deadline."""
from __future__ import annotations
import asyncio
from pathlib import Path
import sys
import json

from nonocode.tools.bash import Bash


async def filesystem_worker(script: str, *arguments: str) -> dict:
    proc = None
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).with_name(script)), *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=(sys.platform != 'win32'),
    ))
    try:
        proc = await asyncio.shield(spawn)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode:
            return {'output':'Error: filesystem worker process failed','is_error':True}
        return json.loads(stdout)
    except asyncio.CancelledError:
        if proc is None:
            proc = await spawn
        await Bash._stop_process(proc)
        raise
    except asyncio.TimeoutError:
        await Bash._stop_process(proc)
        return {'output':'Error: filesystem operation timed out after 30s; check filesystem availability','is_error':True}
    except (OSError, ValueError, KeyError):
        if proc is not None:
            await Bash._stop_process(proc)
        return {'output':'Error: filesystem worker failed','is_error':True}
