"""Charles Schwab Trader API market data with automatic Yahoo fallback.

Schwab's Market Data API serves real-time quotes and option chains (with
greeks and IV), which are far more accurate than Yahoo's delayed scrape —
especially option bid/ask spreads, which drive the liquidity screen and all
strategy pricing. Schwab has no earnings-history endpoint, so those calls
always delegate to the Yahoo-backed fallback provider.

Auth model (Schwab constraint, not ours): access tokens last ~30 minutes and
are refreshed here automatically; refresh tokens expire after 7 days and can
only be renewed interactively — run ``scripts/schwab_auth.py`` weekly. When
the token has lapsed (or Schwab errors), every call falls back to Yahoo, so
a missed re-auth degrades data quality but never breaks the run.

Token JSON format (written by the auth script):
    {"access_token": "...", "refresh_token": "...", "expires_at": 1754700000}
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import time
from typing import Dict, List, Optional

from .models import OptionChain, OptionContract, PriceBar, QuarterResult, Snapshot
from .provider import MarketDataProvider

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
MARKET_DATA_BASE = "https://api.schwabapi.com/marketdata/v1"

# How far out to request option expirations in one chains call.
CHAIN_HORIZON_DAYS = 60


def schwab_symbol(ticker: str) -> str:
    """Yahoo-normalized symbols use a dash for share classes (BRK-B);
    Schwab uses a slash (BRK/B)."""
    return ticker.replace("-", "/")


# --------------------------------------------------------------------------- #
# Pure response parsers (testable without network)
# --------------------------------------------------------------------------- #
def bars_from_candles(payload: dict) -> List[PriceBar]:
    """Parse a ``/pricehistory`` response into daily :class:`PriceBar` rows."""
    bars: List[PriceBar] = []
    for candle in (payload or {}).get("candles", []):
        ms = candle.get("datetime")
        close = candle.get("close")
        if ms is None or close is None or close <= 0:
            continue
        day = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).date()
        bars.append(
            PriceBar(
                day=day,
                open=candle.get("open") or close,
                high=candle.get("high") or close,
                low=candle.get("low") or close,
                close=close,
                volume=candle.get("volume") or 0.0,
            )
        )
    return bars


def _contract(row: dict, option_type: str, expiry: dt.date) -> Optional[OptionContract]:
    strike = row.get("strikePrice")
    if strike is None:
        return None
    iv = row.get("volatility")
    # Schwab reports IV as a percentage (27.5); our convention is decimal.
    # Sentinel "NaN"/-999 values appear on stale contracts — drop them.
    if iv is not None:
        try:
            iv = float(iv) / 100.0
        except (TypeError, ValueError):
            iv = None
        if iv is not None and (iv != iv or iv <= 0 or iv > 10):
            iv = None
    return OptionContract(
        contract_symbol=str(row.get("symbol", "")),
        option_type=option_type,
        strike=float(strike),
        expiry=expiry,
        bid=row.get("bid"),
        ask=row.get("ask"),
        last=row.get("last"),
        close_price=row.get("closePrice"),
        day_change=row.get("netChange"),
        implied_volatility=iv,
        volume=row.get("totalVolume"),
        open_interest=row.get("openInterest"),
        in_the_money=row.get("inTheMoney"),
    )


def chains_from_payload(payload: dict, ticker: str) -> Dict[dt.date, OptionChain]:
    """Parse a ``/chains`` response into one :class:`OptionChain` per expiry.

    Schwab keys the maps as ``"YYYY-MM-DD:<dte>" -> {strike: [contract]}``.
    """
    underlying = (payload or {}).get("underlyingPrice") or 0.0
    chains: Dict[dt.date, OptionChain] = {}

    def _ingest(exp_map: dict, option_type: str) -> None:
        for exp_key, strikes in (exp_map or {}).items():
            try:
                expiry = dt.date.fromisoformat(str(exp_key).split(":", 1)[0])
            except ValueError:
                continue
            chain = chains.get(expiry)
            if chain is None:
                chain = OptionChain(
                    ticker=ticker, expiry=expiry, underlying_price=underlying
                )
                chains[expiry] = chain
            bucket = chain.calls if option_type == "call" else chain.puts
            for rows in (strikes or {}).values():
                for row in rows or []:
                    contract = _contract(row, option_type, expiry)
                    if contract is not None:
                        bucket.append(contract)

    _ingest(payload.get("callExpDateMap"), "call")
    _ingest(payload.get("putExpDateMap"), "put")
    return chains


# --------------------------------------------------------------------------- #
# OAuth session
# --------------------------------------------------------------------------- #
class SchwabAuthError(RuntimeError):
    """Token is missing/expired and cannot be refreshed non-interactively."""


class SchwabSession:
    """Bearer-token HTTP session with automatic access-token refresh."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        token_json: str = "",
        token_file: str = "",
        timeout: int = 20,
    ) -> None:
        if not app_key or not app_secret:
            raise SchwabAuthError("SCHWAB_APP_KEY / SCHWAB_APP_SECRET not set")
        self.app_key = app_key
        self.app_secret = app_secret
        self.token_file = token_file
        self.timeout = timeout
        self._token = self._load_token(token_json, token_file)

    @staticmethod
    def _load_token(token_json: str, token_file: str) -> dict:
        raw = token_json
        if not raw and token_file and os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8") as fh:
                raw = fh.read()
        if not raw:
            raise SchwabAuthError("no Schwab token (SCHWAB_TOKEN / SCHWAB_TOKEN_FILE)")
        try:
            token = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SchwabAuthError(f"Schwab token is not valid JSON: {exc}") from exc
        if "refresh_token" not in token:
            raise SchwabAuthError("Schwab token has no refresh_token")
        return token

    def _basic_auth(self) -> str:
        raw = f"{self.app_key}:{self.app_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _access_token(self) -> str:
        expires_at = self._token.get("expires_at", 0)
        if self._token.get("access_token") and time.time() < expires_at - 60:
            return self._token["access_token"]
        return self._refresh()

    def _refresh(self) -> str:
        import requests

        response = requests.post(
            TOKEN_URL,
            headers={
                "Authorization": self._basic_auth(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._token["refresh_token"],
            },
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise SchwabAuthError(
                f"token refresh failed ({response.status_code}): the 7-day "
                "refresh token has likely expired — run scripts/schwab_auth.py"
            )
        fresh = response.json()
        self._token["access_token"] = fresh["access_token"]
        self._token["refresh_token"] = fresh.get(
            "refresh_token", self._token["refresh_token"]
        )
        self._token["expires_at"] = int(time.time()) + int(
            fresh.get("expires_in", 1800)
        )
        if self.token_file:
            try:
                with open(self.token_file, "w", encoding="utf-8") as fh:
                    json.dump(self._token, fh)
            except OSError as exc:  # noqa: PERF203 - best effort
                logger.debug("could not persist refreshed token: %s", exc)
        return self._token["access_token"]

    def get(self, path: str, params: dict) -> dict:
        import requests

        response = requests.get(
            MARKET_DATA_BASE + path,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            params=params,
            timeout=self.timeout,
        )
        if response.status_code == 401:
            # Access token revoked mid-session — refresh once and retry.
            response = requests.get(
                MARKET_DATA_BASE + path,
                headers={"Authorization": f"Bearer {self._refresh()}"},
                params=params,
                timeout=self.timeout,
            )
        elif response.status_code in (502, 503, 504):
            # Transient server-side hiccup — one retry after a short pause.
            time.sleep(2)
            response = requests.get(
                MARKET_DATA_BASE + path,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                params=params,
                timeout=self.timeout,
            )
        response.raise_for_status()
        return response.json()


# --------------------------------------------------------------------------- #
# Hybrid provider
# --------------------------------------------------------------------------- #
class SchwabMarketData:
    """Schwab for quotes/chains/history; Yahoo fallback for everything else.

    Any Schwab failure disables the session for the rest of the run and
    silently serves the fallback — degraded data beats a broken email.
    """

    def __init__(self, session: SchwabSession, fallback: MarketDataProvider) -> None:
        self._session = session
        self._fallback = fallback
        self._disabled = False
        self._chain_cache: Dict[str, Dict[dt.date, OptionChain]] = {}

    def _schwab(self, what: str, fn):
        if self._disabled:
            return None
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Schwab %s failed (%s); falling back to Yahoo for the rest "
                "of this run",
                what,
                exc,
            )
            self._disabled = True
            return None

    # --- Schwab has no earnings data; always delegate. ---
    def earnings_history(self, ticker: str, limit: int = 20) -> List[QuarterResult]:
        return self._fallback.earnings_history(ticker, limit)

    def snapshot(self, ticker: str) -> Optional[Snapshot]:
        return self._fallback.snapshot(ticker)

    # --- Real-time-capable paths. ---
    def price_history(self, ticker: str, days: int = 700) -> List[PriceBar]:
        def _fetch():
            payload = self._session.get(
                "/pricehistory",
                {
                    "symbol": schwab_symbol(ticker),
                    "periodType": "year",
                    "period": 2,
                    "frequencyType": "daily",
                    "frequency": 1,
                },
            )
            return bars_from_candles(payload) or None

        bars = self._schwab(f"price history {ticker}", _fetch)
        return bars if bars else self._fallback.price_history(ticker, days)

    def _chains(self, ticker: str) -> Optional[Dict[dt.date, OptionChain]]:
        if ticker in self._chain_cache:
            return self._chain_cache[ticker]

        def _fetch():
            today = dt.date.today()
            payload = self._session.get(
                "/chains",
                {
                    "symbol": schwab_symbol(ticker),
                    "contractType": "ALL",
                    "strategy": "SINGLE",
                    "fromDate": today.isoformat(),
                    "toDate": (
                        today + dt.timedelta(days=CHAIN_HORIZON_DAYS)
                    ).isoformat(),
                },
            )
            return chains_from_payload(payload, ticker) or None

        chains = self._schwab(f"option chains {ticker}", _fetch)
        if chains:
            self._chain_cache[ticker] = chains
        return chains

    def option_expiries(self, ticker: str) -> List[dt.date]:
        chains = self._chains(ticker)
        if chains:
            return sorted(chains)
        return self._fallback.option_expiries(ticker)

    def option_chain(self, ticker: str, expiry: dt.date) -> Optional[OptionChain]:
        chains = self._chains(ticker)
        if chains:
            chain = chains.get(expiry)
            if chain is not None:
                return chain
        return self._fallback.option_chain(ticker, expiry)


def build_provider(config, today: Optional[dt.date] = None) -> MarketDataProvider:
    """The production provider: Schwab-over-Yahoo when configured, else Yahoo."""
    from .provider import YFinanceMarketData

    yahoo = YFinanceMarketData(today=today)
    if not (config.schwab_app_key and config.schwab_app_secret):
        return yahoo
    try:
        session = SchwabSession(
            app_key=config.schwab_app_key,
            app_secret=config.schwab_app_secret,
            token_json=config.schwab_token,
            token_file=config.schwab_token_file,
        )
    except SchwabAuthError as exc:
        logger.warning("Schwab not usable (%s); using Yahoo only", exc)
        return yahoo
    logger.info("Using Schwab market data with Yahoo fallback")
    return SchwabMarketData(session, yahoo)
