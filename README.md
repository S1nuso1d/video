# Video Auditor — shared video analysis workspace

Video Auditor is a team tool for preliminary video-quality analysis against a project's own criteria. It accepts a public YouTube URL, queues a background job, downloads the source, runs deterministic technical and frame checks, asks Google Gemini for a structured visual/audio assessment, evaluates an optional project rubric, and stores a shareable result.

The product is intentionally a shared analysis workspace, not a user-management platform. There are no admin/operator/reviewer accounts, invitations, per-user permissions, or admin dashboard. Everyone who can access the workspace sees the same audit history and review queue.

> Important: results are preliminary evidence, not a certified technical measurement or a guarantee that every defect has been found. Human inspection against the source material remains necessary for consequential decisions.

## Product capabilities

- Submit a YouTube URL, select the content type, and optionally supply project-specific technical, visual, and semantic criteria.
- Track queued/running/cancelled/completed/failed jobs and see the current processing stage.
- Cancel a queued or running job; the worker stops at cooperative checkpoints and cleans temporary files.
- Inspect FFprobe metadata and technical observations separately from AI findings.
- Inspect OpenCV sampling statistics, timestamped visual observations, and small frame previews.
- Read the structured Gemini verdict, summary, detected defects, confidence, timestamps, and SFT annotation comment.
- See the criteria-engine decision (APPROVE, REVIEW, REJECT) without overwriting the original Gemini verdict.
- Record a shared team decision (approved, rejected, or needs_changes) with a comment; the decision history is append-only.
- Copy a direct link to a particular audit. Teammates open it inside the same workspace and use the same workspace key, if one is configured.
- View estimated Gemini usage/cost and per-stage processing timings.
- Use the REST API or Swagger documentation at /docs to integrate the analysis engine with an existing company website or internal system.

The web interface is a lightweight workspace for the analysis engine. It does not attempt to replace an existing corporate portal, identity provider, or workflow application.

## Shared access

WORKSPACE_ACCESS_KEY is an optional shared key, not an account system.

- For development on a private localhost machine, leave it empty.
- For a shared server, generate one random key and configure it in the server's .env.
- Give that same key to authorized teammates through a secure channel. The browser stores it in the current tab's sessionStorage and sends it as Authorization: Bearer WORKSPACE_ACCESS_KEY.
- Everyone with the key has the same access to every audit and can submit/cancel jobs and record team review decisions. The application does not identify individual team members.
- If the key is empty, API endpoints are not protected by this key. Do not expose such an instance to an untrusted network.

Generate a key:

~~~powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
~~~

Use HTTPS, keep the key private, and put rate limiting and appropriate network restrictions in front of any externally accessible deployment. A shared key is deliberately simpler than per-user authentication; it is not tenant isolation and cannot provide individual accountability.

Audit links contain an audit ID, not an access credential. A teammate still needs the workspace key if the server requires one.

## Processing workflow

~~~text
Browser / existing company system
             |
             v
        FastAPI API
             |
             v
      PostgreSQL queue
             |
             v
       Worker claims job
       (row lock + lease)
             |
             v
         yt-dlp download
             |
       +-----+------+
       |            |
       v            v
     FFprobe      OpenCV
  metadata and   frame sampling
  tech findings  and visual signals
       |            |
       +-----+------+
             |
             v
   Gemini video assessment
   (public YouTube URI by default)
             |
             v
   Criteria engine + structured report
             |
             v
   Save result and processing metrics
             |
             v
   Delete temporary source files
~~~

The worker downloads the source locally for FFprobe and OpenCV. With GEMINI_YOUTUBE_DIRECT=true, Gemini receives the public YouTube URL directly, so the same local file is not uploaded a second time through Gemini Files. Google documents this direct YouTube input as a preview feature; it requires a video publicly accessible to Google. Set the option to false to use the local downloaded file with Gemini Files instead.

## API endpoints

All workspace endpoints are under /api/v1. When WORKSPACE_ACCESS_KEY is set, send it in the Bearer header.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | /api/v1/audits | Create and queue a video analysis |
| GET | /api/v1/audits | List the shared audit history |
| GET | /api/v1/audits/{audit_id} | Retrieve one report/status |
| POST | /api/v1/audits/{audit_id}/cancel | Cancel a queued/running analysis |
| GET | /api/v1/reviews/queue | List results waiting for team review |
| POST | /api/v1/reviews/{audit_id}/decision | Record a team decision |
| POST | /api/v1/reviews/{audit_id}/reopen | Reopen a completed team decision |
| GET | /api/v1/reviews/{audit_id}/history | Retrieve append-only decision history |
| GET | /health | Health check (public) |

There are no auth-token, user-provisioning, account-role, reviewer-assignment, admin-dashboard, or admin-CSV endpoints in this workspace version.

Example request:

~~~powershell
$headers = @{ Authorization = "Bearer $env:WORKSPACE_ACCESS_KEY" }
$body = @{
  youtube_url = "https://www.youtube.com/watch?v=VIDEO_ID"
  content_type = "live_action"
} | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/audits" -Headers $headers -ContentType "application/json" -Body $body
~~~

## Project criteria

Each audit can receive a criteria_spec object. It is validated before the job is queued and stored with that audit, allowing different projects to use different rubrics without changing Python code. The current UI accepts the rubric as JSON; saved named profiles and a form-based criteria builder are future product improvements.

Supported rule types:

- technical: FFprobe measurements (duration_seconds, file_size_bytes, width, height, frame_rate, has_audio, video_codec, format_name, video_bit_rate, container_bit_rate, audio_stream_count). Operators: eq, neq, gt, gte, lt, lte, between, in, not_in.
- visual: sampled OpenCV signals by code, using finding_present, finding_absent, or max_duration.
- semantic: Gemini defect categories using defect_present or defect_absent. Semantic rules remain unknown unless Gemini reports adequate preliminary review coverage; uncertain/low-confidence matches also remain unknown.

Rule severity is critical, warning, or info. A failed critical rule produces REJECT; other failures and any unknown result produce REVIEW; APPROVE is returned only when every configured rule passes. Missing technical or visual measurements are not silently treated as a pass. The result is stored in decision, decision_summary, and criteria_results; the original Gemini verdict remains unchanged.

Example rubric: examples/criteria/outdoor_video.json.

Example create-audit request:

~~~json
{
  "youtube_url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "content_type": "live_action",
  "criteria_spec": {
    "name": "Outdoor people videos",
    "version": "1.0",
    "rules": [
      {
        "id": "minimum_height",
        "type": "technical",
        "field": "height",
        "operator": "gte",
        "value": 1080,
        "severity": "critical"
      },
      {
        "id": "no_watermark",
        "type": "semantic",
        "code": "watermark_or_overlay",
        "operator": "defect_absent",
        "severity": "critical"
      }
    ]
  }
}
~~~

Technical fields are allow-listed; arbitrary Python paths or expressions are not accepted. between takes a two-item value array such as [30, 180]. Visual max_duration uses seconds. Semantic codes must match the existing Gemini DefectCategory values.

## Local setup (Windows PowerShell)

Requirements: Python 3.11+, Docker Desktop with Compose (or PostgreSQL), Node.js for frontend syntax validation, FFmpeg/FFprobe, and a Gemini API key.

1. Clone the repository and enter it:

~~~powershell
git clone https://github.com/S1nuso1d/video.git
cd video
~~~

2. Create the local environment file once:

~~~powershell
Copy-Item .env.example .env
notepad .env
~~~

Set GEMINI_API_KEY. Do not commit .env, and do not overwrite an existing .env when updating the project. For shared access, set WORKSPACE_ACCESS_KEY on the server; for localhost-only development it can remain blank.

3. Install FFmpeg and ensure both executables are on PATH. Verify:

~~~powershell
ffmpeg -version
ffprobe -version
~~~

4. Start PostgreSQL:

~~~powershell
docker compose up -d postgres
docker compose ps
~~~

5. Create/activate a virtual environment and install dependencies:

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
~~~

6. Apply database migrations:

~~~powershell
python -m alembic upgrade head
python -m alembic current
~~~

7. Start the API in one PowerShell window:

~~~powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~

8. Start the worker in a second PowerShell window:

~~~powershell
cd "C:\path\to\video"
.\.venv\Scripts\Activate.ps1
python -m app.worker
~~~

Open http://127.0.0.1:8000. OpenAPI docs are at http://127.0.0.1:8000/docs.

## Configuration

All settings load from .env.

| Variable | Default | Purpose |
|---|---:|---|
| DATABASE_URL | local Docker PostgreSQL | SQLAlchemy connection |
| GEMINI_API_KEY | empty | Gemini API key |
| WORKSPACE_ACCESS_KEY | empty | Optional shared team key |
| GEMINI_MODEL | gemini-3.8-flash | Gemini model |
| GEMINI_YOUTUBE_DIRECT | true | Pass a public YouTube URL directly to Gemini; false uses Gemini Files |
| GEMINI_TIMEOUT_MS | 180000 | Gemini HTTP request timeout in milliseconds |
| GEMINI_RETRY_ATTEMPTS | 3 | SDK attempts including the first |
| GEMINI_INPUT_PRICE_USD_PER_MILLION | 0.75 | Configured input rate |
| GEMINI_CACHED_INPUT_PRICE_USD_PER_MILLION | 0.075 | Configured cached-input rate |
| GEMINI_OUTPUT_PRICE_USD_PER_MILLION | 3.75 | Configured output rate (includes thinking tokens) |
| GEMINI_PRICING_VERSION | 2026-11-standard-paid | Rate-card identifier stored with usage |
| GEMINI_USD_TO_RUB_RATE | 0 | Optional conversion rate; 0 disables RUB estimate |
| YT_DLP_BINARY | yt-dlp | Downloader executable |
| VIDEO_TEMP_ROOT | storage/temp | Temporary video root |
| VIDEO_FORMAT | bv+ba/b | Download the best available source rendition |
| VIDEO_MAX_FILESIZE_BYTES | 1500000000 | Maximum download size |
| VIDEO_DOWNLOAD_TIMEOUT_SECONDS | 900 | Download timeout |
| FFPROBE_BINARY | ffprobe | FFprobe executable/path |
| FFPROBE_TIMEOUT_SECONDS | 30 | Metadata extraction timeout |
| FFPROBE_REQUIRED | false | Fail the audit if FFprobe is unavailable |
| CV_ANALYSIS_REQUIRED | false | Fail the audit if OpenCV analysis cannot run |
| CV_SAMPLE_INTERVAL_SECONDS | 0.5 | Target interval between sampled frames |
| CV_MAX_SAMPLES | 1200 | Maximum number of frames sampled |
| CV_ANALYSIS_TIMEOUT_SECONDS | 120 | OpenCV stage time budget |
| CV_RESIZE_WIDTH | 320 | Maximum analysis-frame width |
| CV_BLACK_LUMA_THRESHOLD | 16 | Grayscale threshold for dark pixels |
| CV_BLACK_PIXEL_RATIO | 0.985 | Dark-pixel ratio for black-frame candidate |
| CV_BLACK_MIN_DURATION_SECONDS | 0.75 | Minimum estimated black interval |
| CV_FREEZE_MEAN_DIFF_THRESHOLD | 0.8 | Freeze-like frame difference threshold |
| CV_FREEZE_CHANGED_PIXEL_RATIO | 0.002 | Maximum changed-pixel ratio |
| CV_FREEZE_PIXEL_DELTA | 3 | Pixel delta considered changed |
| CV_FREEZE_MIN_DURATION_SECONDS | 2.5 | Minimum freeze-like interval |
| CV_SCENE_CHANGE_THRESHOLD | 0.65 | Scene-change candidate threshold |
| CV_BLUR_FOCUS_THRESHOLD | 20 | Laplacian variance blur threshold |
| CV_BLUR_MIN_DURATION_SECONDS | 1.5 | Minimum sustained low-focus interval |
| CV_EVIDENCE_MAX_FRAMES | 8 | Maximum preview frames per audit |
| CV_EVIDENCE_WIDTH | 240 | Preview frame width |
| CV_EVIDENCE_JPEG_QUALITY | 45 | JPEG preview quality |
| CV_EVIDENCE_MAX_BYTES_PER_FRAME | 12000 | Maximum encoded preview size |
| AUDIT_MAX_ATTEMPTS | 2 | Maximum worker claims |
| AUDIT_RETRY_DELAY_SECONDS | 30 | Base retry delay |
| GEMINI_RATE_LIMIT_COOLDOWN_SECONDS | 120 | Queue cooldown after HTTP 429 |
| WORKER_POLL_SECONDS | 2 | Queue polling interval |
| WORKER_HEARTBEAT_SECONDS | 15 | Worker heartbeat interval |
| WORKER_LEASE_TIMEOUT_SECONDS | 90 | Lease expiry threshold |
| WORKER_RECOVERY_INTERVAL_SECONDS | 15 | Stale-lease recovery interval |

The lease timeout must be at least three times the heartbeat interval.

## Processing performance and diagnostics

The API response includes processing_stage, processing_metrics, Gemini token usage, and estimated costs when provider usage data is available. The worker logs download, FFprobe, OpenCV, Gemini upload/preparation (when used), and Gemini interaction durations separately. The browser polls only the currently selected audit every five seconds.

OpenCV currently targets one sample every 0.5 seconds, up to 1,200 samples, resized to 320 pixels wide. This is a bounded screening pass, not full frame-by-frame defect adjudication. A 120-second CV timeout is a safety limit, not an expected runtime. If the CV stage times out and CV_ANALYSIS_REQUIRED=false, the worker should preserve a warning and continue to Gemini; with the setting true, the timeout fails the audit. Check the worker log and the actual .env value when Gemini shows zero processing time.

If the report says FFprobe is unavailable, install FFmpeg/FFprobe and restart the API/worker process so Windows PATH changes are visible. Until FFprobe is available, technical metadata and rules depending on it cannot be evaluated.

The Gemini pricing values are configured estimates, not Google invoices. The output rate includes thinking tokens; thinking is shown separately for diagnostics and must not be charged twice. Update the rate card when the model, billing tier, or provider prices change. The rate-card identifier and usage metadata are stored with the audit.

## Worker reliability

Each claimed audit receives a worker ID, lease token, heartbeat timestamp, and attempt count. The worker refreshes its lease periodically in a separate thread. Recovery returns a stale job to the queue if the attempt budget remains; otherwise it fails. Lease tokens prevent a worker with an expired claim from overwriting a newer attempt.

The worker cooperatively observes cancellation at stage boundaries and during supported subprocess/frame-processing waits. The current synchronous Gemini Interactions call cannot be forcefully interrupted mid-request; a cancellation requested during that call is applied after the call returns or times out, and its result is discarded.

Temporary source files are isolated per audit and removed after processing. Worker startup also removes abandoned temporary directories from unclean shutdowns.

## Expert-labelled calibration

The offline calibration tool compares results with expert labels. It does not change production verdicts or fine-tune Gemini/OpenCV.

Prepare UTF-8 JSONL with one sample per line. The included example is synthetic; meaningful metrics require independently reviewed real samples and an agreed defect taxonomy.

~~~powershell
python -m app.calibration_cli --input examples/calibration/example.jsonl --output reports/calibration.json --markdown reports/calibration.md
~~~

The evaluator reports the three-class verdict confusion matrix, accuracy, balanced accuracy, decision coverage, manual-review share, false-acceptance/rejection rates, and per-defect precision/recall/F1. It uses exact defect-code matching; taxonomy mappings must be explicit.

## Tests

~~~powershell
python -m pytest
python scripts/check_frontend.py
~~~

Tests cover API schemas, worker leases/cancellation, downloader cleanup, FFprobe parsing, OpenCV signals, criteria evaluation, Gemini usage/cost handling, and shared-workspace access/review. External YouTube and Gemini calls are mocked; passing automated tests does not prove live provider access or establish real-video processing time. Run an end-to-end test with a video the team is authorized to process after deployment.

## Data compatibility

This branch removes account and admin APIs and the corresponding UI. It does not drop legacy user_accounts tables or user-reference columns from the existing PostgreSQL schema. Those records are retained to avoid destructive migrations and preserve existing audit data. New audits are shared and are not assigned to individual users.

## Limitations and responsible use

1. Gemini does not guarantee that every frame or audio sample is inspected. Timestamps, duration estimates, and confidence can be inaccurate.
2. Black/freeze/scene-change/blur signals are heuristic screening indicators. They can miss short events between samples or flag intentional dark/static scenes and motion as possible issues.
3. Missing FFprobe metadata means technical measurements/rules cannot be evaluated. Missing evidence must not be treated as a pass.
4. Source downloading must comply with platform terms, copyright, and the content owner's permissions. Process only material the team is authorized to analyze and share.
5. JPEG evidence previews are stored in audit records and can contain source-video content. Apply a suitable database retention policy.
6. A shared workspace key grants the same access to every team member who possesses it. Use appropriate network controls and HTTPS before team/public exposure.
7. The PostgreSQL-backed worker is an MVP queue. High-volume deployments need monitoring, concurrency limits, operational alerting, and a deliberately managed job-queue architecture.

## Useful documentation

- FFmpeg / FFprobe: https://ffmpeg.org/ffprobe.html
- Gemini video understanding: https://ai.google.dev/gemini-api/docs/video-understanding
- Gemini structured outputs: https://ai.google.dev/gemini-api/docs/structured-output
- FastAPI: https://fastapi.tiangolo.com/
- SQLAlchemy: https://docs.sqlalchemy.org/
- Alembic: https://alembic.sqlalchemy.org/
