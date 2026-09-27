# AGENTS.md — rules for anyone (human or AI) changing HouseAgent

## Scope boundaries (spec 15.1)

V1.0 **must not** gain: automatic third-party account registration, filling in personal data, contacting agents,
booking viewings, submitting applications or transactions, a shared/public listing database, staff/organization
management, CRM, cloud sync or payments. Multi-user workspaces, licensing and team features stay as extension
points only. Any scope change needs the requirements document, data model and acceptance list updated **first** —
do not widen scope on your own.

## Hard rules

- Never store passwords, cookies, tokens or captcha values in the database, logs, exports or telemetry.
  Sessions live only in `browser_profiles\...` (restricted to the current Windows user). App secrets use DPAPI.
- Never bypass captchas, login checks, rate limits or access restrictions; they pause the run for a human.
- Automated site access only when the site's `browser_automation` permission is `allowed` **with a recorded source**.
  Only store fields the site's retention rules permit.
- `NOT_FOUND` means "not observed this time" — never mark a listing sold/deleted without explicit evidence.
- Cross-site matches are suggestions; merging always needs user confirmation. Price similarity alone is never a match.
- Site-specific selectors/URLs live only in `backend/houseagent/adapters/<site>/`. Adapters never write to the database.
- The frontend never talks to SQLite or Playwright; everything goes through `/api/v1`.
- UI text goes through i18n keys (`ja` is the default and fallback; `zh`, `en` must have the same keys).
  Business logic uses stable codes/keys, never display text. API errors return `error_code`, `message_key`,
  `correlation_id`, `details`.
- Every schema change ships an Alembic migration that runs on an empty DB and upgrades existing data
  (`tests/test_migrations.py` checks models == migrations).
- Writes of run results / sources / snapshots happen in transactions; deletes of user-facing records are soft
  deletes where history matters (tasks, notes).

## Layout

```
backend/houseagent/  api/ services/ scheduler/ browser_worker/ adapters/ db/ mock_site/ ai/  main.py launcher.py
backend/alembic/     migrations        backend/tests/  pytest suites
frontend/src/        pages/ components/ lib/ api/ i18n/locales/{ja,zh,en}.ts
scripts/             setup / dev / start / stop / test / build (PowerShell)
installer/           uninstall script and install/upgrade rules
```

## Before committing

```powershell
scripts\test.ps1
```

This runs `ruff format --check`, `ruff check`, `mypy`, `pytest` (including migration tests), `tsc` and `vitest`.
All must pass. Keep `main` startable; commit in small batches and reference the requirement ID (FR-xx / NFR-xx /
spec section) or acceptance item in the message. Real-site adapter tests must be kept separate from mock-site tests
and must never use or leak real user data.
