# Security

This project logs into the owner's own Telegram account and exposes read-only
tools to an MCP client on the same computer. The things worth protecting are
the Telegram session file and the API hash; the project never stores chat
text and never sends anything.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's
private vulnerability reporting on this repository ("Security" tab →
"Report a vulnerability"). You will get an answer within a week.

## Scope

In scope: anything that lets the server write to the account, leak the
session or the API hash, write chat text to disk, or reach a network
destination other than Telegram.

Out of scope: the security of the Telegram account itself (use two-factor
authentication and review "Active sessions" in the Telegram app).
