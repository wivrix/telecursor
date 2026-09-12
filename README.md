# Telecursor

Secure Telegram bridge for the local [Cursor Agent CLI](https://cursor.com/docs/cli) (`agent` / `cursor-agent`).

Run a daemon on your Ubuntu VPS or PC, then chat with your coding agent from Telegram — prompts, files, images, mode/model controls, and usage limits.

## Features

- **Auth whitelist** — only allowed Telegram user IDs / usernames can talk to the bot
- **Workspace jail** — agent runs only under a configured root path
- **Safe / Yolo modes** — approve tool calls in Telegram, or auto-approve with `--force`
- **Streaming replies** — throttled message edits, long-output splitting
- **Photos & documents** — downloaded locally, path injected into the prompt, cleaned up after
- **Model & limit controls** — `/model`, `/models`, `/limit` (Cursor usage remaining)
- **Background daemon** — `start -d` / `status` / `stop` / `logs` without blocking your terminal
- **Interactive setup** — `python main.py setup`

## Requirements

- Python 3.10+
- [Cursor Agent CLI](https://cursor.com/docs/cli) installed and logged in (`agent login`), **or** a `CURSOR_API_KEY`
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Quick start

```bash
git clone https://github.com/<you>/telecursor.git
cd telecursor

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Make the `telecursor` command available anywhere
pip install -e .
# or:  python main.py install
# or:  ./install.sh

telecursor setup          # interactive wizard
telecursor start -d       # run in background
```

After install you can use `telecursor` from any directory (ensure `~/.local/bin` is on your `PATH`).

Config lives in the project folder during development, or in `~/.config/telecursor` when installed globally. Override with `TELECURSOR_HOME=/path`.

Without the wizard, copy `.env.example` → `.env` (in the app home), edit values, then:

```bash
telecursor start -d
```

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

`python main.py …` still works from the repo checkout.

### Config examples

```bash
telecursor config --allowed-users @alice,123456789
telecursor config --workspace /home/ubuntu/projects --mode yolo
telecursor config --model composer-2.5
telecursor config --bot-token '123456:ABC…'
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
| `/status` | Session state |
| `/health` | Agent install / login check |
| `/cancel` | Stop the active agent run |

## Security notes

- Keep `.env` private — never commit it (gitignored)
- Restrict `ALLOWED_USERS` to your account only
- Set `ALLOWED_WORKSPACE_PATH` to the smallest directory you need
- Prefer `safe` mode unless you trust unattended `--force` runs on that machine
- The bot can run shell tools via the agent; treat the host as a privileged device

## systemd (optional)

For a machine that should keep the bot up across reboots:

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

## Project layout

```
main.py              CLI entrypoint (`telecursor` console script)
paths.py             App home / config / runtime paths
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
install.sh           helper to pip-install the CLI
cursor-bot.service   systemd unit template
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
| `telecursor: command not found` | `pip install -e .` then add `~/.local/bin` to `PATH` |

## License

MIT — see [LICENSE](LICENSE).
