"""Educational options-strategy suggestions for screened candidates.

The screener's thesis is: a fundamentally strong company sold off on an earnings
report, and mean reversion / continued quality may drive a recovery. That is a
*bullish-to-neutral* setup. The strategies below express that view with an
emphasis on defined risk. Post-earnings implied volatility is usually elevated
and then collapses ("IV crush"), which favors *selling* premium -- reflected in
the ordering here.

IMPORTANT: These are study templates, not trade recommendations. Strikes are
described relative to the current price; the user must price real contracts,
check liquidity/IV, and size positions themselves.
"""

from __future__ import annotations

from typing import List

from ..data.models import Candidate, OptionSuggestion


def _pct_price(price: float, pct: float) -> str:
    """Format a strike a given percentage away from ``price``."""
    return f"${price * (1 + pct):.2f}"


def suggest_option_strategies(candidate: Candidate) -> List[OptionSuggestion]:
    price = candidate.crash.current_close
    high_quality = candidate.fundamental_score >= 75
    suggestions: List[OptionSuggestion] = []

    # 1) Cash-secured put -- get paid to buy the quality name lower.
    suggestions.append(
        OptionSuggestion(
            strategy="Cash-secured put",
            outlook="neutral-to-bullish",
            description=(
                "Sell a put to collect premium (boosted by post-earnings IV). "
                "If assigned, you buy a company you already like at a discount; "
                "if not, you keep the premium."
            ),
            strike_guidance=(
                f"Sell a put ~5-10% below spot, near {_pct_price(price, -0.07)}. "
                "Pick an expiry 30-45 days out to capture rich theta/IV."
            ),
            risk_note=(
                "Obligated to buy 100 shares/contract at the strike; set aside "
                "the cash. Max loss if the stock keeps falling toward zero."
            ),
        )
    )

    # 2) Bull put credit spread -- defined-risk version of the above.
    suggestions.append(
        OptionSuggestion(
            strategy="Bull put credit spread",
            outlook="bullish",
            description=(
                "Sell a put and buy a further-OTM put to cap risk. Profits if "
                "the stock stays above the short strike; benefits from IV crush."
            ),
            strike_guidance=(
                f"Short put ~5% below spot ({_pct_price(price, -0.05)}), long "
                f"put ~10% below ({_pct_price(price, -0.10)}). 30-45 DTE."
            ),
            risk_note=(
                "Max loss = spread width minus credit received -- fully defined "
                "and much smaller than a naked put."
            ),
        )
    )

    # 3) LEAPS call -- leveraged, longer-horizon recovery bet.
    suggestions.append(
        OptionSuggestion(
            strategy="Long LEAPS call",
            outlook="bullish",
            description=(
                "Buy a long-dated in-the-money call to participate in a multi-"
                "quarter recovery with less capital than owning shares."
            ),
            strike_guidance=(
                f"Buy a call slightly ITM (~{_pct_price(price, -0.05)}) with 6-12 "
                "months to expiry so time decay is slow and delta is high."
            ),
            risk_note=(
                "Long premium: max loss is the debit paid. Buying after IV has "
                "cooled off avoids overpaying for volatility."
            ),
        )
    )

    # 4) For the very highest-quality setups, a bullish call debit spread as a
    #    cheaper directional play.
    if high_quality:
        suggestions.append(
            OptionSuggestion(
                strategy="Bull call debit spread",
                outlook="bullish",
                description=(
                    "Buy a call and sell a higher call to lower cost -- a cheap, "
                    "defined-risk way to target a specific recovery level."
                ),
                strike_guidance=(
                    f"Long call near spot ({_pct_price(price, 0.0)}), short call "
                    f"~10% higher ({_pct_price(price, 0.10)}). 45-90 DTE."
                ),
                risk_note=(
                    "Max loss = net debit paid; max gain capped at the spread "
                    "width. Good risk/reward when you expect a measured bounce."
                ),
            )
        )

    return suggestions
