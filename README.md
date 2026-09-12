# Telecursor

**[wivrix.com](https://wivrix.com)** · [GitHub](https://github.com/wivrix/telecursor)

A private Telegram bridge for the local [Cursor Agent CLI](https://cursor.com/docs/cli).

Message your bot from anywhere. Telecursor runs the agent on **your** machine (or VPS), streams the reply back to Telegram, and keeps each conversation tied to the project you select.

```
Telegram  →  Telecursor  →  Cursor Agent CLI  →  your project files
                ↑
         streamed reply
```

---

## Features

- **Remote coding from Telegram** — prompts, photos, and documents
- **Multiple projects** — register folders on the server; switch in chat
- **Run modes** — `agent` (full tools), `plan` (planning), `ask` (Q&A)
- **Approvals** — `safe` (confirm tools) or `yolo` (auto-approve)
- **Conversation memory** — continues until you clear history; restored after restarts via `state.json`
- **Job queue** — follow-ups wait if a run is already in progress
- **Background service** — crash recovery supervisor on `start -d`
- **Clean replies** — Telegram shows typing while the agent works; messages contain the answer only

---

## Requirements

| Requirement | Notes |
|-------------|--------|
| Python 3.10+ | Used for the Telecursor process |
| [Cursor Agent CLI](https://cursor.com/docs/cli) | `agent login`, or set `CURSOR_API_KEY` |
| Telegram bot token | Create a bot with [@BotFather](https://t.me/BotFather) |
| Your Telegram user ID or `@username` | Whitelisted during setup |

---

## Install

**Linux / macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/wivrix/telecursor/main/install.sh | bash && source ~/.bashrc
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/wivrix/telecursor/main/install.ps1 | iex
```

The installer clones the repo (default `~/telecursor`), creates a virtualenv, and places `telecursor` on your `PATH`.

---

## Quick start

1. **Configure the bot** (token, allowed users, defaults):

   ```bash
   telecursor setup
   ```

2. **Start from a project folder**:

   ```bash
   cd /path/to/your/project
   telecursor start -d
   ```

3. **Open Telegram**, message your bot, and send `/start`.

You should see a compact keyboard: **Menu**, **Status**, **Clear history**, and **Refresh**.

---

## Multiple projects

One Telecursor process can serve many folders. Each `telecursor start -d` **registers the current directory** as a project. If the bot is already running, it only adds the new project—no second process is needed.

```bash
cd ~/apps/api && telecursor start -d      # starts the bot and registers "api"
cd ~/apps/web && telecursor start -d      # registers "web" (bot already running)
```

In Telegram, open **Menu → Projects** (or send `/projects`) and pick the folder you want. Each chat keeps its own project selection and conversation history, so you can work on several projects at once from different chats or by switching mid-session.

```bash
telecursor projects                 # list registered projects
telecursor projects remove <id>     # unregister one
```

---

## Using Telegram

### Chat keyboard

| Button | Action |
|--------|--------|
| **Menu** | Opens the full control panel |
| **Status** | Shows project, modes, queue, and history state |
| **Clear history** | Starts a fresh agent conversation |
| **Refresh** | Resets to the project root and clears history |

### Menu panel

| Item | Purpose |
|------|---------|
| **Projects** | Switch among registered folders |
| **Run mode** | `agent` · `plan` · `ask` |
| **Approvals** | `safe` · `yolo` |
| **Model** / **Effort** | Cursor model and thinking effort |
| **Path** | Subfolder inside the active project |
| **Limit** | Remaining Cursor usage |
| **Queue** / **Stop** | Pending jobs and cancel current run |
| **Health** / **Help** | Agent install check and command list |

### Commands

| Command | Description |
|---------|-------------|
| `/projects` | List or select a project |
| `/runmode agent\|plan\|ask` | Set Cursor execution mode |
| `/mode safe\|yolo` | Tool approval policy |
| `/model <id>` | Set model (`auto` to reset) |
| `/models` | List available models |
| `/effort <level>` | `low` · `medium` · `high` · `xhigh` · `max` · `auto` |
| `/workspace <path>` | Working path inside the selected project |
| `/clear` | Clear conversation history |
| `/refresh` | Reset path + clear history |
| `/stop` | Cancel the active run |
| `/queue` | Show queued jobs (`/queue clear` drops pending) |
| `/status` | Session summary |
| `/limit` | Cursor usage |
| `/health` | Agent binary and login check |

Replies contain **only the agent’s answer**—no model banners or completion footers. While a run is in progress, Telegram shows the **typing** indicator.

---

## CLI reference

| Command | Description |
|---------|-------------|
| `telecursor setup` | Interactive configuration wizard |
| `telecursor setup -d` | Setup, then start in the background |
| `telecursor start -d` | Register the current folder; start the bot if needed |
| `telecursor stop` | Stop the background bot (and supervisor) |
| `telecursor status` | Process status, PID, and uptime |
| `telecursor logs` | Recent logs (`-f` to follow) |
| `telecursor projects` | List registered projects |
| `telecursor projects remove <id>` | Unregister a project |
| `telecursor config --help` | Update settings from the terminal |
| `telecursor show` | Print config with secrets redacted |
| `telecursor install` | Reinstall the `telecursor` command on `PATH` |

### Background behaviour

- `telecursor start -d` starts a **supervisor** that restarts the bot if it crashes.
- `telecursor stop` signals a clean shutdown and stops restarts.
- Temporary uploads older than **four hours** are deleted automatically.

### Configuration location

| Situation | Config directory |
|-----------|------------------|
| Running from a git checkout | Project folder |
| Typical Linux install | `~/.config/telecursor` |
| macOS | `~/Library/Application Support/telecursor` |
| Windows | `%APPDATA%\telecursor` |

Override with `TELECURSOR_HOME`. Important keys live in `.env` (created by `setup`): bot token, allowed users, default project path, agent binary, and optional API key.

---

## Architecture

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  Telegram   │────▶│  Telecursor (1 bot)  │────▶│ Cursor Agent CLI│
│  (phone/PC) │◀────│                      │◀────│                 │
└─────────────┘     │  • projects.json     │     └────────┬────────┘
                    │  • state.json        │              │
                    │  • per-chat session  │              ▼
                    │  • queue + streaming │     your project files
                    └──────────────────────┘
```

| Module | Responsibility |
|--------|----------------|
| `main.py` | CLI entrypoint, bot process, crash-recovery supervisor |
| `daemon.py` | Background start / stop / status / logs |
| `projects.py` | Multi-project registry (`projects.json`) |
| `handlers.py` | Telegram commands and menus |
| `keyboards.py` | Reply and inline keyboard builders |
| `job_worker.py` | Per-chat job queue and agent run orchestration |
| `agent_runner.py` | Agent subprocess, stream parsing, approvals |
| `session.py` | Per-chat state with durable `state.json` persistence |
| `streaming.py` | Throttled Telegram message updates |
| `cleanup.py` | Stale temp-file removal |
| `config.py` | Settings loaded from `.env` |

Chat selections (project, path, modes, model, effort, and agent conversation id) are saved to `state.json` so they survive bot restarts. **Live job queues are in-memory only** and are discarded if the bot process restarts.

---

## Security

Telecursor is designed for **private** use with a whitelist—not as a public bot.

- Allow only your accounts in `ALLOWED_USERS`
- Prefer `/mode safe` unless you trust fully unattended runs (see [How safe-mode approvals work](SECURITY.md#how-safe-mode-approvals-work) in `SECURITY.md`)
- Use `/runmode plan` or `ask` when you want read-only exploration
- Register only project folders you intend the agent to touch
- Never commit `.env` (it is gitignored)

See [SECURITY.md](SECURITY.md) for reporting issues.

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| `telecursor: command not found` | Re-run the installer, then `source ~/.bashrc` |
| Bot offline / no replies | `telecursor status` and `telecursor logs` |
| Bot keeps restarting | Check `telecursor logs`; supervisor backs off up to 30s between crashes |
| Agent not found | Install the Cursor CLI; set `AGENT_BIN` via `telecursor config` |
| Not authenticated | Run `agent login`, or set `CURSOR_API_KEY` |
| Wrong folder | **Menu → Projects**, or `cd` there and `telecursor start -d` again |
| Queued jobs vanished | Expected after a bot restart — the queue is not persisted |
| Safe mode stuck waiting | Use `/stop`, then retry; see safe-mode notes in `SECURITY.md` |
| Queue full | Wait for runs to finish, or `/queue clear` / `/stop` (`MAX_QUEUE_SIZE` in `.env`) |

### Development checks

```bash
pip install -e ".[dev]"
pytest
```

---

## License

MIT. See [LICENSE](LICENSE).

Built by [Wivrix](https://wivrix.com).
