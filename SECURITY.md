# Security

For anyone who found a security problem in tgmcp, or wants to know what counts as one.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting: the repository's "Security" tab, "Report a vulnerability". Include what you saw, the steps to reproduce, and your Windows and Python versions. Never include a real `secrets.bin`, session string or chat content. You will get an answer within a week.

## Scope

In scope: anything that lets tgmcp write to the Telegram account, leak the login key or the API hash, write chat text to disk, or reach a network destination other than Telegram's servers and a model server on this computer.

Out of scope: the security of the Telegram account itself (use two-step verification and check Settings, Devices in the Telegram app), a modified copy of tgmcp, and a patched Telethon.

## Install only from this repository

The name `telegram-mcp` on PyPI belongs to an unrelated package: `pip install telegram-mcp` installs that package, not this one. Install tgmcp from a clone of this repository with the hashed lock files, as [docs/setup.md](docs/setup.md) says. Be suspicious of any other source that claims to be this tool.

## Threat model

What tgmcp protects, against what, and its limits: [docs/privacy.md](docs/privacy.md#threat-model).
