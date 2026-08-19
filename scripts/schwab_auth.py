"""Interactive Schwab OAuth login — run this once a week.

Schwab refresh tokens expire after 7 days and can only be renewed through a
browser login. This script walks the manual flow (no local web server
needed), writes the token JSON, and can push it straight into the GitHub
repository secret the workflow reads.

Usage (from the repo root):
    python scripts/schwab_auth.py --set-secret

Requires SCHWAB_APP_KEY / SCHWAB_APP_SECRET, from (in order): the
--app-key/--app-secret flags, the environment, or a KEY=VALUE env file —
a ``.env`` in the current directory is loaded automatically, or pass
--env-file. The app's callback URL on developer.schwab.com must match
--callback (default https://127.0.0.1).

Steps it performs:
  1. Prints the Schwab authorize URL — open it, log in, approve.
  2. Your browser lands on the callback URL (the page won't load — that's
     expected). Copy the full address-bar URL and paste it back here.
  3. Exchanges the code for tokens and writes schwab_token.json.
  4. With --set-secret, runs `gh secret set SCHWAB_TOKEN` with the JSON.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote, urlparse

import requests

AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


def load_env_file(path: str) -> bool:
    """Load KEY=VALUE lines into the environment (existing values win)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-key", default="")
    parser.add_argument("--app-secret", default="")
    parser.add_argument("--env-file", default=None,
                        help="KEY=VALUE file with the app credentials "
                             "(default: ./.env when present)")
    parser.add_argument("--callback", default="https://127.0.0.1",
                        help="must exactly match the app's registered callback URL")
    parser.add_argument("--output", default="schwab_token.json")
    parser.add_argument("--set-secret", action="store_true",
                        help="also push the token to the SCHWAB_TOKEN repo secret via gh")
    args = parser.parse_args()

    env_file = args.env_file or (".env" if os.path.exists(".env") else None)
    if env_file:
        if load_env_file(env_file):
            print(f"Loaded environment from {env_file}")
        elif args.env_file:
            print(f"Could not read {args.env_file}", file=sys.stderr)
            return 2
    args.app_key = args.app_key or os.environ.get("SCHWAB_APP_KEY", "")
    args.app_secret = args.app_secret or os.environ.get("SCHWAB_APP_SECRET", "")

    if not args.app_key or not args.app_secret:
        print("Set SCHWAB_APP_KEY / SCHWAB_APP_SECRET (or use --app-key/--app-secret).",
              file=sys.stderr)
        return 2

    url = (
        f"{AUTHORIZE_URL}?client_id={quote(args.app_key)}"
        f"&redirect_uri={quote(args.callback, safe='')}"
    )
    print("\n1. Open this URL, log in to Schwab, and approve access:\n")
    print(f"   {url}\n")
    print("2. Your browser will be sent to the callback URL. The page will fail")
    print("   to load — that is expected. Copy the FULL address-bar URL.\n")
    pasted = input("3. Paste that URL here: ").strip()

    query = parse_qs(urlparse(pasted).query)
    codes = query.get("code")
    if not codes:
        print("No ?code= found in that URL — make sure you copied the whole thing.",
              file=sys.stderr)
        return 1

    basic = base64.b64encode(f"{args.app_key}:{args.app_secret}".encode()).decode()
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": codes[0],
            "redirect_uri": args.callback,
        },
        timeout=30,
    )
    if response.status_code != 200:
        print(f"Token exchange failed ({response.status_code}): {response.text}",
              file=sys.stderr)
        return 1

    payload = response.json()
    token = {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": int(time.time()) + int(payload.get("expires_in", 1800)),
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(token, fh)
    print(f"\nToken written to {args.output} (refresh token valid ~7 days).")

    if args.set_secret:
        result = subprocess.run(
            ["gh", "secret", "set", "SCHWAB_TOKEN"],
            input=json.dumps(token).encode(),
            check=False,
        )
        if result.returncode == 0:
            print("Pushed to the SCHWAB_TOKEN repository secret.")
        else:
            print("gh secret set failed — push it manually:\n"
                  f"  gh secret set SCHWAB_TOKEN < {args.output}", file=sys.stderr)
            return 1
    else:
        print("To use it in GitHub Actions:  gh secret set SCHWAB_TOKEN "
              f"< {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
