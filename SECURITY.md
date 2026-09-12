# Security Policy

## Reporting a vulnerability

If you find a security issue in Telecursor (auth bypass, path escape, command injection, secret leakage, etc.), please **do not** open a public GitHub issue.

Contact the maintainer privately with a clear description, reproduction steps, and impact if known.

## Hardening checklist for operators

- Never commit `.env` or API tokens
- Keep `ALLOWED_USERS` minimal (only your accounts)
- Only register project folders you intend the agent to touch
- Prefer `/mode safe` and `/runmode plan` or `ask` when exploring
- Run under a dedicated OS user with least privilege
- Review agent output — the bot streams whatever the agent produces
