# Changelog

All notable changes to AgentsWatch are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 2.2.0 — 2026-11-11

### Added

- New English README as the default entry point, plus a full Chinese
  translation in `README.zh-CN.md`, both with a generated brand banner,
  capability matrix, six project highlights, a refreshed architecture diagram,
  port map, and a single end-to-end quick start.
- `frontend/src/runtimeMode.js`: explicit demo-mode policy and last-sync
  formatting, covered by a dedicated Node test suite.
- Nginx security headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, HSTS) for standalone and combined
  ingress; the management route also gets a same-origin CSP.
- Ruff configuration and a CI lint job; `docker compose config --quiet` now
  runs in CI.
- `frontend/.nvmrc` and an `engines.node` constraint.
- GitHub issue and pull-request templates, a `NOTICE` file, and a supported
  version table in `SECURITY.md`.

### Changed

- **Production builds no longer fall back to synthetic demo data.** A failed
  management API now renders a blocking “无法连接管理 API” page. Demo data is
  limited to Vite dev builds or an explicit `VITE_ALLOW_DEMO=true` opt-in.
- Header environment/freshness now reflect the real data source and the last
  successful sync instead of a hard-coded “生产环境 / 更新于 2 分钟前”.
- Management production Compose takes the host data root from
  `AGENTSWATCH_MANAGEMENT_DATA_HOST_ROOT` (default
  `/srv/agentswatch/management`) instead of hard-coding `/data`, matching the
  deployment guide and entrypoint checks.
- `vite` and `@vitejs/plugin-react` moved to `devDependencies`.
- Unused imports, dead variables, and lambda assignments removed; ruff passes
  the `E4/E7/E9/F` rule set.

### Fixed

- The gateway-token JSON5 test now creates a self-contained fake OpenClaw
  installation instead of depending on `openclaw` being present on the CI
  runner. This was the only failure in the first GitHub Actions run.

### Removed

- `frontend/AGENTS.md` is no longer tracked or shipped in release artifacts.
  Internal prototype notes stay local and are ignored by Git.
- Development-server host allow-list no longer references a workstation name.

### Security

- Removed site-specific identifiers from the public release scanner. The
  public rules now cover generic private networks, workstation paths, private
  keys, and common token formats; site-specific terms are supplied through an
  untracked private denylist (`--denylist` / `AGENTSWATCH_RELEASE_DENYLIST`).
- Replaced the literal dummy password used for timing equalization with a
  randomly generated value.
- Sanitized remaining generic department names in fixtures and documentation.
- Confirmed the published tree contains no real API keys, account passwords,
  private keys, tokens, local inventories, or local data directories.
- Release artifacts are generated from a single-commit tree so no private
  history is published; see `scripts/check_public_release.py --history`.

## 2.1.1 — 2026-09-04

- Removed the tracked runtime inventory, generated Prometheus target file, and site-specific migration snapshots and tooling.
- Replaced server, runtime, agent, organization, owner, endpoint, and credential examples with deterministic synthetic data.
- Replaced the fixed local PostgreSQL password with mode-`0600` generated secret files and no-login bootstrap roles.
- Added a fail-closed public-release scanner for source files, Office fixtures, prohibited paths, common key formats, and reachable Git history.
- Added GitHub Actions checks for release hygiene, Python tests, frontend tests, and the production frontend build.
- Unified the public package and service version at `2.1.1`.
- Added public release, contribution, and security documentation; generalized deployment examples and documented which local state must stay outside Git.
- Licensed the project under the Apache License 2.0.

### Known constraints

- The frontend production build still emits a chunk-size warning and should be split before the UI grows further.
- The management task runner remains single-worker, and the observability deployment remains a single-host Compose stack.
- OpenClaw versions that require plugin capability consent need an operator-reviewed Feishu plugin installation before automated agent deployment; AgentsWatch does not auto-consent to new capabilities.
