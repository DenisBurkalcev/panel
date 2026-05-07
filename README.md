# FunPay Killer — local web panel for managing FunPay accounts

> **MVP.** This iteration ships secure account management, a chat UI for
> reading and sending messages, and the plugin contract for future
> auto-lift / auto-deliver / dashboard / seller-analytics plugins. No
> plugins ship in this MVP.

## What it is

A self-hosted, single-user web panel that runs on **your own machine** at
`http://127.0.0.1:8000`. You add as many FunPay accounts as you like and the
panel keeps each one isolated:

- a separate `golden_key` cookie per account,
- a separate `proxy` (HTTP/HTTPS/SOCKS5) per account,
- a separate `User-Agent` per account.

All sensitive fields are encrypted at rest with a local master key, and the
panel itself is protected by a local admin password (Argon2id), HttpOnly
session cookies, CSRF tokens, strict same-origin checks and a sliding-window
login throttle.

## Stack

- **Backend:** Python 3.10+, FastAPI, Uvicorn, SQLAlchemy 2.x, SQLite.
- **Crypto:** `cryptography` (Fernet for at-rest secrets), `argon2-cffi`
  (admin password hashing).
- **HTTP:** `httpx` with native HTTP/SOCKS proxy support, `beautifulsoup4`
  for parsing FunPay HTML.
- **Frontend:** React + TypeScript + Vite + Tailwind, served as static files
  by FastAPI from the same origin.
- **No containers.** No background services. No external services. Runs on
  Windows, macOS, and Linux with just Python and Node installed.

## Requirements

- Python **3.10 or newer** (3.11/3.12 recommended)
- Node.js **18 or newer** with npm
- A FunPay account: you'll paste its `golden_key` cookie value during account
  setup. (Open `funpay.com` while logged in → DevTools → Application →
  Cookies → copy the `golden_key` value.)

## Quick start

### Linux / macOS

```bash
git clone https://github.com/stalxboro-pixel/FunPayKiller228337.git
cd FunPayKiller228337

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
python run.py
```

### Windows (PowerShell)

```powershell
git clone https://github.com/stalxboro-pixel/FunPayKiller228337.git
cd FunPayKiller228337

py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
python run.py
```

`run.py` will:

1. install frontend deps (`npm ci`) the first time,
2. build the SPA into `app/static/`,
3. start the API + UI server at `http://127.0.0.1:8000`.

Open the URL in your browser. The first visit asks you to create the local
admin user — pick a strong password; this protects every encrypted credential
in your local database.

### Useful flags

```text
python run.py             # build the SPA if missing, then serve
python run.py --skip-ui   # serve the API only (placeholder UI)
python run.py --rebuild   # force a fresh SPA build
python run.py --dev       # API only — run `npm run dev` separately for HMR
```

## Configuration

`run.py` reads settings from environment variables and an optional `.env`
file. See `.env.example`. The most useful knobs:

| Variable               | Default                  | Purpose                                                                 |
| ---------------------- | ------------------------ | ----------------------------------------------------------------------- |
| `FPK_SECRET_KEY`       | auto-generated           | Master key for at-rest encryption (32-byte hex). Stored in `data/.master.key` if unset. **Back this up.** |
| `FPK_HOST`             | `127.0.0.1`              | Bind address. **Don't change** unless you understand the risk.          |
| `FPK_PORT`             | `8000`                   | TCP port.                                                               |
| `FPK_ALLOWED_ORIGIN`   | `http://127.0.0.1:8000`  | Origin allowed by the same-origin / CSRF checks.                        |
| `FPK_ENV`              | `development`            | `production` enables `Secure` cookies and disables docs.                |

## Security model

This is software that holds *your* FunPay credentials, so security is a first
class concern.

- **Localhost binding by default.** The server refuses to assume any
  non-loopback bind is intentional; if `FPK_HOST` is changed, a warning is
  logged on every start.
- **Encrypted secrets at rest.** `golden_key` and proxy URLs are stored as
  Fernet ciphertexts derived from a single master key (`FPK_SECRET_KEY` →
  SHA-256 → Fernet). The DB on its own is useless without the master key.
- **Master key file** lives at `data/.master.key`, mode `0600` on POSIX. On
  Windows, rely on user-profile ACLs. Back it up — losing it means you'll
  have to re-enter every `golden_key`.
- **Strong admin password.** Argon2id (default `argon2-cffi` parameters).
  Online brute-force is throttled to 10 attempts / 5 minutes per IP.
- **Authenticated session.** 256-bit token, HttpOnly + SameSite=Strict cookie,
  rotated on logout, expires after 8h of inactivity.
- **CSRF.** Synchronizer-token pattern (`fpk_csrf` cookie + `X-CSRF-Token`
  header) plus a strict `Origin`/`Referer` check on every mutation.
- **OWASP Secure Headers.** `Content-Security-Policy` (no remote origins, no
  inline scripts), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy` denying camera / mic /
  geolocation / payment.
- **Strict input validation.** Pydantic models enforce shape and length on
  every endpoint; account labels, proxy URLs, and user-agents have explicit
  regexes.
- **No outbound traffic except FunPay.** `trust_env=False` on the httpx
  client so ambient `HTTP_PROXY`/`HTTPS_PROXY` don't leak requests.
- **Defensive parsing.** Every HTML scrape is wrapped in error handling so a
  FunPay UI change never crashes the panel.

### Threat model in plain language

- **In scope:** local admin account compromise, DB exfil without the master
  key, CSRF / clickjacking from another tab, accidental public exposure on
  `0.0.0.0`, brute-force of the admin password, dependency CVEs.
- **Out of scope:** an attacker with full local privilege on the host where
  the panel runs (they can read the master key file and do whatever they
  want). Use disk encryption (FileVault / BitLocker / LUKS) to mitigate this.

If you want to share the panel between machines, ship the same
`FPK_SECRET_KEY` securely (out-of-band) and copy `data/funpay.sqlite3`.

## Plugins

The MVP **does not ship any plugins**. The plugin contract is defined in
`app/plugins/base.py` and the registry in `app/plugins/registry.py`. See
[`app/plugins/README.md`](app/plugins/README.md) for the planned interface
(auto-lift, auto-deliver, market dashboards, seller analytics from buyer
reviews).

## Development

```bash
# back-end tests
pytest -q

# back-end lint / typecheck
ruff check .
mypy app

# front-end dev server (with API proxy to localhost:8000)
cd frontend
npm install
npm run dev
```

## Project layout

```
app/
├── __main__.py        # python -m app
├── main.py            # FastAPI app factory + security middleware
├── config.py          # Pydantic settings + master-key bootstrap
├── db.py              # SQLAlchemy engine + Base + session
├── crypto.py          # Fernet encrypt/decrypt helpers
├── security.py        # Auth / sessions / CSRF / rate limit
├── models/            # ORM models (AdminUser, Account)
├── schemas/           # Pydantic DTOs
├── routers/           # FastAPI routers (auth, accounts, chats, plugins)
├── services/
│   ├── funpay_client.py    # Per-account async FunPay HTTP client
│   └── account_service.py  # DB <-> client glue
├── plugins/           # Plugin interface (no plugins ship in MVP)
└── static/            # Built SPA goes here (gitignored)

frontend/              # Vite + React + TS + Tailwind SPA
tests/                 # Pytest smoke tests (encryption, auth, validation)
run.py                 # Cross-platform launcher
```

## Disclaimer

FunPay does not publish an official API. This project talks to FunPay's
website through the same HTML / `/runner/` endpoint that a logged-in browser
uses. FunPay can change those at any time and that may temporarily break
parts of the panel. Use at your own risk and respect FunPay's Terms of
Service.
