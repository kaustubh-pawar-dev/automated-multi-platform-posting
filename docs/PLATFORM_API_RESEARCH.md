# Platform API Research

Research date: 16 November 2026. Purpose: choose at least five platforms with a legitimate, documented API for publishing a short text post containing one link, and record honestly what works and what does not.

**Evidence level.** Facts below were cross-checked against vendor documentation mirrors and developer guides during research. Items marked **verify** were not confirmed against the vendor's own page and must be re-checked before the integration relies on them (done in Steps 5 and 6).

## 1. Comparison table

| Platform | Official API | Authentication | Publishing Endpoint | Public URL Retrieval | Requirements | Integration Status |
|---|---|---|---|---|---|---|
| Mastodon | Yes (docs.joinmastodon.org) | OAuth 2 bearer token; token generated in the instance under Preferences > Development; scope `write:statuses` | `POST https://{instance}/api/v1/statuses` | `url` field in the returned Status object | Account on any instance; app + token | **Selected** |
| Bluesky | Yes (docs.bsky.app, AT Protocol) | Handle + app password via `createSession`, then `Authorization: Bearer <accessJwt>` | `POST https://bsky.social/xrpc/com.atproto.repo.createRecord`, collection `app.bsky.feed.post` | Response gives `at://` URI + CID; web URL is derived from handle/DID + record key (**verify** format) | Account + app password; no approval | **Selected** |
| Telegram | Yes (core.telegram.org/bots/api) | Bot token in URL path | `POST https://api.telegram.org/bot<token>/sendMessage` | Returns `message_id` and chat info; `https://t.me/<username>/<message_id>` exists only for **public** channels with a username | Bot from @BotFather; channel; bot added as admin with "Post Messages" | **Selected** |
| LinkedIn | Yes (Microsoft Learn: LinkedIn Posts API) | OAuth 2 authorization-code flow; scopes `openid profile w_member_social`; access token valid about 60 days (**verify**, refresh behaviour for self-serve apps unconfirmed) | `POST https://api.linkedin.com/rest/posts` with headers `LinkedIn-Version: YYYYMM` and `X-Restli-Protocol-Version: 2.0.0` | Post URN in `x-restli-id` response header; web URL derived from URN (**verify**) | Developer app associated with a LinkedIn Page; products "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect" (both self-serve). Posting as a Company Page needs `w_organization_social` and approval, so out of scope. Reported limits: 150 posts per member per day, 100,000 per app per day | **Selected** |
| WordPress | Yes (developer.wordpress.org/rest-api; WordPress.com: developer.wordpress.com/docs/api) | Self-hosted: Application Password (HTTP Basic). WordPress.com: OAuth 2 token (no Application Passwords) | Self-hosted: `POST https://{site}/wp-json/wp/v2/posts`. WordPress.com: `public-api.wordpress.com` | `link` field in the returned post object | A site we control. Posts need a title (derived by us, decided in Step 6) | **Selected** (variant pending) |
| Discord | Yes (discord.com/developers/docs) | Webhook URL contains the secret token | `POST https://discord.com/api/webhooks/{id}/{token}?wait=true` | Message `id`/`channel_id` returned; a jump link also needs the guild id (**verify** how to obtain). Server channels are generally not publicly accessible | Server where we can create a webhook. Content max 2000 chars; about 5 requests per 2 seconds per webhook | **Reserve (6th adapter)** |
| Reddit | Yes (reddit.com/dev/api) | OAuth 2 | `POST https://oauth.reddit.com/api/submit` | `url` in submit response | New API access requires manual approval under Reddit's Responsible Builder Policy; reports describe weeks of waiting and silent denials; app creation fails for many applicants | **Rejected: not feasible before the deadline** |

## 2. Why Reddit is not implemented

Since November 2025, Reddit requires approval under its Responsible Builder Policy before new apps can use the API. Multiple developer reports describe manual review taking weeks, denials without reasons, and the app-creation form refusing to proceed. We cannot obtain working credentials in the available time, and writing an adapter that can never be exercised would violate the rule against pretending an integration works. It remains a documented future improvement.

## 3. Per-platform notes

### Mastodon
- Scope needed: `write:statuses`.
- `Idempotency-Key` header prevents duplicate statuses; keys are stored up to 1 hour. We will send one key per publish so retries cannot duplicate.
- Visibility is returned in the Status (`visibility`); `public` means publicly accessible.
- Character limit is instance-specific (500 by default, **verify**); our 200-character cap fits.
- Rate limits: **verify**.

### Bluesky
- Posting writes a record into the account's repository. There is no central posting endpoint.
- Post text limit is 300 graphemes (**verify**); our cap fits.
- A bare URL in text is not clickable unless a link facet with UTF-8 byte offsets is added; the adapter will add one.
- `createRecord` has no idempotency header (**verify**). Retry policy will therefore avoid retrying after ambiguous failures.
- `createSession` is rate limited more strictly than other calls, so the adapter will reuse and refresh the session instead of logging in per request (limits: **verify**).

### Telegram
- Bot must be an administrator of the channel with "Post Messages".
- Channel can be addressed by `@username` or numeric chat id.
- No idempotency mechanism. Retry only on explicit `429` (honouring `retry_after`), never after a timeout, to avoid duplicate posts.
- Private channels have no public link: such a post is reported as success with no public URL and `public_access: false`.
- Message limit 4096 characters.

### LinkedIn
- Personal profile posting is self-serve. Author is `urn:li:person:{sub}`, where `sub` comes from the OpenID `userinfo` call.
- The text field is "little text" format in which some characters are reserved and must be escaped (**verify**; important because URLs may contain such characters).
- A successful create returns 201 with the URN in a response header. A 2xx must never be retried.
- Tokens expire (about 60 days). The one-time authorization is done with a helper script; renewal needs manual re-authorization unless refresh tokens are available to us (**verify**).
- `LinkedIn-Version` is required, versions are sunset after at least a year, and a missing or stale version can produce errors.

### WordPress
- Self-hosted: Application Passwords (core since WordPress 5.6) over HTTPS, with `status: publish`.
- WordPress.com: Application Passwords are not supported; OAuth2 is required, and some routes depend on the plan.
- Public access is true only when the returned status is `publish` and the site is not private.

### Discord (reserve)
- Webhook execution with `?wait=true` returns the created message; without it the response is 204 with no body.
- Rate limit responses return `retry_after`.
- We cannot assert public accessibility of a server channel, so `public_access` will be false unless proven otherwise.

## 4. Public access policy (applies to all adapters)

`public_access` is `true` only when the platform response or our own request proves it (for example Mastodon `visibility: public`, LinkedIn visibility set to `PUBLIC` in our request, WordPress status `publish`). Otherwise it is `false`. We never assume.

## 5. Credentials required (create these now)

| Platform | What to create | Scopes / permissions |
|---|---|---|
| Mastodon | Account on an instance; Preferences > Development > New application | `write:statuses` only |
| Bluesky | Account; Settings > Privacy and security > App passwords | App password (not your main password) |
| Telegram | Bot via @BotFather; a channel (public recommended); add bot as admin | "Post Messages" |
| LinkedIn | Developer app linked to a LinkedIn Page; add products "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect"; redirect URL | `openid profile w_member_social` |
| WordPress | Self-hosted: user + Application Password. WordPress.com: OAuth app + token | Ability to publish posts |
| Discord (optional) | Webhook in a server channel | None beyond the webhook |

Exact environment variable names are defined in Step 5.

## 6. Overall result

Five platforms are feasible with documented, self-serve access: Mastodon, Bluesky, Telegram, LinkedIn, WordPress. Discord is a sixth, low-risk reserve. Reddit is documented and rejected. If any selected platform cannot be authorized in time, the adapter stays implemented and tested with mocks, and the README will state plainly that no real publication was verified for it.

## 7. Official documentation to verify against

- Mastodon: https://docs.joinmastodon.org/methods/statuses/
- Bluesky: https://docs.bsky.app/ (posting guide, rate limits)
- Telegram: https://core.telegram.org/bots/api#sendmessage
- LinkedIn: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- WordPress: https://developer.wordpress.org/rest-api/reference/posts/
- WordPress.com: https://developer.wordpress.com/docs/api/
- Discord: https://discord.com/developers/docs/resources/webhook
- Reddit: https://support.reddithelp.com/ (Responsible Builder Policy)


## 8. Step 5 implementation notes

- **Mastodon:** a fresh `Idempotency-Key` is generated once per publication and re-sent on retries, so retrying even an ambiguous failure cannot duplicate the status. The request sets `visibility: public`, and `public_access` comes from the visibility the server returns.
- **Bluesky:** `createRecord` has no idempotency mechanism, so ambiguous failures (read timeouts, 5xx) are never retried. The session is cached; an expired-token response (HTTP 400 or 401 with error `ExpiredToken` or `InvalidToken`, **verify against the live server in Step 13**) causes exactly one new login and one retry, which is safe because nothing was created. `posted_url` is derived from the handle and the record key of the returned `at://` URI. `public_access` is true because AT Protocol records are public; an account can still hide itself from logged-out viewers on bsky.app, which the API does not reveal.


## 9. Step 6 implementation notes

- **Telegram:** the bot token travels in the URL path, so `httpx`/`httpcore` request logging is silenced at application start (a test enforces it) and errors never contain URLs or response bodies. No idempotency exists, so ambiguous failures are never retried. A post in a chat without a public username is reported as a success with no URL and `public_access: false`. `retry_after` is read from the JSON body (`parameters.retry_after`); an HTTP 404 is treated as an unrecognised bot token. Both behaviours come from my knowledge of the Bot API and are **verify** items for Step 13.
- **LinkedIn:** the 15 reserved little-text characters (`| { } @ [ ] ( ) < > # \ * _ ~`) are escaped, as required by LinkedIn's official little text format page. Self-serve tokens last about 60 days and cannot be refreshed in the background (confirmed by several developer guides; refresh tokens are limited to approved partners), so `scripts/linkedin_authorize.py` must be re-run and the optional `LINKEDIN_TOKEN_EXPIRES_AT` makes the adapter fail fast with a clear message instead of calling LinkedIn with a dead token. The post URL pattern `https://www.linkedin.com/feed/update/<urn>/` comes from developer guides rather than LinkedIn's own documentation (**verify** with a real post). `public_access` reflects the requested `PUBLIC` visibility; LinkedIn may still show logged-out visitors a login page. A 2xx response without a usable `x-restli-id` header is treated as ambiguous and never retried, because the post may exist.
- **Not covered by automated tests:** `scripts/linkedin_authorize.py` talks to LinkedIn interactively and is verified manually in Step 13.


## 10. Step 8b implementation notes (fifth adapter: Discord)

- **Discord:** `POST https://discord.com/api/webhooks/{id}/{token}?wait=true` returns the created message (`id`, `channel_id`, `timestamp`); without `wait=true` Discord answers 204 with no body, so a response without a message is treated as ambiguous and never retried. `content` is limited to 2000 characters, far above our 200-character cap. Rate limits return 429 with `retry_after` (seconds) in the JSON body. All messages are sent with `allowed_mentions: {"parse": []}` so `@everyone`, roles and users can never be pinged.
- **Honest limits:** webhook posts go to a server channel that is not publicly accessible, so `public_access` is always `false`. The message object from this endpoint does not show a `guild_id` in the samples reviewed, so the jump link `https://discord.com/channels/<guild>/<channel>/<message>` is built only when `DISCORD_GUILD_ID` is configured (link format verified against a real post on 19 November 2026); otherwise the post succeeds with `posted_url: null`.
- **Security:** the webhook URL contains a secret token, so it is a `SecretStr`, validated at startup, never logged, and requests are restricted to `discord.com` and `discordapp.com` regardless of configuration.
- **Platform count:** with Discord the project integrates five platforms (Mastodon, Bluesky, Telegram, LinkedIn, Discord), meeting the assignment minimum. Reddit remains documented and rejected; WordPress remains a possible sixth adapter.