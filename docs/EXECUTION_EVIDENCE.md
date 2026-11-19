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

`ruff check .` reported `All checks passed!`. No test publishes anything or needs credentials.

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