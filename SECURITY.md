# Security Policy

## Reporting a vulnerability

If you find a security issue in Telecursor (auth bypass, path escape, command injection, secret leakage, etc.), please **do not** open a public GitHub issue.

Contact the maintainer privately with a clear description, reproduction steps, and impact if known.

## How safe-mode approvals work

With `/mode safe` (the default), Telecursor does **not** pass `--force` to the agent. When the CLI asks for confirmation, the bot:

1. Detects structured `stream-json` approval / permission events when present
2. Falls back to heuristic matching of common `(y/n)` / “allow this command” prompts on stdout/stderr
3. Sends an Approve / Reject keyboard in Telegram (5-minute timeout → reject)
4. Writes `y` or `n` to the agent’s stdin

This is **best-effort**, not a sandbox. Missed prompts can leave a run waiting until `/stop`. Prefer `/runmode plan` or `ask` for read-oriented exploration, and only use `/mode yolo` when you accept unattended tool use on the host.

## Hardening checklist for operators

- Never commit `.env` or API tokens
- Keep `ALLOWED_USERS` minimal (only your accounts)
- Only register project folders you intend the agent to touch
- Prefer `/mode safe` and `/runmode plan` or `ask` when exploring
- Run under a dedicated OS user with least privilege
- Review agent output — the bot streams whatever the agent produces
- Remember: queued prompts are in-memory only and are lost on bot restart
