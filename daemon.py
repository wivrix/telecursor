"""Background process management (PID file, start/stop/status/logs)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
PID_PATH = RUNTIME_DIR / "telecursor.pid"
LOG_PATH = RUNTIME_DIR / "telecursor.log"
MAIN_PATH = PROJECT_ROOT / "main.py"


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
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def read_pid() -> int | None:
    if not PID_PATH.is_file():
        return None
    try:
        text = PID_PATH.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def write_pid(pid: int) -> None:
    _ensure_runtime_dir()
    PID_PATH.write_text(f"{pid}\n", encoding="utf-8")
    meta = RUNTIME_DIR / "telecursor.started"
    meta.write_text(
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC\n"),
        encoding="utf-8",
    )


def clear_pid() -> None:
    for path in (PID_PATH, RUNTIME_DIR / "telecursor.started"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # Prefer /proc so we can treat zombies as not running
    status_path = Path(f"/proc/{pid}/status")
    if status_path.exists():
        try:
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("State:"):
                    # e.g. "State:\tZ (zombie)"
                    state = line.split(":", 1)[1].strip()
                    if state.startswith("Z"):
                        return False
                    break
            return True
        except OSError:
            return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_looks_like_bot(pid: int) -> bool:
    """Best-effort check that PID still belongs to this project."""
    cmdline = _read_cmdline(pid)
    if cmdline is None:
        return True  # can't tell (e.g. non-Linux) — assume ok
    joined = " ".join(cmdline).lower()
    return "main.py" in joined or "telecursor" in joined


def _signal_pid(pid: int, sig: signal.Signals) -> None:
    """Signal the process; also try its process group (background sessions)."""
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    try:
        pgid = os.getpgid(pid)
        if pgid > 0:
            os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _read_cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]


def _read_started_at() -> str | None:
    path = RUNTIME_DIR / "telecursor.started"
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
    if pid is None:
        return ProcessStatus(
            running=False,
            pid=None,
            pid_file=PID_PATH,
            log_file=LOG_PATH,
            detail="No PID file (bot is not running in background).",
        )
    if not pid_is_alive(pid):
        clear_pid()
        return ProcessStatus(
            running=False,
            pid=pid,
            pid_file=PID_PATH,
            log_file=LOG_PATH,
            detail=f"Stale PID file for {pid} (process not running). Cleared.",
        )
    if not pid_looks_like_bot(pid):
        clear_pid()
        return ProcessStatus(
            running=False,
            pid=pid,
            pid_file=PID_PATH,
            log_file=LOG_PATH,
            detail=f"PID {pid} is alive but is not the telecursor bot. Cleared stale PID file.",
        )
    return ProcessStatus(
        running=True,
        pid=pid,
        pid_file=PID_PATH,
        log_file=LOG_PATH,
        started_at=started,
        uptime=_format_uptime(started),
        detail="Bot is running in the background.",
    )


def print_status() -> int:
    status = get_status()
    if status.running:
        print("✅ Telecursor bot is running")
        print(f"  PID:      {status.pid}")
        if status.started_at:
            print(f"  Started:  {status.started_at}")
        if status.uptime:
            print(f"  Uptime:   {status.uptime}")
        print(f"  Log:      {status.log_file}")
        print(f"  PID file: {status.pid_file}")
        print("\nCommands:")
        print("  python main.py logs      # view recent logs")
        print("  python main.py logs -f   # follow logs")
        print("  python main.py stop      # stop background bot")
        return 0
    print("⏹  Telecursor bot is not running")
    if status.detail:
        print(f"  {status.detail}")
    print(f"  Log: {status.log_file}" + (" (exists)" if status.log_file.is_file() else ""))
    print("\nStart with:")
    print("  python main.py start -d")
    return 1


def start_background(*, python_exe: str | None = None) -> int:
    status = get_status()
    if status.running:
        print(f"Already running (PID {status.pid}). Use: python main.py status")
        return 1

    _ensure_runtime_dir()
    exe = python_exe or sys.executable
    # Child runs foreground start; parent returns to the shell
    cmd = [exe, "-u", str(MAIN_PATH), "start", "--foreground"]

    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(
            f"\n===== start {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} =====\n"
        )
        log_file.flush()
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "TELECURSOR_BACKGROUND": "1"},
        )

    write_pid(proc.pid)
    # Brief settle check
    time.sleep(0.4)
    if proc.poll() is not None:
        clear_pid()
        print(f"❌ Bot exited immediately (code {proc.returncode}). Check logs:")
        print(f"   python main.py logs")
        print(f"   tail -n 50 {LOG_PATH}")
        return 1

    print(f"✅ Bot started in background (PID {proc.pid})")
    print(f"   Log:    {LOG_PATH}")
    print(f"   Status: python main.py status")
    print(f"   Stop:   python main.py stop")
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
        _signal_pid(pid, signal.SIGTERM)
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

    print("Graceful stop timed out; sending SIGKILL…")
    try:
        _signal_pid(pid, signal.SIGKILL)
    except PermissionError:
        pass
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not pid_is_alive(pid):
            clear_pid()
            print("✅ Stopped (killed).")
            return 0
        time.sleep(0.1)

    clear_pid()
    if pid_is_alive(pid):
        print(f"❌ Failed to stop PID {pid}")
        return 1
    print("✅ Stopped (killed).")
    return 0


def tail_logs(*, follow: bool = False, lines: int = 50) -> int:
    if not LOG_PATH.is_file():
        print(f"No log file yet at {LOG_PATH}")
        print("Start the bot first: python main.py start -d")
        return 1

    try:
        content = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"Cannot read log: {exc}")
        return 1

    for line in content[-lines:]:
        print(line)

    if not follow:
        return 0

    print("\n--- following (Ctrl+C to stop) ---")
    try:
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
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
