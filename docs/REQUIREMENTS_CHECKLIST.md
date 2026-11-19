# Requirements checklist

Status as of 19 November 2026. **Met** means demonstrated by automated tests, a recorded run, or inspection of the repository. **Partially met** says what is missing. The main gap is live verification: only Discord has been exercised against a real service.

## Functional requirements

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Message at most 200 characters, trimmed, never truncated | Met | `tests/test_message_validation.py`, `tests/test_posting_api.py` (200 accepted, 201 rejected with HTTP 400) |
| 2 | Exactly one CTA link | Met | codes `MISSING_CTA_URL` and `MULTIPLE_CTA_URLS`; `tests/test_url_validator.py` |
| 3 | CTA is a valid, safe URL (http/https only) | Met | `tests/test_url_validator.py` (schemes, credentials, localhost, malformed hosts) |
| 4 | Platform selection is internal | Met | unknown request fields rejected with 422 `UNKNOWN_FIELD`; registry decides |
| 5 | Invalid input returns structured errors | Met | HTTP 400 and 422 with one error envelope; recorded in `docs/API_EXAMPLES.md` |
| 6 | At least five platforms researched | Met | `docs/PLATFORM_API_RESEARCH.md` (seven researched, five implemented, Reddit rejected with reasons) |
| 7 | Official API documentation referenced | Partially met | official URLs are listed; several facts were cross-checked through secondary guides, and a few items are still marked "verify" (Telegram `retry_after` location and 404 meaning, LinkedIn post URL format) |
| 8 | At least five real adapters | Met in code | Mastodon, Bluesky, Telegram, LinkedIn, Discord follow the documented endpoints; tested with mocked HTTP |
| 9 | Message published automatically | Met | live Discord run; other platforms tested with mocked HTTP |
| 10 | Each platform runs independently | Met | per-platform deadline, bounded concurrency; `tests/test_posting_service.py` |
| 11 | One failure never stops the others | Met | `tests/test_integration_posting.py` (five real adapters, mocked network) |
| 12 | Actual results collected per platform | Met for Discord live | other results come from mocked platform responses |
| 13 | Result fields: platform, status, posted_url, public_access, date, time, error | Met | enforced by `PlatformResult`; recorded live output for Discord |
| 14 | Real platform URLs, never invented | Met for Discord live | verified jump link; Bluesky and LinkedIn URLs derive from identifiers the platform returns and are mock-tested only |
| 15 | Public access reported honestly | Met by design | true only with evidence; Discord always `false`; live for Discord only |
| 16 | Timestamps accurate, timezone-aware (UTC) | Met | matches Discord's displayed times (India time minus 5:30) |
| 17 | Failures carry useful safe errors | Met | fixed messages; no response bodies, secrets or stack traces |
| 18 | Retries safe against duplicates | Met | idempotency keys for Mastodon; ambiguous failures never retried elsewhere |

## Infrastructure and deliverables

| Requirement | Status | Evidence |
|---|---|---|
| FastAPI on port 4000, `POST /posting` | Met | `python -m app`; recorded runs |
| Swagger (`/docs`) and ReDoc (`/redoc`) | Met | screenshot; `tests/test_app_lifecycle.py` |
| Pydantic request and response schemas | Met | `app/schemas/` |
| Modular adapter architecture | Met | `docs/ARCHITECTURE.md` |
| Credentials in environment variables, `.env.example` placeholders only | Met | `tests/test_repo_hygiene.py` |
| Proper exception handling | Met | `app/api/errors.py`, `app/core/exceptions.py` |
| Requirements files | Met | `requirements.txt`, `requirements-dev.txt`, `requirements-lock.txt` |
| Automated tests | Met | 579 tests, 100% statement and branch coverage, no test publishes or needs credentials |
| README, architecture document, API examples | Met | `README.md`, `docs/` |
| Sample requests and responses | Met | `README.md`, `docs/API_EXAMPLES.md` |
| Screenshots or execution logs | Partially met | real evidence for Discord only (`docs/EXECUTION_EVIDENCE.md`) |
| Public GitHub repository | Met | unauthenticated request returned HTTP 200 |
| No credentials in the repository or its history | Met | history audits printed nothing |
| Submission email | Not yet | Step 14 |