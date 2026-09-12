"""Cross-platform helpers (Windows / macOS / Linux)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path


IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"


def user_config_home() -> Path:
    """OS-standard config directory for Telecursor (when not in a source checkout)."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "telecursor"
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / "telecursor"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "telecursor"
    return Path.home() / ".config" / "telecursor"


def cursor_auth_candidates() -> list[Path]:
    """Likely locations of Cursor CLI auth.json across platforms."""
    home = Path.home()
    candidates = [
        home / ".cursor" / "auth.json",
        home / ".config" / "cursor" / "auth.json",
    ]
    if IS_WINDOWS:
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        candidates.extend(
            [
                appdata / "Cursor" / "auth.json",
                local / "Cursor" / "auth.json",
                local / "cursor-agent" / "auth.json",
            ]
        )
    elif IS_MACOS:
        candidates.append(
            home / "Library" / "Application Support" / "Cursor" / "auth.json"
        )
    return candidates


def agent_bin_search_paths() -> list[Path]:
    """Extra filesystem locations to look for the agent binary."""
    home = Path.home()
    paths: list[Path] = [
        home / ".local" / "bin" / "agent",
        home / ".local" / "bin" / "cursor-agent",
    ]
    if IS_WINDOWS:
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        for base in (local, appdata, home):
            paths.extend(
                [
                    base / "cursor-agent" / "agent.exe",
                    base / "cursor-agent" / "cursor-agent.exe",
                    base / "Programs" / "cursor-agent" / "agent.exe",
                    base / "cursor" / "resources" / "app" / "bin" / "cursor-agent.exe",
                ]
            )
        # npm / scoop / chocolatey style
        paths.extend(
            [
                home / "AppData" / "Roaming" / "npm" / "agent.cmd",
                home / "scoop" / "shims" / "agent.exe",
            ]
        )
    elif IS_MACOS:
        paths.extend(
            [
                Path("/usr/local/bin/agent"),
                Path("/opt/homebrew/bin/agent"),
                home / ".cursor" / "bin" / "agent",
            ]
        )
    return paths


def console_script_names() -> list[str]:
    if IS_WINDOWS:
        return ["telecursor.exe", "telecursor.cmd", "telecursor.bat", "telecursor"]
    return ["telecursor"]


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False

    if IS_WINDOWS:
        return _windows_pid_alive(pid)

    # Linux / macOS: prefer /proc when present (also detects zombies on Linux)
    status_path = Path(f"/proc/{pid}/status")
    if status_path.exists():
        try:
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("State:"):
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
    except OSError:
        return False
    return True


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return exit_code.value == STILL_ACTIVE
        return True
    finally:
        kernel32.CloseHandle(handle)


def read_cmdline(pid: int) -> list[str] | None:
    if IS_WINDOWS:
        return _windows_cmdline(pid)
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]


def _windows_cmdline(pid: int) -> list[str] | None:
    """Best-effort command line via WMIC (available on most Windows builds)."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                f"ProcessId={pid}",
                "get",
                "CommandLine",
                "/VALUE",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.startswith("CommandLine="):
            value = line.split("=", 1)[1].strip()
            return [value] if value else None
    return None


def terminate_process(pid: int, *, force: bool = False) -> None:
    """Ask a process to exit (graceful) or kill it (force)."""
    if IS_WINDOWS:
        _windows_terminate(pid, force=force)
        return

    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    if not force:
        try:
            pgid = os.getpgid(pid)
            if pgid > 0:
                os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _windows_terminate(pid: int, *, force: bool) -> None:
    if not force:
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            return
        except (OSError, ValueError):
            pass
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    args = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        args.append("/F")
    try:
        subprocess.run(
            args,
            capture_output=True,
            timeout=10,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def popen_detached_kwargs() -> dict:
    """Extra subprocess.Popen kwargs for a background child process."""
    if IS_WINDOWS:
        create_new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": create_new_group | create_no_window}
    return {"start_new_session": True}


def path_hint_for_scripts_dir(scripts_dir: Path) -> str:
    if IS_WINDOWS:
        return (
            f'Add to User PATH (PowerShell):\n'
            f'  [Environment]::SetEnvironmentVariable(\n'
            f'    "Path",\n'
            f'    $env:Path + ";{scripts_dir}",\n'
            f'    "User"\n'
            f'  )\n'
            f"Then open a new terminal."
        )
    return (
        f'Add this to your shell profile (~/.bashrc or ~/.zshrc):\n'
        f'  export PATH="{scripts_dir}:$PATH"\n'
        f"Then run:  source ~/.bashrc"
    )
