"""Interactive TradeStation OAuth login — run this once.

Unlike Schwab's 7-day refresh token, a TradeStation refresh token minted
with the ``offline_access`` scope keeps working indefinitely, so this is a
one-time setup rather than a weekly chore. Re-run it only if you revoke the
app or the refresh stops working.

Usage (from the repo root):
    python scripts/tradestation_auth.py --set-secret

Requires TRADESTATION_CLIENT_ID / TRADESTATION_CLIENT_SECRET, from (in
order): the --client-id/--client-secret flags, the environment, or a
KEY=VALUE env file — a ``.env`` in the current directory is loaded
automatically, or pass --env-file. The app's redirect URI in the
TradeStation developer portal must match --callback exactly.

Steps it performs:
  1. Prints the TradeStation authorize URL — open it, log in, approve.
  2. Your browser lands on the callback URL (the page won't load — that's
     expected). Copy the full address-bar URL and paste it back here.
  3. Exchanges the code for tokens and writes tradestation_token.json.
  4. With --set-secret, runs `gh secret set TRADESTATION_TOKEN`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote, urlparse

import requests

AUTHORIZE_URL = "https://signin.tradestation.com/authorize"
TOKEN_URL = "https://signin.tradestation.com/oauth/token"
# The audience tells the identity server which API the token is for; without
# it TradeStation issues an opaque token the market-data API rejects.
AUDIENCE = "https://api.tradestation.com"
SCOPES = "openid offline_access MarketData"


def load_env_file(path: str) -> bool:
    """Load KEY=VALUE lines into the environment (existing values win).

    Read as utf-8-sig: Notepad and PowerShell redirects write a UTF-8 BOM,
    which would otherwise glue itself to the first key and make that one
    line vanish while every other line worked.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key.startswith("export "):     # a shell habit, not a typo
                    key = key[len("export "):].strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        return False
    return True


def authorize_url(client_id: str, callback: str) -> str:
    return (
        f"{AUTHORIZE_URL}?response_type=code"
        f"&client_id={quote(client_id)}"
        f"&redirect_uri={quote(callback, safe='')}"
        f"&audience={quote(AUDIENCE, safe='')}"
        f"&scope={quote(SCOPES)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", default="")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--env-file", default=None,
                        help="KEY=VALUE file with the app credentials "
                             "(default: ./.env when present)")
    parser.add_argument("--callback", default="http://localhost",
                        help="must exactly match the app's registered redirect URI")
    parser.add_argument("--output", default="tradestation_token.json")
    parser.add_argument("--set-secret", action="store_true",
                        help="also push the token to the TRADESTATION_TOKEN "
                             "repo secret via gh")
    args = parser.parse_args()

    env_file = args.env_file or (".env" if os.path.exists(".env") else None)
    if env_file:
        if load_env_file(env_file):
            print(f"Loaded environment from {env_file}")
        elif args.env_file:
            print(f"Could not read {args.env_file}", file=sys.stderr)
            return 2
    args.client_id = args.client_id or os.environ.get("TRADESTATION_CLIENT_ID", "")
    args.client_secret = args.client_secret or os.environ.get(
        "TRADESTATION_CLIENT_SECRET", ""
    )

    if not args.client_id:
        print("Set TRADESTATION_CLIENT_ID (or use --client-id).", file=sys.stderr)
        if env_file:
            print(
                f"\n{env_file} was loaded but TRADESTATION_CLIENT_ID was not "
                "set by it. Check that the line:\n"
                "  - is not commented out (a leading '#' is ignored — the "
                "lines in .env.example are commented by default)\n"
                "  - reads TRADESTATION_CLIENT_ID=your-id, with no quotes "
                "needed\n"
                "  - is spelled exactly that way",
                file=sys.stderr,
            )
        return 2

    print("\n1. Open this URL, log in to TradeStation, and approve access:\n")
    print(f"   {authorize_url(args.client_id, args.callback)}\n")
    print("2. Your browser will be sent to the callback URL. The page will fail")
    print("   to load — that is expected. Copy the FULL address-bar URL.\n")
    pasted = input("3. Paste that URL here: ").strip()

    query = parse_qs(urlparse(pasted).query)
    codes = query.get("code")
    if not codes:
        print("No ?code= found in that URL — make sure you copied the whole thing.",
              file=sys.stderr)
        return 1

    data = {
        "grant_type": "authorization_code",
        "client_id": args.client_id,
        "code": codes[0],
        "redirect_uri": args.callback,
    }
    if args.client_secret:
        data["client_secret"] = args.client_secret
    response = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=30,
    )
    if response.status_code != 200:
        print(f"Token exchange failed ({response.status_code}): {response.text}",
              file=sys.stderr)
        return 1

    payload = response.json()
    if "refresh_token" not in payload:
        print("No refresh_token returned — the app must request the "
              "'offline_access' scope.", file=sys.stderr)
        return 1
    token = {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": int(time.time()) + int(payload.get("expires_in", 1200)),
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(token, fh)
    print(f"\nToken written to {args.output} (refresh token does not expire).")

    if args.set_secret:
        result = subprocess.run(
            ["gh", "secret", "set", "TRADESTATION_TOKEN"],
            input=json.dumps(token).encode(),
            check=False,
        )
        if result.returncode == 0:
            print("Pushed to the TRADESTATION_TOKEN repository secret.")
        else:
            print("gh secret set failed — push it manually:\n"
                  f"  gh secret set TRADESTATION_TOKEN < {args.output}",
                  file=sys.stderr)
            return 1
    else:
        print("To use it in GitHub Actions:  gh secret set TRADESTATION_TOKEN "
              f"< {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
