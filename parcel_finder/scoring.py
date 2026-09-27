"""Motivated-seller signals and the parcel score (pure; no I/O).

Four seller types, each read from the tax roll:

1. Behind on taxes   - unpaid receivables for past tax years
2. Out of state      - mailing state is not the parcel's state
3. Estate / heirs    - owner name says ESTATE, HEIRS, ET AL, DECD, LIFE ESTATE
4. Long idle         - no improvements / vacant-land code, held 10+ years,
                       no homestead exemption (owner doesn't live there)

Collection status sharpens it: a tax suit or judgment means the county is
already pushing toward a sale (+), while bankruptcy (automatic stay) and an
over-65/disabled deferral (can't be foreclosed) make a deal unlikely (-).
Flood zone and road access come from optional GIS enrichment and only count
when known.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional

# Word-bounded so "ESTATES" (a subdivision) and "REAL ESTATE" (a company)
# don't trip it; "EST OF" / "ESTATE OF" do.
ESTATE_PATTERN = re.compile(
    r"\bESTATE\b|\bEST OF\b|\bHEIRS?\b|\bET\s?AL\b|\bDEC'?D\b|\bDECEASED\b|\bLIFE\s+EST(ATE)?\b",
    re.IGNORECASE,
)
_REAL_ESTATE = re.compile(r"\bREAL\s+ESTATE\b", re.IGNORECASE)

VACANT_CODES = ("C1", "D1", "D2", "E")   # Texas PTAD prefixes; a layout can supply its own


@dataclass(frozen=True)
class Targets:
    """The parcels you want to buy: acreage band and price ceiling."""
    min_acres: float = 0.5
    max_acres: float = 5.0
    max_value: float = 500_000
    min_value: float = 5_000     # below this the "price" is an HOA common area or ROW sliver


@dataclass(frozen=True)
class Weights:
    delinquent: int = 3
    per_year_behind: int = 1
    # Only the first N years behind earn points (0 = no cap). Uncapped, a
    # 20-year-delinquent $1 sliver outranks a 2-acre lot that fits the targets.
    max_years_scored: int = 5
    target_acreage: int = 3    # acreage inside Targets band
    under_price: int = 4       # most points for the cheapest parcel under the cap (graded)
    out_of_state: int = 2
    estate: int = 2
    long_held: int = 1
    vacant: int = 1
    absentee: int = 1          # no homestead exemption
    in_suit: int = 1           # delinquent-tax lawsuit filed
    judgment: int = 1          # judgment entered: sale is next
    bankruptcy: int = -3       # automatic stay
    deferral: int = -3         # over-65 / disabled deferral: can't be foreclosed
    flood_zone: int = -5       # parcel center in a FEMA Special Flood Hazard Area
    flood_partial: int = -2    # SFHA covers part of the parcel, not its center
    no_road_access: int = -5

    def as_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


@dataclass
class Signals:
    delinquent: bool = False
    years_behind: int = 0
    target_acreage: bool = False
    under_price: bool = False
    price_fraction: float = 0.0  # value / cap, for parcels under it
    out_of_state: bool = False
    estate: bool = False
    long_held: bool = False
    vacant: bool = False
    absentee: bool = False
    in_suit: bool = False
    judgment: bool = False
    bankruptcy: bool = False
    deferral: bool = False
    flood_zone: Optional[bool] = None
    flood_partial: bool = False
    road_access: Optional[bool] = None
    reasons: list = field(default_factory=list)


def is_estate(*names: Optional[str]) -> bool:
    for name in names:
        if name and ESTATE_PATTERN.search(_REAL_ESTATE.sub("", name)):
            return True
    return False


def is_out_of_state(mail_state: Optional[str], home_state: str = "TX") -> bool:
    """Blank state reads as unknown, not out of state."""
    state = (mail_state or "").strip().upper()
    return bool(state) and state != home_state.upper()


def is_vacant(impr_value: Optional[float], state_code: Optional[str],
              vacant_codes: Iterable[str] = VACANT_CODES, *,
              land_value: Optional[float] = None) -> bool:
    """No improvements on a parcel that has value, or a vacant-land property code.

    A $0 improvement value alone isn't enough: rolls often leave the land /
    improvement split blank (both 0) and carry only the total, so it counts
    only when land value is present.
    """
    code = (state_code or "").strip().upper()
    if code and any(code.startswith(c) for c in vacant_codes) and not impr_value:
        return True
    return impr_value is not None and impr_value <= 0 and (land_value or 0) > 0


def years_held(deed_date: Optional[str], as_of: dt.date) -> Optional[float]:
    if not deed_date:
        return None
    try:
        d = dt.date.fromisoformat(deed_date)
    except ValueError:
        return None
    return (as_of - d).days / 365.25


def compute_signals(parcel: Dict, *, as_of: dt.date, home_state: str = "TX",
                    homestead: re.Pattern = re.compile(r"\bHS\b"),
                    long_held_years: int = 10,
                    vacant_codes: Iterable[str] = VACANT_CODES,
                    targets: Targets = Targets()) -> Signals:
    s = Signals()
    s.years_behind = int(parcel.get("years_delinquent") or 0)
    s.delinquent = s.years_behind > 0
    acres, value = parcel.get("acreage"), parcel.get("market_value")
    s.target_acreage = acres is not None and targets.min_acres <= acres <= targets.max_acres
    # 0 means the roll carries no value, not a free parcel.
    s.under_price = value is not None and targets.min_value <= value < targets.max_value
    s.price_fraction = value / targets.max_value if s.under_price else 0.0
    s.out_of_state = is_out_of_state(parcel.get("mail_state"), home_state)
    s.estate = is_estate(parcel.get("owner_name"), parcel.get("owner_name2"))
    held = years_held(parcel.get("deed_date"), as_of)
    s.long_held = held is not None and held >= long_held_years
    s.vacant = is_vacant(parcel.get("impr_value"), parcel.get("state_code"), vacant_codes,
                         land_value=parcel.get("land_value"))
    s.absentee = not homestead.search(parcel.get("exemptions") or "")
    s.in_suit = bool(parcel.get("in_suit"))
    s.judgment = bool(parcel.get("in_judgment"))
    s.bankruptcy = bool(parcel.get("in_bankruptcy"))
    s.deferral = bool(parcel.get("in_deferral"))
    fz, road = parcel.get("flood_zone"), parcel.get("road_access")
    s.flood_zone = None if fz is None else bool(fz)
    s.flood_partial = bool(parcel.get("flood_partial"))
    s.road_access = None if road is None else bool(road)
    return s


def score(s: Signals, w: Weights = Weights()) -> int:
    total, reasons = 0, []

    def add(points: int, why: str):
        nonlocal total
        total += points
        reasons.append(f"{points:+d} {why}")

    if s.delinquent:
        add(w.delinquent, "tax-delinquent")
        years = min(s.years_behind, w.max_years_scored) if w.max_years_scored else s.years_behind
        add(w.per_year_behind * years, f"{s.years_behind} yr(s) behind")
    if s.target_acreage:
        add(w.target_acreage, "target acreage")
    if s.under_price:
        # Lower price, more points: with weight 4 and a $500k cap that is
        # +4 under $125k, +3 under $250k, +2 under $375k, +1 under $500k.
        pts = max(1, math.ceil(w.under_price * (1 - s.price_fraction)))
        add(pts, "low price" if pts >= w.under_price else "under price cap")
    if s.out_of_state:
        add(w.out_of_state, "out-of-state owner")
    if s.estate:
        add(w.estate, "estate/heirs")
    if s.long_held:
        add(w.long_held, "owned 10+ yrs")
    if s.vacant:
        add(w.vacant, "vacant/no improvements")
    if s.absentee:
        add(w.absentee, "no homestead")
    if s.in_suit:
        add(w.in_suit, "tax suit filed")
    if s.judgment:
        add(w.judgment, "tax judgment")
    if s.bankruptcy:
        add(w.bankruptcy, "in bankruptcy")
    if s.deferral:
        add(w.deferral, "tax deferral")
    if s.flood_zone:
        add(w.flood_zone, "flood zone")
    elif s.flood_partial:
        add(w.flood_partial, "partly in flood zone")
    if s.road_access is False:
        add(w.no_road_access, "no road access")
    s.reasons = reasons
    return total


def delinquent_through_year(as_of: dt.date) -> int:
    """Latest tax year whose bill is past due on ``as_of``.

    Texas bills for year Y are due Jan 31 of Y+1 and delinquent Feb 1.
    """
    return as_of.year - 1 if as_of >= dt.date(as_of.year, 2, 1) else as_of.year - 2
