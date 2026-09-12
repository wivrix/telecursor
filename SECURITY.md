# Security Policy

## Reporting a vulnerability

If you find a security issue in Telecursor (auth bypass, path escape, command injection, secret leakage, etc.), please **do not** open a public GitHub issue.

Contact the maintainer privately with a clear description, reproduction steps, and impact if known.

## How safe-mode approvals work

- **`/mode safe`** (default): when the agent asks for confirmation, Telecursor shows **Approve / Reject** buttons in Telegram.
- Detection uses structured `stream-json` approval events when available, plus heuristic matching of common `(y/n)` / “allow this command” text on stdout/stderr.
- Approvals time out after **5 minutes** and are treated as **reject** (`n` on stdin).
- **`/mode yolo`**: passes `--force` to the agent and skips Telegram approval prompts.
- Prefer **safe** plus `/runmode plan` or `ask` when exploring unfamiliar code; YOLO is for trusted unattended runs only.

This is **best-effort**, not a sandbox. Missed prompts can leave a run waiting until `/stop`.

## Hardening checklist for operators

- Never commit `.env` or API tokens
- Keep `ALLOWED_USERS` minimal (only your accounts)
- Only register project folders you intend the agent to touch
- Prefer `/mode safe` and `/runmode plan` or `ask` when exploring
- Run under a dedicated OS user with least privilege
- Review agent output — the bot streams whatever the agent produces
- Remember: queued prompts are in-memory only and are lost on bot restart
