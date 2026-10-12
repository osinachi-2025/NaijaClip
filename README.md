# NaijaClip

NaijaClip is a video clipping platform that analyzes uploaded videos and helps creators produce short, shareable clips. It includes a FastAPI web app, a Redis-backed Celery worker pool, a browser-based clip editor, and integrations for transcription, AI analysis, storage, authentication, and payments.

## Architecture

The application now uses:

- FastAPI for the API and upload endpoints
- SQLAlchemy with the existing production database as the authoritative job source of truth
- Redis as the Celery broker
- Celery workers that claim and process the same database-backed jobs created by the API
- The existing pipeline and clip export functions without rewriting the processing stages

The old database-polling worker is available only with the explicit `NAIJACLIP_ENABLE_LEGACY_WORKER=1` opt-in. Both `python -m tasks.worker` and the compatibility alias `python -m app.tasks.worker` enforce that gate. This does not stop a legacy process that was already running before the migration; inspect and stop old services during rollout.

## Features

- Video upload, validation, and processing jobs
- Transcript-based clip suggestions and automated subject tracking
- Clip editing and export
- User accounts, email verification, password recovery, and Google sign-in
- Subscription billing with Paystack
- Local media storage or Cloudflare R2 object storage

## Requirements

- Python 3.11 or newer
- FFmpeg and FFprobe available on your `PATH`
- Redis reachable from both the API and Celery worker
- Deepgram and Groq API keys for video analysis
- Cloudflare R2 credentials when using R2 storage instead of local storage

## Local setup

1. Create and activate a virtual environment, then install the Python dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   On Windows PowerShell, activate it with `.venv\Scripts\Activate.ps1`.

2. Create your local environment file:

   ```bash
   cp .env.example .env
   ```

   On Windows, copy `.env.example` to `.env` using File Explorer or `Copy-Item`.

3. Configure the required values in `.env` for local development:

   ```dotenv
   DATABASE_URL=sqlite:///naijaclip.db
   CELERY_BROKER_URL=redis://localhost:6379/0
   WORKER_CONCURRENCY=1
   JWT_SECRET_KEY=replace-with-a-long-random-secret
   FRONTEND_URL=http://localhost:8000
   STORAGE_BACKEND=local
   DEEPGRAM_API_KEY=your-deepgram-api-key
   GROQ_API_KEY=your-groq-api-key
   ```

   Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` before the first initialization if you want an initial administrator account. Configure Google, email, and Paystack credentials only when using those integrations. Never commit `.env` or production secrets.

4. Apply database migrations:

   ```bash
   alembic upgrade head
   ```

5. Start the web application:

   ```bash
   uvicorn app.main:app --reload
   ```

   Open <http://localhost:8000>.

6. Start a local Celery worker in a second terminal:

   ```bash
   celery -A workers.celery_app worker --loglevel=info --concurrency=1
   ```

   This worker reads job records from the same database and processes them through the existing pipeline and clip export handlers.

## Required worker environment variables

The worker and API must use the same production database. Do not use SQLite for a worker on a different host. Set a reachable Redis broker URL explicitly; the code fallback to `localhost` is for local development, not production. These are configuration names and placeholders, not provisioned endpoints or credentials:

```dotenv
DATABASE_URL=<same SQLAlchemy database URL used by the API>
CELERY_BROKER_URL=<Redis URL reachable by the API and worker>
STORAGE_BACKEND=r2
R2_BUCKET_NAME=<bucket name>
R2_ENDPOINT_URL=<R2 S3-compatible endpoint>
R2_ACCESS_KEY_ID=<R2 access key>
R2_SECRET_ACCESS_KEY=<R2 secret key>
R2_PUBLIC_URL=<optional legacy media base; leave unset for private clip storage>
DEEPGRAM_API_KEY=<Deepgram API key for video-analysis jobs>
GROQ_API_KEY=<Groq API key for video-analysis jobs>
```

`R2_ENDPOINT_URL` may instead be derived from `R2_ACCOUNT_ID`. If `STORAGE_BACKEND=local`, API and worker must share the same `MEDIA_ROOT` filesystem. Video-analysis jobs require Deepgram and Groq credentials; clip-render jobs use storage and FFmpeg/FFprobe. `WORKER_CONCURRENCY` is optional when the command sets `--concurrency`, and `CELERY_RESULT_BACKEND` is optional because job state and the Celery task ID are stored in the database. Worker tasks do not require JWT, Google, or Paystack credentials. Store actual production values in a secrets file outside version control.

Dashboard clips, thumbnails, editor sources, and edited exports are streamed through owner-authorized API routes. Keep the R2 bucket private and disable public `r2.dev` or custom-domain access; featured homepage clips are streamed separately only after an administrator features them. `R2_PUBLIC_URL` is not required for dashboard playback.

## Worker startup and production EC2 guidance

Start only the Celery worker on the dedicated server. The API should also be running: its bounded recovery loop scans at startup and every 30 seconds, so dispatch and worker-loss recovery do not require a restart or Celery Beat. Update the example paths to match the installed checkout and virtual environment:

```bash
set -a
. /etc/naijaclip/worker.env
set +a
source /srv/naijaclip/.venv/bin/activate
celery -A workers.celery_app worker --loglevel=info --concurrency=1
```

For production, use a systemd unit similar to:

```ini
[Unit]
Description=NaijaClip Celery Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=naijaclip
Group=naijaclip
WorkingDirectory=/srv/naijaclip
EnvironmentFile=/etc/naijaclip/.env
ExecStart=/srv/naijaclip/.venv/bin/celery -A workers.celery_app worker --loglevel=info --concurrency=1
Restart=on-failure
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

After installation, run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable naijaclip-celery
sudo systemctl start naijaclip-celery
sudo systemctl status naijaclip-celery
journalctl -u naijaclip-celery -f
```

The worker service runs Celery only; do not add either legacy polling command to systemd, cron, or another process manager. Run `alembic upgrade head` against the shared production database before starting the new code so the dispatch lease columns exist.

## Retries, recovery, and duplicates

The job row is the durable dispatch record and remains the source of truth. The API commits a queued job before dispatch. Dispatchers atomically lease eligible rows; Redis publication failures get persisted backoff, while multiple API instances cannot acquire the same lease. A task ID (`queue_id`) identifies a Celery delivery; the processing job ID is the task argument and business identifier. The task ID is metadata, not a claim lock.

The API checks at most 100 due jobs per recovery pass, every 30 seconds by default. It retries unpublished rows and republishes queued rows whose recorded publication has remained unclaimed for `JOB_TIMEOUT_SECONDS` (30 minutes by default). A successful publish followed by a failed database update can therefore publish twice after the dispatch lease expires; the atomic `QUEUED` to `INGESTING` claim ensures only one delivery executes. The lease and recovery interval are configurable with `JOB_DISPATCH_LEASE_SECONDS` and `JOB_RECOVERY_INTERVAL_SECONDS`.

Processing attempts are bounded by each job's `max_attempts` (3 by default). Transient errors are requeued with backoff; permanent errors and exhausted attempts become terminal `FAILED` jobs. Celery late acknowledgements and worker-loss rejection are enabled. A worker heartbeat continues during long stages; after it expires plus `JOB_RECOVERY_GRACE_SECONDS` (120 seconds by default), API recovery requeues the job or marks it failed if the attempt budget is exhausted. The grace period exceeds the Celery hard task limit, and stage/completion writes are fenced by the current worker ID so a stale attempt cannot overwrite a replacement attempt. Soft and hard task limits are based on `JOB_TIMEOUT_SECONDS`.

## Tests

Run the Celery reliability tests and the full suite with:

```bash
.venv/bin/pytest -q tests/test_celery_worker.py
.venv/bin/pytest -q
```

## Configuration

Use `.env.example` as the reference for available settings. The application defaults to Cloudflare R2 storage, so set `STORAGE_BACKEND=local` for development without R2 credentials. Video processing also requires a working FFmpeg installation and valid provider credentials. Keep uploaded media and local database files out of version control; the repository's `.gitignore` excludes them by default.

## Deploying worker code safely

1. Review the code and tests, then install the release on the API and worker hosts.
2. Set the real shared database, Redis, storage, and provider configuration on each host; no production endpoints or credentials are supplied by this repository.
3. Run `alembic upgrade head` once against the shared database.
4. Identify and stop any old poller before starting Celery. Check processes with `ps -eo pid,args | grep -E '[p]ython.*(app\\.)?tasks\\.worker'` and inspect services with `systemctl list-units --type=service --all | grep -Ei 'naijaclip|worker'`. Review each match; do not stop an unidentified service.
5. Stop and disable the actual legacy unit after identifying its name: `sudo systemctl stop <identified-legacy-unit>` and `sudo systemctl disable <identified-legacy-unit>`. For a manually launched process, send `TERM` to its verified PID. These commands are guidance only and have not been run against a server.
6. Start the API and the Celery worker service. Verify the worker with `celery -A workers.celery_app inspect ping` and follow logs with `journalctl -u naijaclip-celery -f`.

## Rollback and old worker cleanup

Before a migration, verify old poller processes and services have stopped. To stop or disable the Celery service during a rollback:

```bash
sudo systemctl stop naijaclip-celery
sudo systemctl disable naijaclip-celery
```

Do not set `NAIJACLIP_ENABLE_LEGACY_WORKER=1` in production. That opt-in exists only for controlled development use; the supported production command is `celery -A workers.celery_app worker --loglevel=info --concurrency=1`.
