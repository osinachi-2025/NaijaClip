# NaijaClip

NaijaClip is a video clipping platform that analyzes uploaded videos and helps creators produce short, shareable clips. It includes a FastAPI web app, a background processing worker, a browser-based clip editor, and integrations for transcription, AI analysis, storage, authentication, and payments.

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

3. Configure at least these values in `.env` for local development:

   ```dotenv
   DATABASE_URL=sqlite:///naijaclip.db
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
   uvicorn app:app --reload
   ```

   Open <http://localhost:8000>.

6. In a second terminal with the same virtual environment and `.env`, start the background worker:

   ```bash
   python -m app.tasks.worker
   ```

   The worker processes uploaded videos and clip export jobs.

## Tests

Run the test suite with:

```bash
pytest
```

## Configuration

Use `.env.example` as the reference for available settings. The application defaults to Cloudflare R2 storage, so set `STORAGE_BACKEND=local` for development without R2 credentials. Video processing also requires a working FFmpeg installation and valid provider credentials. Keep uploaded media and local database files out of version control; the repository's `.gitignore` excludes them by default.
