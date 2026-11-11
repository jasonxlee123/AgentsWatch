# Contributing to AgentsWatch

## Development environment

Use Python 3.12+, Node.js 22+, npm, and Docker with Docker Compose. Before local observability work, create an untracked inventory:

```bash
cp cluster/lobsters.example.json cluster/lobsters.json
```

Start the local stack with `./scripts/local_management_stack.sh start`; it prints local addresses, the login user, and the path to the mode-`0600` generated password file.

## Tests

Run the checks relevant to your change before opening a pull request:

```bash
python3 -m unittest discover -s tests -v
npm --prefix frontend test
npm --prefix frontend run build
docker compose config --quiet
python3 scripts/check_public_release.py --history
```

## Pull requests

Keep changes focused, explain user-visible behavior and verification, and update the adjacent documentation when configuration, deployment, or API behavior changes. Do not commit generated build output unless the repository explicitly requires it.

## Sensitive data

Never commit `.env`, `cluster/lobsters.json`, `.local-data/`, runtime data, passwords, tokens, private keys, certificates, or production DSNs. Use placeholders and `*.example` files in documentation and tests. Report suspected vulnerabilities through the private process in [SECURITY.md](SECURITY.md), not a public issue.

The public release scanner uses generic rules only. Maintainers must additionally run it with a private, untracked denylist before publishing:

```bash
python3 scripts/check_public_release.py --history --denylist .release-denylist.local
```

The denylist contains site-specific terms and must never be committed; `.gitignore` excludes `.release-denylist*`.

## License

Unless explicitly stated otherwise, contributions submitted to this project are provided under the [Apache License 2.0](LICENSE).
