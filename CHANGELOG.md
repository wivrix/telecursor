# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-12

### Added
- Private Telegram bridge for the local Cursor Agent CLI (aiogram 3)
- Multi-project registry (`projects.json`) with Telegram project picker
- Run modes: `agent`, `plan`, `ask`
- Tool approval modes: `safe` (Telegram Approve/Reject) and `yolo` (`--force`)
- Per-chat session persistence to `state.json` (modes, workspace, model, history id)
- In-memory per-chat job queue while an agent run is active
- Background `start -d` supervisor with crash recovery
- Offline pytest suite for workspace confinement, sessions, projects, middleware, and stream helpers

### Changed
- Split Telegram UI markups into `keyboards.py` and queue/agent orchestration into `job_worker.py`
- Supervisor restart delay uses exponential backoff (2s → 30s cap)
- Agent start logs redact / truncate user prompts more aggressively
- Safe-mode approval detection covers additional prompt and stream-json shapes

### Security
- Whitelist middleware silently drops unauthorized users
- Workspace paths confined under registered project roots
- Agent invoked via `create_subprocess_exec` (no shell)
