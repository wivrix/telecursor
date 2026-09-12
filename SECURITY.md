# Security Policy

## Reporting a vulnerability

If you find a security issue in Telecursor (auth bypass, path jail escape, command injection, secret leakage, etc.), please **do not** open a public GitHub issue.

Email or message the maintainer privately with:

- A clear description of the issue
- Steps to reproduce
- Impact assessment (if known)

## Hardening checklist for operators

- Never commit `.env` or API tokens
- Keep `ALLOWED_USERS` minimal
- Keep `ALLOWED_WORKSPACE_PATH` as narrow as possible
- Prefer `/mode safe` on shared or production hosts
- Run under a dedicated OS user with least privilege
- Review agent tool output — the bot streams whatever the agent produces
