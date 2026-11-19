# API examples

Base URL: `http://127.0.0.1:4000`. "Recorded" examples are real output from this project; "illustrative" ones show the format and are covered by automated tests.

## POST /posting

### All configured platforms succeed (recorded, 17 November 2026)

A live Discord publication; the other platforms were switched off for the run.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:4000/posting -ContentType "application/json" `
  -Body '{"message":"Check out our latest business platform and grow your online visibility. https://example.com"}'
```

```json
{"success":true,"total_platforms":1,"successful_count":1,"failed_count":0,"results":[{"platform":"Discord","status":"success","posted_url":null,"public_access":false,"date":"2026-11-17","time":"08:54:20","error":null}],"skipped_platforms":["Mastodon","Bluesky","Telegram","LinkedIn"]}
```

### With timings (recorded, 18 November 2026)

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
      "posted_url": null,
      "public_access": false,
      "date": "2026-11-18",
      "time": "03:00:09",
      "error": null
    }
  ],
  "skipped_platforms": [],
  "total_duration_ms": 2561,
  "platform_durations_ms": {"Discord": 2547}
}
```

### One platform fails (illustrative)

Exercised by `test_partial_failure_retries_only_where_it_is_safe`. The request still returns HTTP 200; the failed platform carries its own error and the others are unaffected.

```json
{
  "success": false,
  "total_platforms": 2,
  "successful_count": 1,
  "failed_count": 1,
  "results": [
    {"platform": "Mastodon", "status": "success", "posted_url": "https://mastodon.example/@user/110000000000000000", "public_access": true, "date": "2026-11-15", "time": "10:30:00", "error": null},
    {"platform": "LinkedIn", "status": "failed", "posted_url": null, "public_access": false, "date": null, "time": null, "error": "Authentication failed"}
  ],
  "skipped_platforms": [],
  "total_duration_ms": 1204,
  "platform_durations_ms": {"Mastodon": 611, "LinkedIn": 1204}
}
```

### Message has no link (recorded, 17 November 2026)

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:4000/posting -ContentType "application/json" -Body '{"message":"hello"}'
```

HTTP 400, header `X-Request-ID: 0bb0c98c80ab4b8ebc63127953a03c8a`:

```json
{"error":"validation_error","message":"The message failed validation.","details":[{"code":"MISSING_CTA_URL","message":"Message must contain exactly one http:// or https:// link; none was found."}],"request_id":"0bb0c98c80ab4b8ebc63127953a03c8a"}
```

Business-rule codes: `EMPTY_MESSAGE`, `MESSAGE_TOO_LONG`, `INVALID_CHARACTERS`, `MISSING_CTA_URL`, `MULTIPLE_CTA_URLS`, `UNSUPPORTED_URL_SCHEME`, `URL_HOST_NOT_PUBLIC`, `INVALID_URL`. Several can appear together.

### Client tries to choose platforms (recorded, 16 November 2026)

Recorded before request IDs were added to error bodies; `request_id` is now populated.

HTTP 422:

```json
{"error":"request_validation_error","message":"The request body is invalid.","details":[{"code":"UNKNOWN_FIELD","message":"platforms: Extra inputs are not permitted"}],"request_id":null}
```

### Missing `message` field (recorded, 16 November 2026)

HTTP 422 with code `FIELD_REQUIRED` and message `message: Field required`.

### No platform configured (recorded, 16 November 2026)

HTTP 503, recorded with every credential blanked and before request IDs were added to error bodies:

```json
{"error":"no_platforms_configured","message":"No publishing platforms are configured on this server.","details":[{"code":"PLATFORM_NOT_CONFIGURED","message":"Mastodon is not configured"},{"code":"PLATFORM_NOT_CONFIGURED","message":"Bluesky is not configured"},{"code":"PLATFORM_NOT_CONFIGURED","message":"Telegram is not configured"},{"code":"PLATFORM_NOT_CONFIGURED","message":"LinkedIn is not configured"}],"request_id":null}
```

## Custom request ID (recorded, 17 November 2026)

A valid `X-Request-ID` (8 to 64 characters of letters, digits, `.`, `_`, `-`) is reused in the response header and in every log line of that request; anything else is replaced.

```powershell
Invoke-WebRequest -UseBasicParsing -Method Post -Uri http://127.0.0.1:4000/posting `
  -ContentType "application/json" -Headers @{"X-Request-ID"="demo-request-001"} `
  -Body '{"message":"Logging demo https://example.com"}'
```

Result: HTTP 200 with `X-Request-ID: demo-request-001`.

## GET /health (recorded, 18 November 2026)

```json
{"status": "ok", "version": "0.1.0", "uptime_seconds": 88.7}
```

## GET /platforms (recorded, 18 November 2026)

Only Discord was enabled for this run. The response names missing variables and never shows values.

```json
{
  "platforms": [
    {"name": "Discord", "configured": true, "missing_settings": [], "idempotent_retries": false}
  ],
  "configured_count": 1,
  "total_count": 1,
  "disabled_platforms": ["Mastodon", "Bluesky", "Telegram", "LinkedIn"]
}
```