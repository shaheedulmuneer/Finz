# FINZ: publish with GitHub + Render

## What this release is
A working FastAPI/SQLite development MVP with an installable PWA and `/download` page. Public hosting and OAuth connections are NOT included. This is not a production-security-audited multi-tenant SaaS. Do not onboard real paying clients or upload sensitive customer data before hardening authentication, CSRF defenses, login rate limiting, recovery, monitoring, backups, and privacy controls.

## Step 1 — GitHub
1. At https://github.com/new create a **private** repository called `finz-ai-platform`. Do not initialize with a README.
2. Unzip the project. Upload the **contents** of `finz-platform` to the repository root, including `render.yaml`, `main.py`, `requirements.txt`, `static/` and `tests/`.
3. Do NOT upload `.env`, databases, virtual environments, or API secrets. `.gitignore` is supplied.
4. Commit changes. Confirm `render.yaml` appears at the repository root.

## Step 2 — Render Blueprint (recommended)
1. Connect your Render account and authorize GitHub repository access.
2. Open https://dashboard.render.com/select-repo?type=blueprint or in Render choose New > Blueprint.
3. Select the private `finz-ai-platform` GitHub repository.
4. Confirm settings from `render.yaml`. It provisions a **paid** Python Web Service with a 1 GB disk at `/var/data` for SQLite persistence. Review charges before approving.
5. `OPENAI_API_KEY` is optional: leave unset if you don't want AI requests yet, or enter it securely in Render. Never put an API key in GitHub source.
6. Click Apply / Deploy. Wait until service status is Live. The service receives a public HTTPS URL such as `https://finz-ai-studio-xxxx.onrender.com`.
7. Check `https://YOUR-ACTUAL-URL/api/health`, `https://YOUR-ACTUAL-URL/`, and `https://YOUR-ACTUAL-URL/download`.
8. Register a NEW account on the published FINZ site. Demo credentials are disabled by `DEMO_MODE=0`.

## Manual Web Service alternative
- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health check: `/api/health`
- Plan: paid service that supports persistent disks
- Disk mount path: `/var/data`, 1 GB
- Environment variables: `DATABASE_PATH=/var/data/finz.sqlite3`, `COOKIE_SECURE=1`, `DEMO_MODE=0`
- Optional: `OPENAI_API_KEY` as a secret

## App installation page
- `/download` explains Mac, Windows, iPad, iPhone and Android installation via browser.
- This is a Progressive Web App (PWA), not separate App Store/Google Play/native installers.
- Do not advertise .exe/.dmg/.apk/.ipa files as existing; no such binaries are built.

## Important cautions
- Render Free has an ephemeral filesystem. This version writes to SQLite; hosting without a disk **can erase accounts and data at every redeploy/restart**. Render persistent disks require paid compute.
- Automated Meta/TikTok/Google connections are future work and require official provider authorization.
- Test all features with synthetic data first. Add secure production operations, backups, verified email, password reset, protection from automated abuse, and a managed database before accepting clients.
