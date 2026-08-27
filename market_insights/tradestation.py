"""TradeStation Market Data API: OAuth session plus pure payload parsers.

TradeStation serves consolidated-tape volume, which is what makes this
package possible — an unusual-volume screen needs the *whole* day's shares,
not one venue's. Two endpoints carry the scan:

``/marketdata/quotes/{symbols}``
    Up to 100 symbols per request, so the entire S&P 500 costs ~5 calls.
    Used as a cheap first pass to find volume candidates.
``/marketdata/barcharts/{symbol}``
    Daily OHLCV history, one call per symbol. Used only on the candidates
    that survive the first pass, since 500 of these would take minutes.

Auth model (TradeStation's, not ours): access tokens last ~20 minutes and
are refreshed here automatically. Unlike Schwab's 7-day refresh token, a
TradeStation refresh token issued with the ``offline_access`` scope does not
expire, so ``scripts/tradestation_auth.py`` is a one-time setup rather than
a weekly chore.

Numbers come back as JSON *strings* on these endpoints ("Close": "123.45"),
and absent values as empty strings, so every field goes through :func:`_num`.

Token JSON format (written by the auth script):
    {"refresh_token": "...", "access_token": "...", "expires_at": 0}
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from earnings_analyzer.models import PriceBar

logger = logging.getLogger(__name__)

SIGNIN_BASE = "https://signin.tradestation.com"
TOKEN_URL = f"{SIGNIN_BASE}/oauth/token"
AUTHORIZE_URL = f"{SIGNIN_BASE}/authorize"

LIVE_BASE = "https://api.tradestation.com/v3"
SIM_BASE = "https://sim-api.tradestation.com/v3"

# Market data needs only MarketData; offline_access is what mints a refresh
# token in the first place.
SCOPES = "openid offline_access MarketData"

# TradeStation allows 120 market-data requests/minute per user.
MAX_REQUESTS_PER_MINUTE = 120

# The quotes endpoint takes at most 100 symbols in one URL.
QUOTE_BATCH_SIZE = 100

EASTERN = ZoneInfo("America/New_York")


def api_base(environment: str) -> str:
    """``"sim"`` selects the simulation host; anything else is live."""
    return SIM_BASE if (environment or "").strip().lower() in {"sim", "simulation"} else LIVE_BASE


def tradestation_symbol(ticker: str) -> str:
    """Yahoo-normalized symbols use a dash for share classes (BRK-B);
    TradeStation uses a dot (BRK.B)."""
    return ticker.replace("-", ".")


def yahoo_symbol(symbol: str) -> str:
    """Inverse of :func:`tradestation_symbol`, for keying results back."""
    return symbol.replace(".", "-")


def _num(value) -> Optional[float]:
    """Coerce TradeStation's stringly-typed numerics; "" and junk -> None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out    # drop NaN


def _parse_timestamp(raw) -> Optional[dt.date]:
    """``"2026-08-21T20:00:00Z"`` -> the Eastern session date.

    Daily bars are stamped at the session close in UTC (20:00Z on EDT,
    21:00Z on EST), so converting to Eastern before taking the date keeps
    the bar on its true trading day either side of the DST switch.
    """
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(EASTERN).date()


# --------------------------------------------------------------------------- #
# Pure response parsers (testable without network)
# --------------------------------------------------------------------------- #
def bars_from_payload(payload: dict) -> List[PriceBar]:
    """Parse a ``/barcharts`` response into daily :class:`PriceBar` rows.

    Rows without a usable close are dropped rather than zero-filled: a
    phantom 0.00 close would poison every moving average downstream.
    """
    bars: List[PriceBar] = []
    for row in (payload or {}).get("Bars", []) or []:
        day = _parse_timestamp(row.get("TimeStamp"))
        close = _num(row.get("Close"))
        if day is None or close is None or close <= 0:
            continue
        bars.append(
            PriceBar(
                day=day,
                open=_num(row.get("Open")) or close,
                high=_num(row.get("High")) or close,
                low=_num(row.get("Low")) or close,
                close=close,
                volume=_num(row.get("TotalVolume")) or 0.0,
            )
        )
    bars.sort(key=lambda b: b.day)
    return bars


def last_bar_is_open(payload: dict) -> bool:
    """True when the final bar is still forming (an in-progress session).

    TradeStation marks it with ``BarStatus: "Open"``; that flag is the
    difference between "this stock traded 3x its normal volume" and "this
    stock is 40% of the way through a normal day".
    """
    rows = (payload or {}).get("Bars", []) or []
    if not rows:
        return False
    return str(rows[-1].get("BarStatus", "")).strip().lower() == "open"


@dataclass
class Quote:
    """A snapshot quote — the fields the volume screen actually reads."""

    symbol: str
    last: Optional[float] = None
    volume: Optional[float] = None            # shares so far today
    previous_volume: Optional[float] = None   # prior session's total
    previous_close: Optional[float] = None
    net_change_pct: Optional[float] = None
    high_52week: Optional[float] = None
    low_52week: Optional[float] = None

    @property
    def change_pct(self) -> Optional[float]:
        """Day change as a decimal (0.031 = +3.1%).

        Prefers the feed's own NetChangePct (a percentage number) and falls
        back to computing it from the previous close.
        """
        if self.net_change_pct is not None:
            return self.net_change_pct / 100.0
        if self.last and self.previous_close:
            return (self.last - self.previous_close) / self.previous_close
        return None


def quotes_from_payload(payload: dict) -> Dict[str, Quote]:
    """Parse a ``/quotes`` response, keyed by Yahoo-style ticker.

    Symbols the feed rejects arrive under ``Errors`` and are simply absent
    from the result; callers treat a missing quote as "skip this name".
    """
    quotes: Dict[str, Quote] = {}
    for row in (payload or {}).get("Quotes", []) or []:
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol:
            continue
        quotes[yahoo_symbol(symbol)] = Quote(
            symbol=symbol,
            last=_num(row.get("Last")),
            volume=_num(row.get("Volume")),
            previous_volume=_num(row.get("PreviousVolume")),
            previous_close=_num(row.get("PreviousClose")),
            net_change_pct=_num(row.get("NetChangePct")),
            high_52week=_num(row.get("High52Week")),
            low_52week=_num(row.get("Low52Week")),
        )
    return quotes


# --------------------------------------------------------------------------- #
# OAuth session
# --------------------------------------------------------------------------- #
class TradeStationAuthError(RuntimeError):
    """Token is missing/expired and cannot be refreshed non-interactively."""


class TradeStationSession:
    """Bearer-token HTTP session with automatic access-token refresh."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_json: str = "",
        token_file: str = "",
        environment: str = "live",
        timeout: int = 20,
    ) -> None:
        if not client_id:
            raise TradeStationAuthError("TRADESTATION_CLIENT_ID not set")
        if not client_secret:
            # Refreshing without a secret returns a bare 401 access_denied,
            # which reads like an expired token rather than a config gap.
            # Legitimate for a public/PKCE client, so warn rather than raise.
            logger.warning(
                "TRADESTATION_CLIENT_SECRET is not set — token refresh will "
                "fail with 401 unless this app is a public (PKCE) client"
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = token_file
        self.base = api_base(environment)
        self.timeout = timeout
        self._token = self._load_token(token_json, token_file)

    @staticmethod
    def _load_token(token_json: str, token_file: str) -> dict:
        raw = (token_json or "").strip()
        if not raw and token_file and os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as fh:
                raw = fh.read()
        if not raw:
            raise TradeStationAuthError(
                "no TradeStation token (TRADESTATION_TOKEN / "
                "TRADESTATION_TOKEN_FILE) — run scripts/tradestation_auth.py"
            )
        # A bare refresh token is accepted as a convenience: it is the only
        # part that must be carried between runs.
        if not raw.lstrip().startswith("{"):
            return {"refresh_token": raw}
        try:
            token = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TradeStationAuthError(
                f"TradeStation token is not valid JSON: {exc}"
            ) from exc
        if not token.get("refresh_token"):
            raise TradeStationAuthError("TradeStation token has no refresh_token")
        return token

    def _access_token(self) -> str:
        expires_at = self._token.get("expires_at", 0)
        if self._token.get("access_token") and time.time() < expires_at - 60:
            return self._token["access_token"]
        return self._refresh()

    def _refresh(self) -> str:
        import requests

        # TradeStation expects the client credentials in the form body, not
        # in a Basic auth header (Schwab does the opposite).
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": self._token["refresh_token"],
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        response = requests.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise TradeStationAuthError(
                f"token refresh failed ({response.status_code}): re-authorize "
                "with scripts/tradestation_auth.py"
            )
        fresh = response.json()
        self._token["access_token"] = fresh["access_token"]
        # TradeStation normally returns the same refresh token; honour a
        # rotated one if it ever sends a new one.
        self._token["refresh_token"] = fresh.get(
            "refresh_token", self._token["refresh_token"]
        )
        self._token["expires_at"] = int(time.time()) + int(fresh.get("expires_in", 1200))
        if self.token_file:
            try:
                with open(self.token_file, "w", encoding="utf-8") as fh:
                    json.dump(self._token, fh)
            except OSError as exc:  # noqa: PERF203 - best effort
                logger.debug("could not persist refreshed token: %s", exc)
        return self._token["access_token"]

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        import requests

        url = self.base + path
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            params=params or {},
            timeout=self.timeout,
        )
        if response.status_code == 401:
            # Access token revoked mid-sweep — refresh once and retry.
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {self._refresh()}"},
                params=params or {},
                timeout=self.timeout,
            )
        response.raise_for_status()
        return response.json()

    # --- Endpoint wrappers ------------------------------------------------ #
    def bars(self, ticker: str, barsback: int) -> Tuple[List[PriceBar], bool]:
        """Daily bars for one ticker, plus whether the last one is partial."""
        payload = self.get(
            f"/marketdata/barcharts/{tradestation_symbol(ticker)}",
            {"interval": 1, "unit": "Daily", "barsback": barsback},
        )
        return bars_from_payload(payload), last_bar_is_open(payload)

    def quotes(self, tickers: List[str]) -> Dict[str, Quote]:
        """Snapshot quotes for up to :data:`QUOTE_BATCH_SIZE` tickers."""
        if not tickers:
            return {}
        symbols = ",".join(tradestation_symbol(t) for t in tickers)
        return quotes_from_payload(self.get(f"/marketdata/quotes/{symbols}"))


def build_session(config) -> Optional[TradeStationSession]:
    """The production session, or None when TradeStation isn't configured."""
    if not config.tradestation_client_id:
        return None
    try:
        session = TradeStationSession(
            client_id=config.tradestation_client_id,
            client_secret=config.tradestation_client_secret,
            token_json=config.tradestation_token,
            token_file=config.tradestation_token_file,
            environment=config.tradestation_environment,
        )
    except TradeStationAuthError as exc:
        logger.warning("TradeStation not usable (%s)", exc)
        return None
    logger.info("Using TradeStation market data (%s)", session.base)
    return session
