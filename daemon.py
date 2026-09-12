"""Background process management (PID file, start/stop/status/logs)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from paths import app_home, package_dir, runtime_dir
from platform_util import (
    IS_WINDOWS,
    pid_is_alive,
    popen_detached_kwargs,
    read_cmdline,
    terminate_process,
)


def _runtime() -> Path:
    return runtime_dir()


def _pid_path() -> Path:
    return _runtime() / "telecursor.pid"


def _log_path() -> Path:
    return _runtime() / "telecursor.log"


def _started_path() -> Path:
    return _runtime() / "telecursor.started"


@dataclass
class ProcessStatus:
    running: bool
    pid: int | None
    pid_file: Path
    log_file: Path
    started_at: str | None = None
    uptime: str | None = None
    detail: str = ""


def _ensure_runtime_dir() -> None:
    _runtime().mkdir(parents=True, exist_ok=True)


def read_pid() -> int | None:
    path = _pid_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def write_pid(pid: int) -> None:
    _ensure_runtime_dir()
    _pid_path().write_text(f"{pid}\n", encoding="utf-8")
    _started_path().write_text(
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC\n"),
        encoding="utf-8",
    )


def clear_pid() -> None:
    for path in (_pid_path(), _started_path()):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def pid_looks_like_bot(pid: int) -> bool:
    cmdline = read_cmdline(pid)
    if cmdline is None:
        # Can't verify (common on Windows without WMIC) — assume OK
        return True
    joined = " ".join(cmdline).lower()
    return (
        "main.py" in joined
        or "telecursor" in joined
        or "from main import" in joined
    )


def _read_started_at() -> str | None:
    path = _started_path()
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _format_uptime(started_text: str | None) -> str | None:
    if not started_text:
        return None
    try:
        started = datetime.strptime(started_text, "%Y-%m-%d %H:%M:%S UTC").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    seconds = int((datetime.now(timezone.utc) - started).total_seconds())
    if seconds < 0:
        return None
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def get_status() -> ProcessStatus:
    pid = read_pid()
    started = _read_started_at()
    pid_file = _pid_path()
    log_file = _log_path()
    if pid is None:
        return ProcessStatus(
            running=False,
            pid=None,
            pid_file=pid_file,
            log_file=log_file,
            detail="No PID file (bot is not running in background).",
        )
    if not pid_is_alive(pid):
        clear_pid()
        return ProcessStatus(
            running=False,
            pid=pid,
            pid_file=pid_file,
            log_file=log_file,
            detail=f"Stale PID file for {pid} (process not running). Cleared.",
        )
    if not pid_looks_like_bot(pid):
        clear_pid()
        return ProcessStatus(
            running=False,
            pid=pid,
            pid_file=pid_file,
            log_file=log_file,
            detail=f"PID {pid} is alive but is not the telecursor bot. Cleared stale PID file.",
        )
    return ProcessStatus(
        running=True,
        pid=pid,
        pid_file=pid_file,
        log_file=log_file,
        started_at=started,
        uptime=_format_uptime(started),
        detail="Bot is running in the background.",
    )


def _cli_name() -> str:
    from setup_cli import _telecursor_bin

    bin_path = _telecursor_bin()
    if bin_path and shutil.which("telecursor"):
        return "telecursor"
    if bin_path:
        return str(bin_path)
    return f"{Path(sys.executable).name} main.py"


def resolve_bot_command() -> list[str]:
    """
    Prefer the installed `telecursor` console script so background runs work
    from any working directory.
    """
    from setup_cli import _telecursor_bin

    installed = _telecursor_bin()
    if installed:
        return [str(installed)]
    main_py = package_dir() / "main.py"
    return [sys.executable, "-u", str(main_py)]


def print_status() -> int:
    status = get_status()
    cli = _cli_name()
    if status.running:
        print("✅ Telecursor bot is running")
        print(f"  PID:      {status.pid}")
        if status.started_at:
            print(f"  Started:  {status.started_at}")
        if status.uptime:
            print(f"  Uptime:   {status.uptime}")
        print(f"  Log:      {status.log_file}")
        print(f"  PID file: {status.pid_file}")
        print(f"  Home:     {app_home()}")
        print("\nCommands:")
        print(f"  {cli} logs      # view recent logs")
        print(f"  {cli} logs -f   # follow logs")
        print(f"  {cli} stop      # stop background bot")
        return 0
    print("⏹  Telecursor bot is not running")
    if status.detail:
        print(f"  {status.detail}")
    exists = " (exists)" if status.log_file.is_file() else ""
    print(f"  Log: {status.log_file}{exists}")
    print(f"  Home: {app_home()}")
    print("\nStart with:")
    print(f"  {cli} start -d")
    return 1


def start_background(*, python_exe: str | None = None) -> int:
    status = get_status()
    cli = _cli_name()
    if status.running:
        print(f"Already running (PID {status.pid}). Use: {cli} status")
        return 1

    _ensure_runtime_dir()
    if python_exe:
        cmd = [python_exe, "-u", str(package_dir() / "main.py"), "start", "--foreground"]
    else:
        cmd = [*resolve_bot_command(), "start", "--foreground"]

    log_path = _log_path()
    popen_kwargs = popen_detached_kwargs()
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(
            f"\n===== start {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} =====\n"
        )
        log_file.flush()
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(app_home()),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env={**os.environ, "TELECURSOR_BACKGROUND": "1"},
            **popen_kwargs,
        )

    write_pid(proc.pid)
    time.sleep(0.6 if IS_WINDOWS else 0.4)
    if proc.poll() is not None:
        clear_pid()
        print(f"❌ Bot exited immediately (code {proc.returncode}). Check logs:")
        print(f"   {cli} logs")
        print(f"   ({log_path})")
        return 1

    print(f"✅ Bot started in background (PID {proc.pid})")
    print(f"   Log:    {log_path}")
    print(f"   Status: {cli} status")
    print(f"   Stop:   {cli} stop")
    return 0


def stop_background(*, timeout: float = 15.0) -> int:
    status = get_status()
    if not status.running or status.pid is None:
        print("Bot is not running.")
        clear_pid()
        return 0

    pid = status.pid
    print(f"Stopping bot (PID {pid})…")
    try:
        terminate_process(pid, force=False)
    except PermissionError as exc:
        print(f"❌ Permission denied stopping PID {pid}: {exc}")
        return 1

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_is_alive(pid):
            clear_pid()
            print("✅ Stopped.")
            return 0
        time.sleep(0.2)

    print("Graceful stop timed out; forcing kill…")
    try:
        terminate_process(pid, force=True)
    except PermissionError:
        pass
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not pid_is_alive(pid):
            clear_pid()
            print("✅ Stopped (killed).")
            return 0
        time.sleep(0.1)

    clear_pid()
    if pid_is_alive(pid):
        print(f"❌ Failed to stop PID {pid}")
        if IS_WINDOWS:
            print(f"   Try manually: taskkill /PID {pid} /T /F")
        return 1
    print("✅ Stopped (killed).")
    return 0


def tail_logs(*, follow: bool = False, lines: int = 50) -> int:
    log_path = _log_path()
    cli = _cli_name()
    if not log_path.is_file():
        print(f"No log file yet at {log_path}")
        print(f"Start the bot first: {cli} start -d")
        return 1

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"Cannot read log: {exc}")
        return 1

    for line in content[-lines:]:
        print(line)

    if not follow:
        return 0

    print("\n--- following (Ctrl+C to stop) ---")
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, os.SEEK_END)
            while True:
                line = fh.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.25)
    except KeyboardInterrupt:
        print()
        return 0
