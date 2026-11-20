# Execution evidence

Everything below is real output from runs of this project on Windows 11, Python 3.12.3, FastAPI 0.163.1, Uvicorn 0.54.0. Nothing is fabricated or edited except that timestamps are shown exactly as logged. Request IDs and Discord's server and channel IDs are identifiers, not credentials.

## Scope: what was and was not verified live

| Platform | Verified against a live service |
|---|---|
| Discord | Yes (sections 2 to 6) |
| Mastodon, Bluesky, Telegram, LinkedIn | **No.** Implemented and tested with mocked HTTP only |

Discord was verified by a real request through `POST /posting`: the other four platforms were switched off for those runs, a real message appeared in the Discord channel, and the times match (the Discord screenshot shows India Standard Time, UTC+5:30).

## 1. Automated tests (18 November 2026)

```text
569 passed in 61.95s

TOTAL    1229 statements, 0 missed, 254 branches, 0 partial    100.0%
Required test coverage of 97.0% reached. Total coverage: 100.00%
```

`ruff check .` reported `All checks passed!`. A later run, after the documentation checks were added, reported `578 passed in 61.74s`. No test publishes anything or needs credentials..

## 2. Direct adapter check against the live Discord API (17 November 2026)

```text
configured: True
OK PublishedPost(posted_url=None, public_access=False, posted_at=datetime.datetime(2026, 11, 17, 3, 17, 5, 560000, tzinfo=datetime.timezone.utc))
```

03:17:05 UTC is 08:47 IST, matching the first message in the channel.

## 3. End-to-end `POST /posting` against live Discord (17 November 2026)

Response, HTTP 200:

```json
{"success":true,"total_platforms":1,"successful_count":1,"failed_count":0,"results":[{"platform":"Discord","status":"success","posted_url":null,"public_access":false,"date":"2026-11-17","time":"08:54:20","error":null}],"skipped_platforms":["Mastodon","Bluesky","Telegram","LinkedIn"]}
```

08:54:20 UTC is 14:24 IST, matching the "Check out our latest business platform..." message in the channel.

## 4. Request IDs and structured logs (17 November 2026)

Server log for one rejected and one accepted request (credentials for the other platforms were blanked in that terminal):

```text
2026-11-17T09:06:11.155Z INFO     [0bb0c98c80ab4b8ebc63127953a03c8a] app.api.middleware: request_received method=POST path='/posting'
2026-11-17T09:06:11.810Z INFO     [0bb0c98c80ab4b8ebc63127953a03c8a] app.api.errors: request_rejected status=400 codes=['MISSING_CTA_URL']
2026-11-17T09:06:11.826Z INFO     [0bb0c98c80ab4b8ebc63127953a03c8a] app.api.middleware: request_completed method=POST path='/posting' status=400 duration_ms=672
2026-11-17T09:06:13.242Z INFO     [demo-request-001] app.api.middleware: request_received method=POST path='/posting'
2026-11-17T09:06:13.256Z INFO     [demo-request-001] app.api.routes.posting: posting_request_validated message_chars=32
2026-11-17T09:06:13.260Z INFO     [demo-request-001] app.services.posting_service: posting_started platforms=1 skipped=['Mastodon', 'Bluesky', 'Telegram', 'LinkedIn'] message_chars=32
2026-11-17T09:06:13.264Z INFO     [demo-request-001] app.services.posting_service: platform_attempt platform=Discord
2026-11-17T09:06:14.427Z INFO     [demo-request-001] app.services.posting_service: platform_finished platform=Discord status=success attempts=1 duration_ms=1172
2026-11-17T09:06:14.428Z INFO     [demo-request-001] app.services.posting_service: posting_finished success=True attempted=1 succeeded=1 failed=0 duration_ms=1172
2026-11-17T09:06:14.431Z INFO     [demo-request-001] app.api.middleware: request_completed method=POST path='/posting' status=200 duration_ms=1187
```

The 400 response carried `X-Request-ID: 0bb0c98c80ab4b8ebc63127953a03c8a` in its header and body; the second request reused the client-supplied `demo-request-001`. No log line contains message text, a query string or a credential.

## 5. Diagnostics, timing and the platform switch (18 November 2026)

Started with `DISABLED_PLATFORMS=Mastodon,Bluesky,Telegram,LinkedIn` and `python -m app`:

```text
2026-11-18T02:58:40.616Z INFO     [-] app.main: application_started environment=development configured=['Discord'] not_configured=[]
2026-11-18T02:58:56.860Z INFO     [063976a7bd9c415b9bdce539d370d4d1] app.api.middleware: request_received method=GET path='/health'
2026-11-18T02:58:58.436Z INFO     [6650baaea2ad4c6aaec2e0f2bc2ff00c] app.api.middleware: request_received method=GET path='/platforms'
2026-11-18T02:58:58.596Z INFO     [b02af91a6f7a43ae930eb4357d3f9bb7] app.services.posting_service: posting_started platforms=1 skipped=[] message_chars=31
2026-11-18T02:59:00.226Z INFO     [b02af91a6f7a43ae930eb4357d3f9bb7] app.services.posting_service: platform_finished platform=Discord status=success attempts=1 duration_ms=1625
```

A second run's responses (HTTP 200):

```text
status version uptime_seconds
------ ------- --------------
ok     0.1.0             88.7
```

`GET /platforms` listed only Discord (`configured: true`, no missing settings) with the four disabled platforms named, and `POST /posting` returned `"total_duration_ms": 2561` and `"platform_durations_ms": {"Discord": 2547}` with date `2026-11-18` and time `03:00:09`. Full bodies are in [API_EXAMPLES.md](API_EXAMPLES.md).

## 6. Graceful shutdown (18 November 2026)

Ctrl+C on `python -m app` with no request in flight:

```text
2026-11-18T09:03:49.558Z INFO     [-] uvicorn.error: Shutting down
2026-11-18T09:03:49.670Z INFO     [-] uvicorn.error: Waiting for application shutdown.
2026-11-18T09:03:49.756Z INFO     [-] app.main: application_stopped http_client_closed=True
2026-11-18T09:03:49.758Z INFO     [-] uvicorn.error: Application shutdown complete.
2026-11-18T09:03:49.758Z INFO     [-] uvicorn.error: Finished server process [11360]
```

This shows a clean lifecycle with the shared HTTP client closed. It does not demonstrate waiting for an in-flight request: that wait is uvicorn's `timeout_graceful_shutdown`, set from the publication deadline (35 seconds with default settings, covered by a unit test of the calculation).

## 7. Reproducing the evidence

```powershell
pytest -q --cov=app --cov-branch --cov-report=term-missing
$env:DISABLED_PLATFORMS = "Mastodon,Bluesky,Telegram,LinkedIn"   # only Discord stays live
python -m app
```

Then send a request as shown in [API_EXAMPLES.md](API_EXAMPLES.md). Requires your own `DISCORD_WEBHOOK_URL` in `.env`.


## 8. Real post URL from a live Discord publication (19 November 2026)

With `DISCORD_GUILD_ID` set, a request through `POST /posting` returned a working jump link. The other four platforms were switched off for the run.

```json
{
  "success": true,
  "total_platforms": 1,
  "successful_count": 1,
  "failed_count": 0,
  "results": [
    {
      "platform": "Discord",
      "status": "success",
      "posted_url": "https://discord.com/channels/1572084998830555177/1572084999446859828/1572799890562486272",
      "public_access": false,
      "date": "2026-11-19",
      "time": "02:25:15",
      "error": null
    }
  ],
  "skipped_platforms": [],
  "total_duration_ms": 1311,
  "platform_durations_ms": {"Discord": 1311}
}
```

Opening `posted_url` in a logged-in browser jumped to the message "Posted URL check" in the server channel, which Discord displayed at 7:55 AM India Standard Time (02:25 UTC). This verifies the jump-link format that the research document had marked "verify". The link works only for members of the server, which is why `public_access` is `false`.


## 9. Fresh-clone verification (19 November 2026)

The public repository was cloned into a new folder with no `.env` file and no platform variables set. A new Python 3.12.3 virtual environment was created and `requirements-dev.txt` was installed (version ranges; they resolved to FastAPI 0.165.0, while the earlier runs used 0.163.1).

```text
All checks passed!
579 passed in 52.54s

TOTAL                                 1229      0    254      0  100.0%
Required test coverage of 97.0% reached. Total coverage: 100.00%
579 passed in 72.56s (0:01:12)
```

`python -m app` started without credentials:

```text
app.main: application_started environment=development configured=[] not_configured=['Mastodon', 'Bluesky', 'Telegram', 'LinkedIn', 'Discord']
```

Seventeen requests were sent to that server. Each returned the status that was fixed in advance:

| Request | Expected | Received |
|---|---|---|
| valid message, nothing configured | 503 | 503 |
| exactly 200 characters | 503 | 503 |
| 201 characters | 400 | 400 |
| empty message | 400 | 400 |
| no link | 400 | 400 |
| two links | 400 | 400 |
| `javascript:` link | 400 | 400 |
| `ftp://` link | 400 | 400 |
| `localhost` link | 400 | 400 |
| client tries to pick platforms | 422 | 422 |
| missing `message` field | 422 | 422 |
| invalid JSON | 422 | 422 |
| `GET /health` | 200 | 200 |
| `GET /platforms` | 200 | 200 |
| `GET /docs` | 200 | 200 |
| unknown path | 404 | 404 |
| wrong method on `/posting` | 405 | 405 |

The server log showed these codes: `MESSAGE_TOO_LONG`, `EMPTY_MESSAGE`, `MISSING_CTA_URL`, `MULTIPLE_CTA_URLS`, `UNSUPPORTED_URL_SCHEME` (twice), `URL_HOST_NOT_PUBLIC`, `UNKNOWN_FIELD`, `FIELD_REQUIRED` and `INVALID_JSON`. `GET /openapi.json` listed exactly three paths: `/posting`, `/health` and `/platforms`.


## 10. Partial failure against a live service (20 November 2026)

Discord was live. LinkedIn was given a deliberately invalid token (`DISABLED_PLATFORMS` switched off the other three platforms), so LinkedIn rejected the request. A safety check confirmed the override took effect before the server started, so the real LinkedIn token was never used. The same request returned HTTP 200:

```json
{
  "success": false,
  "total_platforms": 2,
  "successful_count": 1,
  "failed_count": 1,
  "results": [
    {
      "platform": "LinkedIn",
      "status": "failed",
      "posted_url": null,
      "public_access": false,
      "date": null,
      "time": null,
      "error": "Authentication failed"
    },
    {
      "platform": "Discord",
      "status": "success",
      "posted_url": "https://discord.com/channels/1572084998830555177/1572084999446859828/1573166654094053499",
      "public_access": false,
      "date": "2026-11-20",
      "time": "02:42:38",
      "error": null
    }
  ],
  "skipped_platforms": [],
  "total_duration_ms": 1234,
  "platform_durations_ms": {"LinkedIn": 1015, "Discord": 1218}
}
```

Server log for that request:

```text
2026-11-20T02:42:37.044Z INFO     [9f300ee643994f0b8246d57c8eef6953] app.services.posting_service: posting_started platforms=2 skipped=[] message_chars=40
2026-11-20T02:42:37.048Z INFO     [9f300ee643994f0b8246d57c8eef6953] app.services.posting_service: platform_attempt platform=LinkedIn
2026-11-20T02:42:37.076Z INFO     [9f300ee643994f0b8246d57c8eef6953] app.services.posting_service: platform_attempt platform=Discord
2026-11-20T02:42:38.067Z WARNING  [9f300ee643994f0b8246d57c8eef6953] app.services.posting_service: platform_failed platform=LinkedIn error='Authentication failed' retryable=False ambiguous=False attempts=1 detail=HTTP 401
2026-11-20T02:42:38.068Z INFO     [9f300ee643994f0b8246d57c8eef6953] app.services.posting_service: platform_finished platform=LinkedIn status=failed attempts=1 duration_ms=1015
2026-11-20T02:42:38.294Z INFO     [9f300ee643994f0b8246d57c8eef6953] app.services.posting_service: platform_finished platform=Discord status=success attempts=1 duration_ms=1218
2026-11-20T02:42:38.296Z INFO     [9f300ee643994f0b8246d57c8eef6953] app.services.posting_service: posting_finished success=False attempted=2 succeeded=1 failed=1 duration_ms=1234
```

This shows, against a live service: one platform failing without affecting the other, the failed result carrying no URL, date or time, an authentication failure being tried once and not retried, and both platforms running concurrently (both attempts started within 30 ms of each other, and the total time is close to the slower platform, not the sum). Only Discord was live in this run; the LinkedIn adapter's failure path was exercised against LinkedIn's real servers, but a successful LinkedIn publication has not been verified.