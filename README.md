# Telecursor

Control the local **Cursor Agent CLI** from **Telegram**.

Send prompts (and files) from your phone → the agent runs on your machine → replies stream back in chat.

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/wivrix/telecursor/main/install.sh | bash && source ~/.bashrc
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/wivrix/telecursor/main/install.ps1 | iex
```

Requirements: Python 3.10+, [Cursor Agent CLI](https://cursor.com/docs/cli) (`agent login` or `CURSOR_API_KEY`), a Telegram bot token from [@BotFather](https://t.me/BotFather).

---

## Quick start

```bash
telecursor setup
cd /path/to/your/project
telecursor start -d
```

Then open your bot in Telegram and send `/start`.

### Multiple projects

Register as many folders as you want — one bot serves all of them:

```bash
cd ~/project-a && telecursor start -d   # starts the bot + registers project-a
cd ~/project-b && telecursor start -d   # registers project-b (bot already running)
```

In Telegram: **Menu → Projects** (or `/projects`) to switch. Each chat keeps its own selection and history.

```bash
telecursor projects          # list
telecursor projects remove <id>
```

---

## Telegram

**Chat buttons:** Menu · Status · Clear history · Refresh

**Menu includes:** Projects · Run mode (agent / plan / ask) · Approvals (safe / yolo) · Model · Effort · Path · Limit · Queue · Stop · Health · Help

| Command | What it does |
|---------|----------------|
| `/projects` | Select a registered project |
| `/runmode agent\|plan\|ask` | Full agent, planning, or Q&A |
| `/mode safe\|yolo` | Ask before tools, or auto-approve |
| `/model` `/models` `/effort` | Model controls |
| `/clear` | Clear conversation history |
| `/refresh` | Reset to project root + clear history |
| `/stop` | Cancel the current run |
| `/status` `/limit` `/health` | Session / usage / agent check |

Replies show **only the agent answer**. Telegram shows **typing…** while the server is working. History continues until you clear it.

---

## CLI

| Command | What it does |
|---------|----------------|
| `telecursor setup` | Interactive config |
| `telecursor start -d` | Register cwd as a project; start bot (with crash recovery) |
| `telecursor stop` | Stop the bot |
| `telecursor status` / `logs` | Status and logs |
| `telecursor projects` | List registered projects |
| `telecursor config --help` | Change settings from the terminal |
| `telecursor show` | Show config (secrets redacted) |

Crash recovery: `start -d` runs a supervisor that restarts the bot if it exits unexpectedly. `telecursor stop` ends it cleanly.

Temp uploads older than **4 hours** are cleaned automatically.

---

## Architecture

```
Telegram  →  Telecursor bot (one process)
                ├─ projects.json   (registered folders)
                ├─ per-chat session (project, mode, history id, queue)
                └─ Cursor Agent CLI (--workspace, --mode, --resume, …)
                       → your project files
```

| Piece | Role |
|-------|------|
| `main.py` | CLI + bot entry + supervisor |
| `daemon.py` | Background start/stop/status + crash recovery |
| `projects.py` | Multi-project registry |
| `handlers.py` | Telegram commands / menus |
| `agent_runner.py` | Agent subprocess + stream parsing |
| `session.py` | Per-chat state & job queue |
| `streaming.py` | Throttled Telegram replies |
| `cleanup.py` | Stale temp file removal |
| `config.py` / `.env` | Bot token, users, defaults |

Config home: project dir (dev) or `~/.config/telecursor` (Linux). Override with `TELECURSOR_HOME`.

---

## Security notes

- Whitelist only your Telegram user(s) in `ALLOWED_USERS`
- Prefer `/mode safe` unless you trust unattended runs
- Prefer `/runmode plan` or `ask` for read-only exploration
- Never commit `.env`

---

## License

MIT — see [LICENSE](LICENSE)
