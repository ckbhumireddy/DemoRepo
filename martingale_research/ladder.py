"""The generic ladder simulator (pure; one continuous replay).

Config keys (all optional except base and factor):
  base         step-0 stake: dollars, or a fraction of balance if pct=True
  pct          True -> base is a fraction of the current balance
  factor       stake multiplier per consecutive losing day
  cap          dollar table limit on the notional (default 160,000)
  reset        after a winning day: "full" (step 0), "half", or "hold"
  enter_after  trade only when the index down-streak >= N days
  max_lev      optional extra cap as a multiple of the balance
  withdraw_at / withdraw_amount
               bank withdraw_amount whenever the balance reaches
               withdraw_at; metrics are on total equity (balance + cash)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

START_BALANCE = 25000.0


def simulate(
    rows: List[Tuple[object, float]],
    cfg: Dict,
    *,
    start_balance: float = START_BALANCE,
    amplify: float = 1.0,
) -> Optional[Dict]:
    """Replay one continuous run. Returns None if the account busts.

    ``rows`` are (day, close) pairs; ``amplify`` scales every daily
    return (stress testing).
    """
    px = [c for _, c in rows]
    if amplify != 1.0:
        scaled = [100.0]
        for i in range(1, len(px)):
            r = px[i] / px[i - 1] - 1.0
            scaled.append(scaled[-1] * (1.0 + amplify * r))
        px = scaled
    cap = cfg.get("cap", 160000.0)
    take_at = cfg.get("withdraw_at")
    balance, step, down, withdrawn = start_balance, 0, 0, 0.0
    peak, max_dd = start_balance, 0.0
    n_withdrawals = 0
    for i in range(1, len(px)):
        ret = px[i] / px[i - 1] - 1.0
        if down >= cfg.get("enter_after", 0):
            base = cfg["base"] * (balance if cfg.get("pct") else 1.0)
            notional = min(base * cfg["factor"] ** step, cap)
            if cfg.get("max_lev"):
                notional = min(notional, cfg["max_lev"] * balance)
            balance += notional * ret
            if balance <= 0:
                return None
            if ret < 0:
                step += 1
            elif ret > 0:
                step = {"full": 0, "half": max(0, step - 1),
                        "hold": step}[cfg.get("reset", "full")]
        down = down + 1 if ret < 0 else 0
        if take_at and balance >= take_at:
            balance -= cfg["withdraw_amount"]
            withdrawn += cfg["withdraw_amount"]
            n_withdrawals += 1
        equity = balance + withdrawn
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    years = (rows[-1][0] - rows[0][0]).days / 365.25
    equity = balance + withdrawn
    return {
        "final": equity,
        "balance": balance,
        "withdrawn": withdrawn,
        "n_withdrawals": n_withdrawals,
        "cagr": (equity / start_balance) ** (1 / years) - 1,
        "max_dd": max_dd,
        "years": years,
    }
