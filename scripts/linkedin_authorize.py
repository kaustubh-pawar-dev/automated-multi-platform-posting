"""One-time helper: obtain a LinkedIn access token and your member URN.

Usage (PowerShell, from the project root, venv active):

    $env:LINKEDIN_CLIENT_ID = "<Client ID from the LinkedIn app's Auth tab>"
    $env:LINKEDIN_CLIENT_SECRET = "<Primary Client Secret>"
    python scripts\\linkedin_authorize.py
    Remove-Item Env:LINKEDIN_CLIENT_ID, Env:LINKEDIN_CLIENT_SECRET

Prerequisites on the LinkedIn developer app:
* Products: "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect".
* Auth tab > Authorized redirect URLs contains exactly: http://localhost:8765/callback

The script opens your browser, receives the authorization code on localhost,
exchanges it for a token and prints the three lines to paste into your local
``.env``. Nothing is written to disk. Never share or commit the output.
"""

import os
import secrets
import sys
import webbrowser
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
CODE_EXCHANGE_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}/callback"
SCOPE = "openid profile w_member_social"
WAIT_SECONDS = 300


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the query string of the OAuth redirect."""

    params: dict[str, list[str]] = {}  # noqa: RUF012 - deliberately shared with main()

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        type(self).params = parse_qs(parts.query)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authorization received. You can close this tab.")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence request logging."""


def _wait_for_callback() -> dict[str, list[str]]:
    server = HTTPServer(("127.0.0.1", PORT), _CallbackHandler)
    server.timeout = WAIT_SECONDS
    try:
        for _ in range(5):
            server.handle_request()
            if _CallbackHandler.params:
                break
    finally:
        server.server_close()
    return _CallbackHandler.params


def main() -> int:
    client_id = os.environ.get("LINKEDIN_CLIENT_ID", "")
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET first (see the docstring).")
        return 2

    state = secrets.token_urlsafe(24)
    url = AUTHORIZE_URL + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "scope": SCOPE,
        }
    )
    print("Opening your browser. If it does not open, visit this URL:\n" + url + "\n")
    webbrowser.open(url)

    params = _wait_for_callback()
    if not params:
        print("No authorization response received before the timeout.")
        return 1
    if "error" in params:
        print("LinkedIn returned an error:", params["error"][0])
        return 1
    if not secrets.compare_digest(params.get("state", [""])[0], state):
        print("State mismatch; aborting for safety.")
        return 1
    code = params.get("code", [""])[0]
    if not code:
        print("No authorization code in the response.")
        return 1

    try:
        exchange_response = httpx.post(
            CODE_EXCHANGE_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        exchange_response.raise_for_status()
        exchange_data = exchange_response.json()
        access_token = exchange_data["access_token"]
        expires_in = int(exchange_data.get("expires_in", 0))

        info_response = httpx.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15
        )
        info_response.raise_for_status()
        subject = info_response.json()["sub"]
    except httpx.HTTPStatusError as exc:
        print(f"LinkedIn rejected the request (HTTP {exc.response.status_code}).")
        return 1
    except (httpx.RequestError, KeyError, ValueError):
        print("Could not complete the token exchange.")
        return 1

    expiry = datetime.now(UTC).date() + timedelta(seconds=max(expires_in, 0)) - timedelta(days=1)
    print("Success. Paste these three lines into your local .env (never share them):\n")
    print(f"LINKEDIN_ACCESS_TOKEN={access_token}")
    print(f"LINKEDIN_PERSON_URN=urn:li:person:{subject}")
    print(f"LINKEDIN_TOKEN_EXPIRES_AT={expiry.isoformat()}")
    print("\nThen clear this terminal (Clear-Host) so the token is not left on screen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())