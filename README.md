# Telecursor

Secure Telegram bridge for the local [Cursor Agent CLI](https://cursor.com/docs/cli) (`agent` / `cursor-agent`).

Run it as a background service on **Windows**, **macOS**, or **Linux**, then chat with your coding agent from Telegram — prompts, files, images, mode/model controls, and usage limits.

## Features

- **Auth whitelist** — only allowed Telegram user IDs / usernames can talk to the bot
- **Workspace jail** — agent runs only under a configured root path
- **Safe / Yolo modes** — approve tool calls in Telegram, or auto-approve with `--force`
- **Streaming replies** — throttled message edits, long-output splitting
- **Photos & documents** — downloaded locally, path injected into the prompt, cleaned up after
- **Model & limit controls** — `/model`, `/models`, `/limit` (Cursor usage remaining)
- **Background daemon** — `start -d` / `status` / `stop` / `logs` without blocking your terminal
- **Cross-platform** — Windows, macOS, and Linux
- **Global CLI** — `telecursor` command after install

## Requirements

- Python 3.10+
- [Cursor Agent CLI](https://cursor.com/docs/cli) installed and logged in (`agent login`), **or** a `CURSOR_API_KEY`
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Quick start

### Linux / macOS

```bash
git clone https://github.com/<you>/telecursor.git
cd telecursor

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
# or: ./install.sh

telecursor setup
telecursor start -d
```

### Windows (PowerShell)

```powershell
git clone https://github.com/<you>/telecursor.git
cd telecursor

py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
# or:  powershell -ExecutionPolicy Bypass -File .\install.ps1

telecursor setup
telecursor start -d
```

If `telecursor` is not found, add `.venv\Scripts` (or your Python `Scripts` folder) to your User PATH, or re-run `telecursor install` / `install.ps1`.

### Config location

| Mode | Where `.env` lives |
|------|--------------------|
| Dev / editable checkout | project folder |
| Global install (Linux) | `~/.config/telecursor` |
| Global install (macOS) | `~/Library/Application Support/telecursor` |
| Global install (Windows) | `%APPDATA%\telecursor` |

Override anytime with `TELECURSOR_HOME`.

## CLI reference

| Command | Description |
|--------|-------------|
| `telecursor install` | Install / refresh the `telecursor` command on PATH |
| `telecursor setup` | Interactive setup, then start |
| `telecursor setup -d` | Setup, then start in background |
| `telecursor start` | Start bot (foreground) |
| `telecursor start -d` | Start bot in background |
| `telecursor status` | Check if background bot is running |
| `telecursor stop` | Stop background bot |
| `telecursor logs` | Show recent logs |
| `telecursor logs -f` | Follow logs |
| `telecursor show` | Show config (secrets redacted) |
| `telecursor config --help` | Update `.env` from flags |

`python main.py …` (or `py main.py …` on Windows) still works from the repo checkout.

### Config examples

```bash
telecursor config --allowed-users @alice,123456789
telecursor config --workspace /home/ubuntu/projects --mode yolo
telecursor config --model composer-2.5
telecursor config --bot-token "123456:ABC…"
```

Windows paths example:

```powershell
telecursor config --workspace "C:\Users\you\projects" --mode yolo
```

## Telegram usage

1. Open your bot and send `/start`
2. Use the reply keyboard or `/menu`
3. Send a **text prompt**, **photo**, or **document**

| Command | What it does |
|--------|----------------|
| `/menu` | Control panel |
| `/mode safe\|yolo` | Tool approval vs `--force` |
| `/model <id>` | Set model (`auto` clears) |
| `/models` | List models |
| `/workspace <path>` | Change cwd (inside jail) |
| `/limit` | Remaining Cursor usage |
| `/status` | Session + queue state |
| `/queue` | Show running + queued jobs |
| `/queue clear` | Drop pending jobs (keep current) |
| `/stop` or `/cancel` | Stop the **current** running task |
| `/health` | Agent install / login check |
| `/cancel` | _(alias of /stop)_ |

## Security notes

- Keep `.env` private — never commit it (gitignored)
- Restrict `ALLOWED_USERS` to your account only
- Set `ALLOWED_WORKSPACE_PATH` to the smallest directory you need
- Prefer `safe` mode unless you trust unattended `--force` runs on that machine
- The bot can run shell tools via the agent; treat the host as a privileged device

## Autostart (optional)

### Linux (systemd)

```bash
sudo mkdir -p /opt/telecursor
sudo rsync -a --exclude .venv --exclude .runtime ./ /opt/telecursor/
cd /opt/telecursor
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
# put your .env in /opt/telecursor/.env

# Edit User=, paths, and ReadWritePaths in the unit if needed
sudo cp cursor-bot.service /etc/systemd/system/telecursor.service
sudo systemctl daemon-reload
sudo systemctl enable --now telecursor
journalctl -u telecursor -f
```

### Windows (Task Scheduler)

1. Install and configure: `telecursor setup`
2. Create a task that runs at logon:
   - Program: `C:\path\to\telecursor\.venv\Scripts\telecursor.exe`
   - Arguments: `start --foreground`
   - Start in: your project or `%APPDATA%\telecursor`
3. Or keep using `telecursor start -d` after login

### macOS (launchd)

Use a LaunchAgent that runs `telecursor start --foreground`, or start with `telecursor start -d` from your login items.

## Project layout

```
main.py              CLI entrypoint (`telecursor` console script)
paths.py             App home / config / runtime paths
platform_util.py     Windows / macOS / Linux helpers
config.py            Settings (.env / pydantic-settings)
setup_cli.py         setup / install / config / show
daemon.py            background PID / logs / stop
handlers.py          Telegram commands & media
agent_runner.py      asyncio subprocess + stream-json
cursor_info.py       usage limits, models, health
middleware.py        auth whitelist
session.py           per-chat mode / model / workspace
streaming.py         Telegram edit throttle + split
env_store.py         .env read/write
install.sh           Linux/macOS install helper
install.ps1          Windows install helper
cursor-bot.service   systemd unit template (Linux)
.env.example         sample configuration
```

## Troubleshooting

| Problem | What to try |
|--------|-------------|
| Bot ignores you | Your user id/`@username` must be in `ALLOWED_USERS` (`telecursor show`) |
| “Agent not installed” | Install CLI; set `AGENT_BIN`; `/health` |
| Auth errors | `agent login` on the host, or set `CURSOR_API_KEY` |
| Usage / limit errors | `/limit` or [cursor.com/dashboard](https://cursor.com/dashboard?tab=usage) |
| Background won’t start | `telecursor logs` |
| `telecursor: command not found` (Linux/macOS) | `pip install -e .` then add `~/.local/bin` to `PATH` |
| `telecursor` not found (Windows) | Add `.venv\Scripts` to User PATH, or run `install.ps1` |
| Execution policy blocks `install.ps1` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

## License

MIT — see [LICENSE](LICENSE).
