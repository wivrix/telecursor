# Telecursor — Telegram Bot for Cursor AI Agent (Remote Coding from Your Phone)

**Telecursor** is an open-source **Telegram bot for Cursor Agent CLI**.  
It lets you **control Cursor AI remotely** from Telegram on your phone or laptop — send coding prompts, upload files and images, run shell tasks through the agent, switch models, check usage limits, and queue jobs — while the real work runs on your **Windows, macOS, or Linux** machine.

> Search-friendly summary: *Telegram + Cursor AI bridge*, *remote Cursor agent bot*, *code with Cursor from Telegram*, *cursor-agent Telegram controller*.

---

## What is Telecursor?

If you use **[Cursor](https://cursor.com)** and its terminal agent (`agent` / `cursor-agent`), Telecursor turns that local agent into a **private Telegram coding assistant**.

You message the bot → Telecursor runs the prompt on your PC/VPS with Cursor Agent → streamed results come back in Telegram.

### Good for

- Coding or debugging **away from your desk**
- Triggering Cursor Agent jobs from **mobile Telegram**
- Running a **self-hosted AI coding bot** you fully control
- Keeping a secure remote channel to a home PC or Ubuntu VPS

### Not a cloud Cursor clone

Telecursor does **not** host models itself. It securely bridges Telegram to **your** installed Cursor Agent CLI and account.

---

## Why people search for this

| You might be looking for… | Telecursor does this |
|---------------------------|----------------------|
| Telegram bot for Cursor AI | ✅ Yes |
| Use Cursor Agent from phone | ✅ Yes |
| Remote coding assistant over Telegram | ✅ Yes |
| cursor-agent / CLI Telegram controller | ✅ Yes |
| Send images/files to Cursor from Telegram | ✅ Yes |
| Queue prompts while agent is busy | ✅ Yes |
| Self-hosted alternative to chat-only bots | ✅ Local agent + tools |
| Windows / Mac / Linux support | ✅ All three |

---

## Key features

- **Private access control** — whitelist Telegram user IDs / `@usernames`
- **Workspace jail** — agent only works inside an allowed folder
- **Safe or Yolo mode** — approve tools in Telegram, or auto-approve (`--force`)
- **Job queue** — new prompts wait if a task is running; `/stop` cancels only the current job
- **Streaming replies** — live updates without Telegram rate-limit spam
- **Photos & documents** — download → attach path to prompt → auto-cleanup
- **Model & usage controls** — `/model`, `/models`, `/limit`
- **Background service** — `telecursor start -d`, plus `status` / `stop` / `logs`
- **One command install** — run `telecursor` from anywhere after setup

---

## Requirements

1. **Python 3.10+**
2. **[Cursor Agent CLI](https://cursor.com/docs/cli)** installed and logged in (`agent login`), **or** a `CURSOR_API_KEY`
3. A Telegram bot token from **[@BotFather](https://t.me/BotFather)**

---

## Quick start (5 minutes)

### One-line install

**Linux / macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/wivrix/telecursor/main/install.sh | bash && source ~/.bashrc
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/wivrix/telecursor/main/install.ps1 | iex
```

Then:

```bash
telecursor setup
telecursor start -d
```

> Default install folder: `~/telecursor` (override with `TELECURSOR_DIR=/path`).

### Manual install — Linux / macOS

```bash
git clone https://github.com/wivrix/telecursor.git
cd telecursor

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
# or: bash install.sh

telecursor setup
telecursor start -d
telecursor status
```

### Manual install — Windows (PowerShell)

```powershell
git clone https://github.com/wivrix/telecursor.git
cd telecursor

py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
# or: powershell -ExecutionPolicy Bypass -File .\install.ps1

telecursor setup
telecursor start -d
telecursor status
```

If Windows says `telecursor` is not recognized, open a **new** terminal (PATH was updated), or re-run the one-liner / `install.ps1`.

### First message in Telegram

1. Open your bot  
2. Send `/start`  
3. Send a prompt like: `Explain the project structure`  
4. Or send a screenshot / file with a caption  

---

## How it works (simple)

```
Telegram app  →  Telecursor bot (on your PC/VPS)  →  Cursor Agent CLI  →  your project files
      ↑                         │
      └──── streamed reply ─────┘
```

1. Only whitelisted users can talk to the bot  
2. Each prompt runs inside your allowed workspace path  
3. If a job is already running, new prompts are queued  
4. Results stream back as Telegram messages  

---

## Telegram commands (cheat sheet)

| Command | What it does |
|--------|----------------|
| `/start` `/help` | Show help + keyboard |
| `/menu` | Buttons for mode, model, queue, stop… |
| `/mode safe` | Ask before running tools |
| `/mode yolo` | Auto-approve tools (`--force`) |
| `/model <id>` | Choose Cursor model (`auto` to reset) |
| `/models` | List available models |
| `/workspace <path>` | Change working folder (inside jail) |
| `/limit` | Show remaining Cursor usage |
| `/status` | Mode, model, busy state, queue size |
| `/queue` | Show running + waiting jobs |
| `/queue clear` | Remove waiting jobs (keep current) |
| `/stop` or `/cancel` | Stop **only the current** running task |
| `/health` | Check agent install + login |

**Tip:** While one task runs, just send another message — it is queued automatically.

---

## CLI commands (server / PC)

| Command | Description |
|--------|-------------|
| `telecursor install` | Put `telecursor` on your PATH |
| `telecursor setup` | Interactive setup wizard |
| `telecursor setup -d` | Setup, then start in background |
| `telecursor start` | Start bot (foreground) |
| `telecursor start -d` | Start bot in background |
| `telecursor status` | Is the bot running? |
| `telecursor stop` | Stop background bot |
| `telecursor logs` | Recent logs |
| `telecursor logs -f` | Follow logs live |
| `telecursor show` | Show config (secrets hidden) |
| `telecursor config --help` | Change settings from terminal |

Examples:

```bash
telecursor config --allowed-users @alice,123456789
telecursor config --workspace /home/you/projects --mode yolo
telecursor config --model composer-2.5
telecursor config --bot-token "123456:ABC…"
```

Windows:

```powershell
telecursor config --workspace "C:\Users\you\projects" --mode yolo
```

You can still use `python main.py …` / `py main.py …` from the repo folder.

---

## Configuration & files

### Where is my `.env`?

| Situation | Config folder |
|-----------|----------------|
| Running from a git checkout (dev) | project folder |
| Global install on Linux | `~/.config/telecursor` |
| Global install on macOS | `~/Library/Application Support/telecursor` |
| Global install on Windows | `%APPDATA%\telecursor` |

Override with environment variable: `TELECURSOR_HOME`.

Important settings (see `.env.example`):

- `BOT_TOKEN` — Telegram bot token  
- `ALLOWED_USERS` — your Telegram ID and/or `@username`  
- `ALLOWED_WORKSPACE_PATH` — folder jail for the agent  
- `AGENT_BIN` — path to `agent` / `cursor-agent`  
- `DEFAULT_MODE` — `safe` or `yolo`  
- `AGENT_MODEL` — optional default model  
- `MAX_QUEUE_SIZE` — max waiting prompts per chat (default 20)  

---

## Security (please read)

Telecursor is powerful because Cursor Agent can edit files and run shell commands on your machine.

- Never commit `.env` (already gitignored)
- Allow **only your** Telegram account in `ALLOWED_USERS`
- Keep `ALLOWED_WORKSPACE_PATH` as small as possible
- Prefer `/mode safe` unless you trust unattended runs
- Treat the host PC/VPS as a privileged device

---

## Run at startup (optional)

### Linux — systemd

```bash
sudo mkdir -p /opt/telecursor
sudo rsync -a --exclude .venv --exclude .runtime ./ /opt/telecursor/
cd /opt/telecursor
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
# add /opt/telecursor/.env

sudo cp cursor-bot.service /etc/systemd/system/telecursor.service
# edit User= and ReadWritePaths= as needed
sudo systemctl daemon-reload
sudo systemctl enable --now telecursor
journalctl -u telecursor -f
```

### Windows — Task Scheduler

1. Finish `telecursor setup`
2. Create a logon task:
   - Program: `...\telecursor\.venv\Scripts\telecursor.exe`
   - Arguments: `start --foreground`
3. Or manually run `telecursor start -d` after login

### macOS — login item / launchd

Run `telecursor start --foreground` from a LaunchAgent, or `telecursor start -d` at login.

---

## FAQ

### Is Telecursor free?

Yes — MIT licensed open source. You still need a Cursor account/plan for the agent.

### Does it work without the Cursor desktop app open?

It needs the **Cursor Agent CLI** available on the machine (and login or API key). The full IDE UI does not have to be open.

### Can multiple people use one bot?

Only users listed in `ALLOWED_USERS`. For safety, keep that list short.

### What happens if I send prompts while one is running?

They are **queued**. Use `/queue` to inspect, `/stop` to cancel the active job, `/queue clear` to drop waiting jobs.

### Can I use it on a VPS?

Yes. Install Cursor Agent + Telecursor on the VPS, whitelist your Telegram user, and keep the workspace jail tight.

### Is my code uploaded to Telegram’s servers forever?

Prompts and replies go through Telegram like any chat. Temp uploads are stored briefly on **your** machine for the agent, then deleted. Review Telegram’s privacy model if that matters for your data.

### How is this different from ChatGPT Telegram bots?

Those usually call a chat API only. Telecursor drives **Cursor Agent** on your machine — with project context, tools, and file edits inside your jail.

---

## Troubleshooting

| Problem | Fix |
|--------|-----|
| Bot ignores messages | Add your Telegram ID/`@username` to `ALLOWED_USERS` (`telecursor show`) |
| “Agent not installed” | Install Cursor CLI; set `AGENT_BIN`; check `/health` |
| Auth / login errors | Run `agent login` or set `CURSOR_API_KEY` |
| Usage / limit errors | `/limit` or [Cursor usage dashboard](https://cursor.com/dashboard?tab=usage) |
| Background bot won’t start | `telecursor logs` |
| `telecursor: command not found` (Linux/macOS) | `pip install -e .` and ensure `~/.local/bin` is on `PATH` |
| `telecursor` not found (Windows) | Add `.venv\Scripts` to PATH or run `install.ps1` |
| PowerShell blocks `install.ps1` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

---

## Project structure

```text
main.py              CLI entry (`telecursor` command)
handlers.py          Telegram commands, queue, media
agent_runner.py      Runs cursor-agent + streams output
daemon.py            Background start/stop/status/logs
session.py           Per-chat mode/model/queue state
config.py            Settings from .env
setup_cli.py         setup / install / config helpers
paths.py             Config home paths per OS
platform_util.py     Windows / macOS / Linux utilities
cursor_info.py       Usage limits, models, health checks
install.sh           Linux/macOS installer
install.ps1          Windows installer
cursor-bot.service   systemd template
.env.example         Sample environment file
```

---

## Keywords & topics

`telegram bot cursor ai` · `cursor agent telegram` · `remote cursor cli` · `code from phone telegram` · `self-hosted coding agent bot` · `cursor-agent bridge` · `aiogram cursor bot` · `windows mac linux telegram coding assistant`

---

## Contributing

Issues and PRs are welcome — especially docs, Windows edge cases, and safer defaults.

## License

MIT — see [LICENSE](LICENSE).

---

**Telecursor** — the simple way to **use Cursor AI from Telegram**, securely, on your own machine.
