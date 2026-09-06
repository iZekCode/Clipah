# Environment Setup Guide

Two codebases live in this repository at once, and they are configured completely
differently. Read the section for the one you are running.

- **[The rebuild](#the-rebuild-backend--frontend)** — `backend/` (FastAPI) and `frontend/`
  (Next.js). Settings take the `CLIPAH_` prefix and live in `backend/.env`. This is the
  only code receiving new features.
- **[The legacy stack](#the-legacy-flask-stack)** — `app.py`, `templates/`, `static/`. It
  reads unprefixed variables from a `.env` at the repository root and stays in place until
  the cutover in Task 48.

---

# The rebuild (backend + frontend)

## What you need first

| Tool | Why |
| --- | --- |
| [`uv`](https://docs.astral.sh/uv/) | Manages the Python 3.13 environment and lockfile. Never call `pip` or a bare `python`. |
| [`pnpm`](https://pnpm.io/) | Manages the frontend workspace. |
| Docker | Runs Postgres, Redis, and MinIO locally. |
| FFmpeg | Media probing, proxies, and exports. |

## Step 1: Start the local infrastructure

```bash
docker compose -f infra/compose.yaml up -d
```

Three services, all bound to loopback only:

| Service | Address | Notes |
| --- | --- | --- |
| Postgres 17 | `127.0.0.1:55433` | database `clipah_rebuild_foundation` |
| Redis 7.4 | `127.0.0.1:56380` | |
| MinIO | `127.0.0.1:59001` (console `59002`) | user `clipah_local`, password `clipah_local_secret` |

`infra/postgres/init-runtime.sql` provisions the database roles on first start. The
migration owner is `clipah_migrator`; the application connects as the least-privilege
logins `clipah_api_runtime` and `clipah_worker_runtime`.

**Create the object-store bucket once.** Compose does not create it for you:

```bash
docker exec -it clipah-rebuild-foundation-minio-1 \
  mc alias set local http://127.0.0.1:9000 clipah_local clipah_local_secret
docker exec -it clipah-rebuild-foundation-minio-1 mc mb local/clipah
```

## Step 2: Write `backend/.env`

Every setting takes the `CLIPAH_` prefix. The file is gitignored. Start from the annotated
draft already in the repository, or write your own — the fields that must be filled in are
listed under [Credentials](#step-3-credentials) below.

**One trap worth knowing.** `CLIPAH_ENVIRONMENT=local` *inside* the file does nothing on
its own. `backend/src/clipah/config.py` only consults `.env` after a real environment
variable or an explicit argument has already selected the local profile, so that a
production process can never be reconfigured by a file someone dropped beside it. Export
it in your shell:

```bash
export CLIPAH_ENVIRONMENT=local
```

Without that, the file is ignored entirely and every setting silently falls back to its
default.

## Step 3: Credentials

### Google sign-in (required — there is no other way in)

The rebuild authenticates every request through a Session created by Google OIDC. Without
these three values nobody can sign in at all.

1. **Create a project** at <https://console.cloud.google.com/projectcreate>, then select it
   in the top bar.
2. **Configure the consent screen** at <https://console.cloud.google.com/auth/branding>:
   user type **External**, an app name, and your own address for both support and developer
   contact. Leave it in **Testing**, and add your Google account under **Audience → Test
   users** — in Testing mode only listed accounts may sign in.
3. **Scopes need no configuration.** The application requests exactly `openid`, `email`,
   and `profile` (`backend/src/clipah/auth/google_oidc.py`). All three are non-sensitive,
   so Google requires no verification review.
4. **Create the client** at <https://console.cloud.google.com/apis/credentials>:
   **Create credentials → OAuth client ID → Web application**.
   - Leave *Authorized JavaScript origins* **empty**. This is a server-side
     authorization-code flow with PKCE; no browser SDK is involved.
   - Add exactly one *Authorized redirect URI*:

     ```
     http://localhost:3000/api/v1/auth/google/callback
     ```

**That URI points at the Next dev server, not the API, and the distinction matters.** The
browser only ever talks to its own origin; `frontend/next.config.mjs` rewrites `/api/*` to
FastAPI, which is what keeps the Session cookie first-party and lets the CSRF `Origin`
check see a same-origin request. Sending Google to port 8000 instead would set the cookie
on the wrong origin, and sign-in would appear to succeed and then silently fail. Google
compares the string literally, so `127.0.0.1` will not match `localhost`. Plain `http` is
accepted because Google exempts localhost; the configuration enforces HTTPS in production
only.

```env
CLIPAH_GOOGLE_OIDC_CLIENT_ID=<...apps.googleusercontent.com>
CLIPAH_GOOGLE_OIDC_CLIENT_SECRET=<GOCSPX-...>
CLIPAH_GOOGLE_OIDC_REDIRECT_URI=http://localhost:3000/api/v1/auth/google/callback
```

### Session secret (required)

At least 32 characters. It signs Session material and seals generated-media confirmation
tokens, so rotating it invalidates any estimate a member is currently holding.

```bash
openssl rand -hex 32
```

### The AI pipeline (required to process anything)

| Variable | Purpose | Where |
| --- | --- | --- |
| `CLIPAH_ASSEMBLYAI_API_KEY` | Transcription and speaker diarization | <https://www.assemblyai.com/dashboard/> |
| `CLIPAH_GROQ_API_KEY` | Highlight extraction and reranking | <https://console.groq.com/keys> |

### Stock B-roll (optional)

| Variable | Where |
| --- | --- |
| `CLIPAH_PEXELS_API_KEY` | <https://www.pexels.com/api/> |
| `CLIPAH_PIXABAY_API_KEY` | <https://pixabay.com/api/docs/> |

An absent credential means that provider is simply unavailable. It is never a startup
failure: a Workspace may run on its own footage alone.

### Generated B-roll (optional)

Leave `CLIPAH_FAL_API_KEY` blank and generation reports itself as unavailable, which is a
normal state. If you do set it, **you must also set `CLIPAH_FAL_WEBHOOK_BASE_URL`** — the
configuration refuses a key without an origin and an origin without a key. It must be a
bare HTTPS origin with no path or query; locally any valid origin will do, because the
durable webhook sink is not wired yet and completion is detected by polling.

```env
CLIPAH_FAL_API_KEY=<https://fal.ai/dashboard/keys>
CLIPAH_FAL_WEBHOOK_BASE_URL=https://clipah.test
```

Generated video stays disabled behind `CLIPAH_GENERATIVE_VIDEO_ENABLED=false` until a
deployment turns it on deliberately. Runway is an optional second video provider and needs
all three of `CLIPAH_RUNWAY_API_SECRET`, `CLIPAH_RUNWAY_VIDEO_MODEL_ALIAS`, and
`CLIPAH_RUNWAY_VIDEO_MODEL_ID`, or none of them. `docs/operations/generative-media.md`
covers costs, quotas, safety, and how to turn generation off in a hurry.

**No configuration path accepts a model alias or provider model ID containing `sora`, in
any capitalization.** Startup fails rather than accepting one.

## Step 4: Install and migrate

```bash
pnpm install                       # from the repository root; installs the frontend
cd backend && uv sync --all-extras
uv run alembic upgrade head
```

## Step 5: Run it

There is no importable ASGI application yet: `create_app` takes its settings as an
argument, and `uvicorn` is in no lockfile, because every test drives the application in
process. Serving the API as a real process is Task 46's work. Until then, run it through a
one-off entrypoint:

```bash
# terminal one, from backend/
CLIPAH_ENVIRONMENT=local uv run --with uvicorn python -c "
import uvicorn
from clipah.api.app import create_app
from clipah.config import Settings
uvicorn.run(create_app(Settings()), host='127.0.0.1', port=8000)
"
```

```bash
# terminal two, from the repository root
pnpm dev
```

Open <http://localhost:3000>. Check the API on its own with
`curl http://127.0.0.1:8000/health/ready` — it answers `503` when a dependency it needs is
unreachable, which is the fastest way to find a compose service that did not come up.

## Step 6: Confirm the configuration loads

```bash
cd backend
CLIPAH_ENVIRONMENT=local uv run python -c "
from clipah.config import Settings
s = Settings()
print('redirect  :', s.google_oidc_redirect_uri)
print('google    :', bool(s.google_oidc_client_id))
print('pipeline  :', bool(s.assemblyai_api_key), bool(s.groq_api_key))
"
```

A `ValidationError` here is the configuration refusing to start on something it cannot
honour — a half-configured provider, a bound that cannot be satisfied — rather than
failing later against a real request. The message names the setting.

## Verification gates

All four backend commands run from `backend/`, and all four must pass:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q --cov=clipah --cov-fail-under=90
```

A task touching `frontend/` runs these four from the repository root:

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Settings all show defaults; `.env` seems ignored | `CLIPAH_ENVIRONMENT=local` is not exported in the shell. |
| `redirect_uri_mismatch` from Google | The registered URI differs from `CLIPAH_GOOGLE_OIDC_REDIRECT_URI`. Compare literally, including port and trailing slash. |
| `access_denied`, or "app not verified" | Your account is not in the consent screen's Test users. |
| Sign-in appears to work, then you are signed out | You reached the API's port directly instead of `localhost:3000`. |
| `fal credentials require complete webhook and model configuration` | `CLIPAH_FAL_API_KEY` is set without `CLIPAH_FAL_WEBHOOK_BASE_URL`. |
| Uploads answer `503` | The MinIO bucket was never created. See Step 1. |
| Tests cannot reach Postgres | The compose stack is not up, or a previous stack is holding the ports. |

## Security notes

- Never commit `backend/.env`. `.env*` is already gitignored.
- Secrets are never stored in a recoverable form where a hash will do, and provider
  credentials, raw payloads, and ephemeral output URLs never reach Postgres, Redis, logs,
  Job events, or an API response.
- Use different credentials for local, staging, and production.

---

# The legacy Flask stack

Everything below configures `app.py` and the templates it serves, from a `.env` at the
repository **root** with unprefixed variable names. It is untouched until the cutover in
Task 48 and receives no new features. If you are working on the rebuild, none of it
applies to you.

## API Keys Configuration

This application requires API keys from two services:

### 1. AssemblyAI API Key
- **Purpose**: Audio transcription and speaker diarization
- **Get your key**: Visit [AssemblyAI Dashboard](https://www.assemblyai.com/dashboard/)
- **Environment variable**: `ASSEMBLYAI_API_KEY`

### 2. Google Gemini AI API Key
- **Purpose**: Video content analysis and clip generation
- **Get your key**: Visit [Google AI Studio](https://aistudio.google.com/app/apikey)
- **Environment variable**: `GROQ_API_KEY`

## Setup Instructions

### Step 1: Set up Python Virtual Environment (Recommended)

A virtual environment isolates your project dependencies from your system Python installation.

**For Windows (PowerShell/Command Prompt):**
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
venv\Scripts\activate

# To deactivate later (when you're done working)
deactivate
```

**For macOS/Linux:**
```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# To deactivate later (when you're done working)
deactivate
```

**Verify virtual environment is active:**
- Your terminal prompt should show `(venv)` at the beginning
- Run `python --version` to confirm you're using the correct Python version

### Step 2: Configure Environment Variables

1. **Copy the environment template**:
   ```bash
   cp .env.example .env
   ```

2. **Edit the `.env` file** and replace the placeholder values with your actual API keys:
   ```env
   ASSEMBLYAI_API_KEY=your_actual_assemblyai_key_here
   GROQ_API_KEY=your_actual_groq_api_key_here
   ```

### Step 3: Install Python Dependencies

Make sure your virtual environment is activated (you should see `(venv)` in your terminal prompt), then install the required packages:

```bash
pip install -r requirements.txt
```

### Step 4: Run the Application

```bash
python app.py
```

The application will start on `http://localhost:5000` by default.

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ASSEMBLYAI_API_KEY` | ✅ Yes | - | AssemblyAI API key for transcription |
| `GROQ_API_KEY` | ✅ Yes | - | Groq API key |
| `FLASK_DEBUG` | No | True | Enable/disable Flask debug mode |
| `FLASK_ENV` | No | development | Flask environment (development/production) |
| `FLASK_HOST` | No | 0.0.0.0 | Flask server host |
| `FLASK_PORT` | No | 5000 | Flask server port |

## Security Notes

- ⚠️ **Never commit your `.env` file to version control**
- The `.env` file is already included in `.gitignore`
- Keep your API keys secure and rotate them regularly
- Use different API keys for different environments (dev/staging/prod)

## Virtual Environment Best Practices

### Why use a virtual environment?
- **Isolation**: Keeps project dependencies separate from system Python
- **Version Control**: Ensures consistent package versions across different machines
- **Clean Development**: Prevents conflicts between different projects
- **Easy Deployment**: Makes it easier to replicate the environment on production servers

### Managing your virtual environment:
```bash
# Always activate before working on the project
venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux

# Check installed packages
pip list

# Update requirements.txt if you install new packages
pip freeze > requirements.txt

# Deactivate when you're done working
deactivate
```

## Troubleshooting

### Common Issues:

**1. Python command not found:**
- Make sure Python is installed and added to your system PATH
- Try `python3` instead of `python` on macOS/Linux

**2. Virtual environment activation fails:**
- On Windows, you might need to change execution policy:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```

**3. Permission denied errors:**
- Run terminal as administrator (Windows) or use `sudo` (macOS/Linux) if needed
- Make sure you have write permissions in the project directory

**4. Package installation fails:**
- Upgrade pip first: `pip install --upgrade pip`
- If specific packages fail, try installing them individually

**5. API key errors:**
- Double-check that your `.env` file is in the project root directory
- Ensure there are no extra spaces around the `=` sign in your `.env` file
- Verify your API keys are valid and have the necessary permissions
