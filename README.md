# HouseAgent 1.0

Windows のローカル環境で動く、不動産サイトの検索条件・観察記録・価格変化・お気に入りを管理するワークベンチです。
仕様は `document/HouseAgent_1.0_产品需求与技术设计.docx` を参照してください。

- **User-driven sign-in**: no passwords are stored; you sign in yourself in an isolated browser profile per account.
- **Permission first**: every site has a permission record (default `unknown`); automated search runs only when
  `browser_automation` is `allowed` with a recorded source. SUUMO ships **disabled** (manual sign-in, manual import
  and "open original site" work).
- **Browser-assisted import for any site**: sites that answer with human verification (e.g. at home) are browsed by
  you in a visible window; HouseAgent reads the result list you have open. A generic card reader
  (`adapters/generic/extract.py`) handles sites without a dedicated parser. Per site (Sites page) it is `auto`
  (offered once an automated run or the "check for verification" button saw verification), `on` or `off`.
  "Add a site" registers any portal by its search entry URL — no code needed. Verification is never solved or bypassed.
- **Local first**: FastAPI on `127.0.0.1` only, SQLite database, no cloud service.
- **Minimal index, traceable**: only permitted summary fields; every listing keeps its site, source ID and original URL,
  and every run records trigger, condition version and account alias.

## Architecture

| Layer | Tech | Where |
|---|---|---|
| UI | React 19 + TypeScript (strict) + Vite, Recharts, ja / zh / en | `frontend/` |
| API & rules | FastAPI, Pydantic, `/api/v1`, session token + Origin/Host checks | `backend/houseagent/api`, `services` |
| Scheduling | APScheduler triggers + a persistent priority queue | `backend/houseagent/scheduler` |
| Browser | Playwright (uses the installed Microsoft Edge) or an HTTP engine for tests | `backend/houseagent/browser_worker` |
| Site adapters | one directory per site; mock sites A/B, SUUMO (permission-gated) | `backend/houseagent/adapters` |
| Data | SQLite (WAL) + SQLAlchemy 2 + Alembic | `backend/houseagent/db`, `backend/alembic` |
| Mock property sites | 9 acceptance scenarios, served at `/mock-site/` | `backend/houseagent/mock_site` |

User data lives in `%LOCALAPPDATA%\HouseAgent\` (override with `HOUSEAGENT_DATA_DIR`):

```
database\houseagent.db   browser_profiles\<site>\<account>\   backups\   logs\   config\   exports\
```

## Requirements

- Windows 10/11, Microsoft Edge (preinstalled on Windows; used by Playwright, no browser download needed)
- Python 3.11+ and Node.js 20+ for development. The scripts use them from `PATH`, or from a portable toolchain in
  `..\tools\python` and `..\tools\node` (override with `HOUSEAGENT_TOOLS`).

## Quick start (development)

```powershell
scripts\setup.ps1          # venv + pinned Python deps, npm ci
scripts\dev.ps1            # backend :8765 (API docs /api/docs) + Vite :5173 -> open http://127.0.0.1:5173
scripts\dev.ps1 -HttpEngine  # same, but adapters use the HTTP engine instead of a real browser
```

Try the full flow with the mock sites:

1. **サイト・アカウント** → add an account for `モック不動産A`, tick "use for automated search".
2. **ログイン画面を開く** → an Edge window opens on the mock login page; enter any ID/password, close the window,
   then **ログイン状態を確認**.
3. **検索タスク** → new task (e.g. 埼玉県) → **今すぐ検索**.
4. **物件管理 / 分析レポート / 実行ログ** show the results. At `http://127.0.0.1:8765/mock-site/` you can switch
   scenarios (login expired, captcha, timeout, page change, …) or simulate price changes, then search again.

## Run from source / build for Windows

```powershell
scripts\start.ps1          # production mode from source: builds the frontend if needed, opens http://127.0.0.1:8765
scripts\stop.ps1           # graceful stop (refuses new runs, cancels running ones at a safe point)
scripts\build.ps1          # frontend build + PyInstaller -> dist\HouseAgent\HouseAgent.exe (double-click to start)
```

`HouseAgent.exe` is single-instance (a second launch just opens the existing page), falls back to a free port if
8765 is busy, and shows a tray icon (Open / Quit). See `installer/README.md` for install, upgrade and uninstall rules.

## Tests & checks

```powershell
scripts\test.ps1           # ruff format/lint, mypy, pytest (API, scenarios, migrations), tsc, vitest (i18n)
```

The backend tests start a real server on a free loopback port with a temporary data directory and drive the
mock sites through the HTTP engine, covering the nine mock scenarios, duplicate handling, price history,
NOT_FOUND ≠ sold, cross-site matching, permission blocking, retries, three-failure pause, cancellation,
interrupted-run recovery and catch-up, backup/restore, and log redaction.

Passing tests show the program behaves as designed; **they do not mean any real site permits automated access.**
Record each site's permission status and source under サイト・アカウント.

## Configuration

See `.env.example`. Settings changed in the UI (language, time zone, backups, AI …) are stored in the database.

## Optional AI

Off by default. When enabled in Settings it can summarize a listing's history via the Claude API
(default model `claude-opus-5`, server-side refusal fallback enabled). Only the fields you allow are sent; browser
sessions and credentials never are. The API key is encrypted with Windows DPAPI; otherwise the SDK's usual
credential resolution (`ANTHROPIC_API_KEY`, `ant auth login`) applies. Search, history, favorites and links work
fully with AI off.
