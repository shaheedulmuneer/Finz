# FINZ — Cross-platform Social Media Intelligence (PWA)

**App status:** Local runnable MVP with secure workspace-scoped APIs, CSV imports, competitor Reel links, audiences, saved reports, and optional OpenAI analysis. FINZ installs as a browser-based PWA on Mac, Windows, iPhone, iPad and Android **after hosting at an HTTPS URL**. It is not a native app-store package. The website includes a `/download` page and installer instructions. It cannot provide an internet URL until you deploy it.

**Start:** double-click `start_windows.bat` on Windows or `bash start_mac_linux.sh` on macOS. Go to http://127.0.0.1:8000. Go to http://127.0.0.1:8000/download for app installation guidance.

# FINZ — Working MVP

A responsive, local-first **multi-client social media intelligence dashboard**. Built with FastAPI, SQLite and vanilla HTML/CSS/JavaScript; no frontend compilation required. Works on Windows, macOS and Linux with Python 3.10+.

## What actually works

- Account registration and sign-in, seven-day HttpOnly cookie sessions and salted PBKDF2 passwords
- Separate business/client workspaces with membership checks on every data endpoint
- Summary dashboard: imported views, reach, engagement, inquiries, bookings and attributed sales
- Real CSV uploads for post / marketing performance and aggregated audience data
- Audience chart breakdowns by age, city, gender, country or interest
- Competitor watchlist, manually verified Instagram Reel URLs, posting dates, creative notes and clickable links
- Daily rules-based analysis of 7-day trends, missing booking data and recently logged competitor Reels
- Optional OpenAI API enhancement for text recommendations (requires a separately billed API key)
- A daily reporting script for Windows Task Scheduler / cron
- Responsive interface suitable for phone or desktop browsers
- Integration readiness screen for Meta, Google Business Profile, TikTok and WhatsApp

**Not implemented yet:** official OAuth connections, automatic account sync, automatic collection of competitor Reels, scheduled email/push notifications, team invitations, billing, PDF export, social media posting. They require separate API setup, approvals and/or additional development. This MVP should not be publicly deployed unchanged.

## 1. Run on Windows (easiest)

1. Extract the ZIP to a folder, for example `C:\FINZ`.
2. Install Python 3.10 or newer from python.org. During installation, tick **Add Python to PATH** if offered.
3. Double-click `start_windows.bat`. It creates `.venv`, installs dependencies and starts the server.
4. Open **http://127.0.0.1:8000** in your computer's browser.
5. For the optional demo login use **demo@finz.local / Demo123!** (the included `.env.example` enables a local demo). **All demo numbers are artificial.**
6. To use your own data, create a new account/workspace and import real CSV files.

For manual Windows commands in PowerShell:

```powershell
cd C:\FINZ
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## 2. Run on Mac or Linux

```bash
cd finz-platform
bash start_mac_linux.sh
```

Open **http://127.0.0.1:8000**.

## 3. First steps

1. Sign in or register.
2. Click **+** next to the workspace selector to create a client workspace (e.g. Client A, Restaurant B, Agency Client C).
3. Open **Data Imports** and download the two example CSVs.
4. Use the same column headers in your authorized exports and upload them. CSV values are **appended**; reimporting the same file duplicates totals.
5. Open **Competitor Reels**, add a competitor and save direct verified `https://www.instagram.com/reel/...` links.
6. Open **AI Recommendations**, click **Generate report**. Optionally enable OpenAI AI enhancement below.

## 4. CSV formats

Performance: `date,platform,post_title,views,reach,likes,comments,shares,saves,profile_visits,inquiries,bookings,revenue`

Audience: `date,platform,dimension,segment,count`

`date`: ISO `YYYY-MM-DD`; `dimension`: `age`, `city`, `gender`, `country`, or `interest`; `count`: nonnegative integer. Uploads are limited to 2 MB. Audience inputs should be aggregated and not identify people. A sample is in `examples/`.

These are normalized application templates rather than direct exports for each social network. Map columns from your platform export to the templates before importing. Revenue is in AED if you are using AED inputs. Avoid mixing currencies in a workspace.

**Analytics caveats:** summing reach across posts can double-count people. Imported rows may mix post-level and aggregate reporting; do not overlap coverage. The app displays only data supplied by the user and explicitly marks demo records.

## 5. Optional real OpenAI recommendations

Create an OpenAI API key in your own API account and set in `.env`:

```ini
OPENAI_API_KEY=your-real-key-here
OPENAI_MODEL=gpt-4.1-mini
```

Restart the server, then click **Enhance with AI** in AI Recommendations. The key stays on the server; only aggregate report information is sent to OpenAI. API usage is billed separately from ChatGPT subscriptions. Without a key, the free local rules-based recommendations still work.

## 6. Generate reports every morning at 08:00 Dubai time

First activate the environment / install dependencies. Then run:

```bash
python daily_report.py
```

It generates a dated report for each workspace and stores it in SQLite. There is **no email/push delivery** yet; open the website's Recommendations screen to see the latest saved report. If run from a machine, the machine must be on when the scheduler executes.

**Windows Task Scheduler**: Create Basic Task → Daily → 8:00 AM (ensure computer time zone is `Asia/Dubai`, or translate time) → Start a program, program path `C:\FINZ\.venv\Scripts\python.exe` → arguments `daily_report.py` → Start in `C:\FINZ`.

**Linux cron**, for a host configured to Dubai time:

```cron
0 8 * * * cd /path/to/finz-platform && /path/to/finz-platform/.venv/bin/python daily_report.py >> /path/to/finz-platform/daily.log 2>&1
```

The preexisting ChatGPT scheduled competitor report is **separate**; it does not automatically write to this app.

## 7. Where data goes

SQLite database: `data/finz.sqlite3` (created when the app runs). Back up this file securely. To remove demo data safely, use a new database with `DEMO_MODE=0` rather than changing tables by hand.

## 8. Before hosting for paying clients

This is a local functional MVP. For internet-facing agency/SaaS use, first:

- Disable demo mode and delete demo credentials/database; use a strong unique app secret and `COOKIE_SECURE=1` on HTTPS.
- Implement CSRF tokens, rate limits on sign-in, email verification, password reset, per-user roles/invitations, 2FA, account deletion and audit logs.
- Move to hosted PostgreSQL with encrypted backups, tenant-aware database access, migrations and secret management.
- Add secure OAuth flows, encrypted per-client refresh tokens, permission audits and revocation handling.
- Implement tenant billing, usage quotas, privacy notice, data retention, opt-outs and legal compliance for each jurisdiction.
- Add monitored background jobs, idempotent imports, audit trails and properly validated KPI definitions.
- Perform professional security and privacy review before accepting external customer data.

## 9. Developer API

Interactive API documentation is available at **http://127.0.0.1:8000/api/docs** once running. Routes under `/api/workspaces/{id}/...` are permission checked.

## 10. Tests

From the project directory with dependencies installed:

```bash
python -m unittest discover -s tests -v
```

## Suggested development roadmap

1. Production-grade auth and a secure hosted database.
2. Official Meta OAuth and Insights connector for authorized accounts.
3. Google Business Profile Performance connector and booking events.
4. Scheduled source sync with data provenance and retry/error logs.
5. Competitor Business Discovery or licensed provider, subject to platform permissions.
6. Customer conversion attribution, notification delivery, and client-facing PDF reports.

This is an independently runnable project — not a screenshot, Figma file or mockup.
