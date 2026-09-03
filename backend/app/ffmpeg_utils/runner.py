"""
Safe subprocess execution for ffmpeg/ffprobe.

SECURITY: every call here uses asyncio.create_subprocess_exec with an
argument LIST -- never a shell string. No user-controlled value (JSON
fields, filenames, URLs) is ever interpolated into a shell command. This is
the single choke point all ffmpeg/ffprobe invocations must go through.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from app.errors import FfmpegFailedError, FfprobeFailedError

FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"

_PROGRESS_LINE_RE = re.compile(r"^(\w+)=(.*)$")


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


async def run_ffprobe(args: list[str], *, timeout: float = 30.0) -> CommandResult:
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        raise TypeError("ffprobe args must be a list of strings")
    proc = await asyncio.create_subprocess_exec(
        FFPROBE_BIN,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FfprobeFailedError("ffprobe timed out while inspecting the video.")
    result = CommandResult(proc.returncode or 0, stdout_b.decode("utf-8", "replace"), stderr_b.decode("utf-8", "replace"))
    if result.returncode != 0:
        raise FfprobeFailedError(
            "ffprobe failed to inspect the video.",
            details={"stderr": result.stderr[-2000:]},
        )
    return result


async def run_ffmpeg_with_progress(
    args: list[str],
    *,
    total_duration_seconds: float,
    on_progress: Optional[Callable[[float], Awaitable[None]]] = None,
    timeout: float = 900.0,
) -> CommandResult:
    """Run ffmpeg with `-progress pipe:1` already appended by the caller's arg builder.

    Parses out_time_ms=... lines from stdout to report real (not faked) progress.
    """
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        raise TypeError("ffmpeg args must be a list of strings")

    proc = await asyncio.create_subprocess_exec(
        FFMPEG_BIN,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_chunks: list[bytes] = []

    async def _read_stderr() -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            stderr_chunks.append(line)

    async def _read_stdout_progress() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            match = _PROGRESS_LINE_RE.match(text)
            if not match:
                continue
            key, value = match.group(1), match.group(2)
            if key == "out_time_ms" and on_progress and total_duration_seconds > 0:
                try:
                    out_seconds = int(value) / 1_000_000
                except ValueError:
                    continue
                pct = max(0.0, min(100.0, (out_seconds / total_duration_seconds) * 100))
                await on_progress(pct)
            elif key == "progress" and value == "end" and on_progress:
                await on_progress(100.0)

    try:
        await asyncio.wait_for(
            asyncio.gather(_read_stderr(), _read_stdout_progress(), proc.wait()),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FfmpegFailedError("ffmpeg processing timed out.", details={"timeout_seconds": timeout})

    stderr_text = b"".join(stderr_chunks).decode("utf-8", "replace")
    if proc.returncode != 0:
        raise FfmpegFailedError(
            "ffmpeg failed while processing the clip.",
            details={"returncode": proc.returncode, "stderr": stderr_text[-4000:]},
        )
    return CommandResult(proc.returncode or 0, "", stderr_text)
