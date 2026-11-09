# Video Auditor — SFT Stage 1

Video Auditor is a preliminary video quality assessment application built with **Python, FastAPI, PostgreSQL, SQLAlchemy, FFmpeg/FFprobe, and Google Gemini**. It accepts a public YouTube URL, creates an audit job, downloads the source video into an isolated temporary directory, runs the configured AI assessment, validates the structured report, and stores the result.

> **Important:** the result is a preliminary assessment, not a certified technical measurement. AI can miss short defects, produce inaccurate timestamps, or misinterpret visual and audio content. Human review remains necessary for decisions that affect dataset acceptance.

## Current capabilities

- Web interface for submitting audits, viewing audit history, and inspecting reports.
- REST API and interactive Swagger documentation at `/docs`.
- PostgreSQL-backed audit queue.
- Separate worker process; long-running downloads and AI requests do not run inside the HTTP request.
- `yt-dlp`-based source acquisition with per-audit temporary directories, download timeout, and maximum file-size setting.
- Temporary local video cleanup after processing, plus cleanup of abandoned temporary directories on worker startup.
- **FFprobe technical metadata extraction** before the AI request: duration, file size, container, codec, resolution, pixel format, frame rates, bitrate, frame count, and audio stream metadata.
- **OpenCV frame sampling** across the video timeline with a configurable sample cap and resized frames. It records timestamped near-black segments, freeze-like regions, scene-change candidates, and sustained low-focus (possible blur) intervals as advisory evidence. Representative low-resolution JPEG previews are attached to findings within strict per-video count and size limits; these signals never change the final verdict by themselves.
- Technical observations are stored separately from AI-detected defects. A missing audio stream is a warning; a measured duration below five seconds is recorded as an SFT finding. These observations are evidence, not an automatic final verdict.
- If FFprobe is missing locally, processing can continue with a visible `ffprobe_unavailable` finding unless `FFPROBE_REQUIRED=true`.
- Gemini file upload and processing-state polling before analysis.
- Pydantic validation for structured JSON reports.
- Conservative verdict policy that routes insufficiently supported results to manual review.
- Bounded SDK and worker retries, including queue cooldown after Gemini HTTP 429.
- Worker leases, periodic heartbeats, stale-lease recovery, and fencing tokens that prevent an expired worker from overwriting a newer attempt.

## Processing workflow

```text
Browser
  |
  v
FastAPI -> PostgreSQL (pending)
                 |
                 v
          Worker claims job
          (row lock + lease token)
                 |
                 v
          yt-dlp download
          (isolated temp directory)
                 |
                 v
          FFprobe technical metadata
          + deterministic observations
                 |
                 v
          OpenCV bounded frame sampling
          + black/freeze-like signals
                 |
                 v
          Gemini file upload
          and processing wait
                 |
                 v
          Structured AI report
          + conservative policy
                 |
                 v
          PostgreSQL (completed/failed)
                 |
                 v
          Delete local temporary files
```

Technical metadata and findings are available from the audit API response as `technical_metadata` and `technical_findings`. OpenCV sampling statistics and timestamped visual observations are exposed as `visual_metadata` and `visual_findings`. Selected findings may contain `evidence.frames` with a timestamp, MIME type, and small base64 JPEG preview for manual inspection. The local video is not intended to be retained as an application asset. The processor deletes the per-audit working directory in a `finally` block. A worker startup cleanup removes old abandoned directories left by an unclean shutdown.

## Worker reliability model

Each claimed audit receives:

- `worker_id`: identifies the worker process;
- `lease_token`: a unique fencing token for this claim;
- `heartbeat_at`: the last successful lease refresh;
- `attempt_count`: incremented atomically when a job is claimed.

The worker refreshes the heartbeat periodically in a separate thread while the audit is being processed. Recovery only considers jobs whose heartbeat (or, for pre-migration records, `started_at`) has expired.

If the attempt budget remains, the job is returned to `pending` with a retry delay. Otherwise, it becomes `failed`. Every completion or failure update is conditional on the same lease token, so a worker whose lease has expired cannot overwrite a newer attempt.

**Deployment note:** stop all old worker processes before applying the lease migration and starting workers with the new code. Old worker processes do not understand lease tokens and must not run alongside the new version.

## Requirements

- Python 3.11 or newer.
- Docker Desktop with Docker Compose, or a reachable PostgreSQL instance.
- A Gemini API key.
- `yt-dlp` available in PATH (installed by `requirements.txt` as a Python package; the executable should be available in the active virtual environment).
- **FFmpeg / FFprobe** installed and available in PATH for local technical analysis. The Docker image installs FFmpeg automatically.
- OpenCV is installed from `requirements.txt` as the headless package; no separate GUI toolkit is required.

## Local setup (Windows PowerShell)

1. Open the project directory:

```powershell
cd .\video_auditor_project
```

2. Create and configure the environment file:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `GEMINI_API_KEY`. Never commit `.env` or share the API key.

3. Install FFmpeg on Windows and make sure `ffprobe.exe` is available in PATH. Verify with `ffmpeg -version` and `ffprobe -version`. If FFprobe is unavailable, processing can continue without metadata while `FFPROBE_REQUIRED=false`.

4. Start PostgreSQL:

```powershell
docker compose up -d postgres
docker compose ps
```

5. Create/activate the virtual environment and install dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

6. Stop any previously running worker processes, then apply database migrations:

```powershell
alembic upgrade head
```

7. Start the API in the first PowerShell window:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

8. Start exactly one worker in a second PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

Open `http://127.0.0.1:8000/` for the interface or `http://127.0.0.1:8000/docs` for API documentation.

## API endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | API health check |
| `POST /api/v1/audits` | Create an audit |
| `GET /api/v1/audits` | List audits |
| `GET /api/v1/audits/{audit_id}` | Get an audit and its report |
| `GET /docs` | Swagger UI |

The `content_type` field accepts `unknown`, `live_action`, or `animation`.

Audit statuses:

- `pending` — waiting to be claimed or waiting for a retry time;
- `processing` — currently owned by a worker lease;
- `completed` — the report was created successfully;
- `failed` — processing ended after a non-retryable error or exhausted attempt budget.

Report verdicts:

- `candidate_accepted` — provisionally suitable;
- `candidate_rejected` — provisionally unsuitable;
- `manual_review_required` — the evidence is insufficient or ambiguous.

## Configuration

All settings are loaded from `.env`.

| Variable | Default | Purpose |
|---|---:|---|
| `DATABASE_URL` | local Docker PostgreSQL | SQLAlchemy connection string |
| `GEMINI_API_KEY` | empty | Gemini API key |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Gemini model |
| `GEMINI_TIMEOUT_MS` | `180000` | HTTP request timeout in milliseconds |
| `GEMINI_RETRY_ATTEMPTS` | `3` | SDK attempts, including the first |
| `YT_DLP_BINARY` | `yt-dlp` | Downloader executable |
| `VIDEO_TEMP_ROOT` | `storage/temp` | Temporary video root |
| `VIDEO_MAX_FILESIZE_BYTES` | `1500000000` | Maximum download size |
| `VIDEO_DOWNLOAD_TIMEOUT_SECONDS` | `900` | Download timeout |
| `FFPROBE_BINARY` | `ffprobe` | FFprobe executable |
| `FFPROBE_TIMEOUT_SECONDS` | `30` | Metadata extraction timeout |
| `FFPROBE_REQUIRED` | `false` | Fail the audit if FFprobe is unavailable |
| `CV_ANALYSIS_REQUIRED` | `false` | Fail the audit if OpenCV frame analysis cannot run |
| `CV_SAMPLE_INTERVAL_SECONDS` | `0.5` | Target interval between sampled frames |
| `CV_MAX_SAMPLES` | `1200` | Maximum number of frames sampled per video |
| `CV_ANALYSIS_TIMEOUT_SECONDS` | `120` | Time budget for the OpenCV sampling stage |
| `CV_RESIZE_WIDTH` | `320` | Maximum width used for analysis frames |
| `CV_BLACK_LUMA_THRESHOLD` | `16` | Grayscale threshold for dark pixels (0–255) |
| `CV_BLACK_PIXEL_RATIO` | `0.985` | Minimum dark-pixel ratio for a black-frame candidate |
| `CV_BLACK_MIN_DURATION_SECONDS` | `0.75` | Minimum estimated black interval duration |
| `CV_FREEZE_MEAN_DIFF_THRESHOLD` | `0.8` | Maximum mean pixel difference for freeze-like samples |
| `CV_FREEZE_CHANGED_PIXEL_RATIO` | `0.002` | Maximum changed-pixel ratio for freeze-like samples |
| `CV_FREEZE_PIXEL_DELTA` | `3` | Per-pixel difference required to count as changed |
| `CV_FREEZE_MIN_DURATION_SECONDS` | `2.5` | Minimum sampled freeze-like interval duration |
| `CV_SCENE_CHANGE_THRESHOLD` | `0.65` | Histogram/pixel-change score for a possible scene transition |
| `CV_BLUR_FOCUS_THRESHOLD` | `20` | Laplacian-variance threshold for low-focus candidates |
| `CV_BLUR_MIN_DURATION_SECONDS` | `1.5` | Minimum sustained low-focus interval |
| `CV_EVIDENCE_MAX_FRAMES` | `8` | Maximum preview frames stored per audit |
| `CV_EVIDENCE_WIDTH` | `240` | Maximum preview width in pixels |
| `CV_EVIDENCE_JPEG_QUALITY` | `45` | JPEG preview quality |
| `CV_EVIDENCE_MAX_BYTES_PER_FRAME` | `12000` | Maximum encoded preview size per image |
| `AUDIT_MAX_ATTEMPTS` | `2` | Maximum worker claims per audit |
| `AUDIT_RETRY_DELAY_SECONDS` | `30` | Base worker retry delay |
| `GEMINI_RATE_LIMIT_COOLDOWN_SECONDS` | `120` | Queue cooldown after HTTP 429 |
| `WORKER_POLL_SECONDS` | `2` | Queue polling interval |
| `WORKER_HEARTBEAT_SECONDS` | `15` | Lease refresh interval |
| `WORKER_LEASE_TIMEOUT_SECONDS` | `90` | Lease expiration threshold |
| `WORKER_RECOVERY_INTERVAL_SECONDS` | `15` | Stale lease recovery interval |

The lease timeout must be at least three times the heartbeat interval.

## Tests

Run the automated tests from the active virtual environment:

```powershell
python -m pytest
```

The unit tests use SQLite for worker lease lifecycle checks and mocks for external services. FFprobe tests mock the executable and cover metadata parsing, rational frame rates, missing audio, short-duration findings, malformed responses, and timeouts. OpenCV tests use synthetic frame metrics and a fake capture source to verify black/freeze grouping, scene-change and blur candidates, timestamp evidence previews, sampling limits, and optional-dependency behavior. They do not prove that Gemini or YouTube access is currently available; perform a separate end-to-end test with an authorized public video.

## Important limitations and responsible use

1. Gemini video analysis does not guarantee that every frame or every audio sample is inspected.
2. Timestamps, affected duration estimates, and defect confidence can be inaccurate.
3. A technical signal such as blur, low brightness, or repeated frames is not automatically a defect; thresholds must be calibrated against the SFT specification and reviewed examples. The initial OpenCV black/freeze detectors are conservative sampling-based indicators, can miss short events between samples, and can flag intentionally static or dark scenes. Scene-change detection can confuse rapid camera/object movement with an edit. Blur uses Laplacian variance, which is content-dependent and is only a screening signal. JPEG evidence previews are stored inside the audit JSON record; apply an appropriate database retention policy because they may contain source-video content.
4. Video downloading must comply with the source platform's terms, copyright, and the content owner's permissions. Only process material that the operator is authorized to download and analyze.
5. Local temporary files are deleted after the processor finishes. As with any process, forced power loss or filesystem errors can leave temporary artifacts; startup cleanup is a recovery measure, not a guarantee against every storage failure.
6. Do not expose the development API publicly without authentication, authorization, rate limiting, HTTPS, and appropriate data-retention controls.
7. The current worker is a simple PostgreSQL-backed worker suitable for a controlled MVP. High-volume production deployments will need monitoring, operational metrics, concurrency limits, and a deliberately managed queue architecture.

## Useful documentation

- [FFmpeg / FFprobe documentation](https://ffmpeg.org/ffprobe.html)
- [Gemini API: Video understanding](https://ai.google.dev/gemini-api/docs/video-understanding)
- [Gemini API: Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [FastAPI](https://fastapi.tiangolo.com/)
- [SQLAlchemy](https://docs.sqlalchemy.org/)
- [Alembic](https://alembic.sqlalchemy.org/)
