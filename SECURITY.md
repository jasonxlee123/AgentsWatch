# Security Policy

## Supported versions

Security fixes are provided for the latest `2.x` release line. Older minors are
expected to upgrade to the newest patch release.

| Version | Supported |
| --- | --- |
| 2.2.x | ✅ |
| 2.1.x | ✅ (upgrade recommended) |
| < 2.1 | ❌ |

## Reporting a vulnerability

Please report security vulnerabilities through a GitHub Private Security
Advisory for this repository. Do not open a public issue or discussion for a
suspected vulnerability.

Include a concise reproduction, affected version or commit, impact, and any
safe mitigation. Do not include passwords, API keys, tokens, private keys,
customer data, production endpoints, or other secrets. If a report requires
sensitive material, provide only the minimum needed through the private
advisory channel.

We will acknowledge valid reports, assess impact, and coordinate a fix and
disclosure timeline with the reporter.

## Secret handling

Never commit `.env` files, local inventories, certificates, private keys,
tokens, passwords, production DSNs, or runtime data. If credentials are
exposed, revoke or rotate them immediately and report the exposure privately.

The repository ships `scripts/check_public_release.py`, which fails closed on
tracked files, Office fixtures, prohibited paths, common key formats, and
reachable Git history. CI runs it with `--history`; run it locally before any
public release:

```bash
python3 scripts/check_public_release.py --history
```
