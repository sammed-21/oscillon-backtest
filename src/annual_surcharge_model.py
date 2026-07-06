"""
State-weighted annual LP surcharge model for Oscillon.

annual_lp_surcharge = sum_s P(state) * lp_surcharge_per_day(state) * 365

P(state)           — fraction of calendar days with max Chainlink |USDC−$1| in bucket
lp_surcharge/day   — empirical from prepared swap replays (85% LP share of surcharge)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd

from .oscillon_fee import BASE_FEE_BPS, drain_surcharge_bps, FeeContext, fee_bps, select_fee_pips
from .swap_direction import validate_prepared_swaps

LP_SURPLUS_SHARE = 0.85
ScenarioName = Literal["BEAR", "BASE", "BULL"]

DEV_BUCKETS: list[tuple[str, float, float]] = [
    ("dead", 0, 3),
    ("micro", 3, 7),
    ("small", 7, 15),
    ("medium", 15, 30),
    ("large", 30, 10_000),
]

TVL_GRID = [
    15_000_000,
    50_000_000,
    100_000_000,
    250_000_000,
    500_000_000,
    1_000_000_000,
]

ALWAYS_ON_STATES = frozenset({"micro", "small"})
EPISODIC_STATES = frozenset({"medium", "large"})


def dev_bucket(dev_bps: float) -> str:
    for name, lo, hi in DEV_BUCKETS:
        if lo <= dev_bps < hi:
            return name
    return "large"


def midpoint_dev_bps(name: str) -> float:
    mapping = {
        "dead": 1.5,
        "micro": 5.0,
        "small": 11.0,
        "medium": 22.5,
        "large": 50.0,
    }
    return mapping[name]


def avg_surcharge_bps_at_midpoint(name: str) -> float:
    if name == "dead":
        return 0.0
    return drain_surcharge_bps(midpoint_dev_bps(name))


def load_chainlink_daily_states(path: Path) -> pd.Series:
    """Max daily deviation (bps) → bucket name, indexed by date."""
    df = pd.read_csv(path)
    ts_col = "minute" if "minute" in df.columns else df.columns[0]
    price_col = next(
        (c for c in ("usdc_price", "price", "oracle_price") if c in df.columns),
        df.columns[1],
    )
    df["ts"] = pd.to_datetime(df[ts_col], utc=True)
    df["dev_bps"] = (df[price_col].astype(float) - 1.0).abs() * 10_000
    daily_max = df.groupby(df["ts"].dt.date)["dev_bps"].max()
    return daily_max.apply(dev_bucket)


def state_probabilities(daily_states: pd.Series) -> dict[str, float]:
    if daily_states.empty:
        return {name: 0.0 for name, _, _ in DEV_BUCKETS}
    counts = daily_states.value_counts(normalize=True)
    return {name: float(counts.get(name, 0.0)) for name, _, _ in DEV_BUCKETS}


def filter_daily_states(
    daily_states: pd.Series,
    *,
    start: date | None = None,
    end: date | None = None,
) -> pd.Series:
    """Restrict to an inclusive calendar-day window."""
    if daily_states.empty:
        return daily_states
    out = daily_states
    if start is not None:
        out = out[out.index >= start]
    if end is not None:
        out = out[out.index <= end]
    return out


def merge_daily_state_series(series_list: list[pd.Series]) -> pd.Series:
    """Union calendar days; later files override earlier on duplicate dates."""
    if not series_list:
        return pd.Series(dtype=object)
    out = series_list[0].copy()
    for s in series_list[1:]:
        out = pd.concat([out, s])
        out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


@dataclass
class PeriodSwapStats:
    label: str
    path: str
    start: str
    end: str
    days: float
    drain_volume_usd: float
    static_lp_income_usd: float
    hybrid_lp_income_usd: float
    lp_surcharge_usd: float
    avg_dev_bps: float
    max_dev_bps: float
    drain_swaps_above_3bps: int
    minute_pct_by_bucket: dict[str, float] = field(default_factory=dict)
    lp_surcharge_per_day_by_bucket: dict[str, float] = field(default_factory=dict)
    drain_volume_by_bucket: dict[str, float] = field(default_factory=dict)


def analyze_prepared_period(path: Path, label: str) -> PeriodSwapStats:
    swaps, _ = validate_prepared_swaps(pd.read_csv(path))
    ts = pd.to_datetime(swaps["timestamp"])
    days = max((ts.max() - ts.min()).total_seconds() / 86400.0, 1.0)

    swaps = swaps.copy()
    swaps["bucket"] = swaps["dev_bps"].apply(dev_bucket)

    minute_pct = (swaps["bucket"].value_counts(normalize=True) * 100).to_dict()

    static_lp = 0.0
    hybrid_lp = 0.0
    for _, row in swaps.iterrows():
        ds = float(row.get("drain_size_usd", 0))
        rs = float(row.get("restore_size_usd", 0))
        total = ds + rs
        static_lp += total * BASE_FEE_BPS / 10_000
        if row["is_drain"]:
            fb = fee_bps(
                select_fee_pips(
                    FeeContext(int(row["dev_bps"]), True, fee_model="hybrid", k_override=45)
                )
            )
            hybrid_lp += ds * fb / 10_000 + rs * BASE_FEE_BPS / 10_000
        else:
            hybrid_lp += total * BASE_FEE_BPS / 10_000

    drain = swaps[swaps["is_drain"]].copy()
    drain["surcharge_usd"] = drain.apply(
        lambda r: r["drain_size_usd"]
        * (drain_surcharge_bps(r["dev_bps"]) if r["dev_bps"] >= 3 else 0)
        / 10_000,
        axis=1,
    )

    lp_surcharge_by_bucket: dict[str, float] = {}
    drain_vol_by_bucket: dict[str, float] = {}
    lp_per_day_by_bucket: dict[str, float] = {}

    for name, _, _ in DEV_BUCKETS:
        sub = drain[drain["bucket"] == name]
        gross = float(sub["surcharge_usd"].sum())
        lp = gross * LP_SURPLUS_SHARE
        lp_surcharge_by_bucket[name] = lp
        drain_vol_by_bucket[name] = float(sub["drain_size_usd"].sum())
        lp_per_day_by_bucket[name] = lp / days

    return PeriodSwapStats(
        label=label,
        path=str(path),
        start=str(ts.min().date()),
        end=str(ts.max().date()),
        days=days,
        drain_volume_usd=float(drain["drain_size_usd"].sum()),
        static_lp_income_usd=static_lp,
        hybrid_lp_income_usd=hybrid_lp,
        lp_surcharge_usd=float(drain["surcharge_usd"].sum()) * LP_SURPLUS_SHARE,
        avg_dev_bps=float(swaps["dev_bps"].mean()),
        max_dev_bps=float(swaps["dev_bps"].max()),
        drain_swaps_above_3bps=int((drain["dev_bps"] >= 3).sum()),
        minute_pct_by_bucket={k: float(minute_pct.get(k, 0.0)) for k, _, _ in DEV_BUCKETS},
        lp_surcharge_per_day_by_bucket=lp_per_day_by_bucket,
        drain_volume_by_bucket=drain_vol_by_bucket,
    )


def calibration_rates(
    calm: PeriodSwapStats,
    stress: PeriodSwapStats,
    recovery: PeriodSwapStats | None = None,
) -> dict[str, float]:
    """
    LP surcharge $/day when a day is in state S.

    - micro: calm replay (May 2026); recovery blended in if present
    - small / medium / large: day-weighted blend of stress + recovery replays
      (recovery fills the 4–15 bps transition gap absent from peak-stress only)
    """
    rates: dict[str, float] = {"dead": 0.0}

    def weighted_per_day(state: str, periods: list[PeriodSwapStats]) -> float:
        if not periods:
            return 0.0
        numer = sum(p.lp_surcharge_per_day_by_bucket.get(state, 0.0) * p.days for p in periods)
        denom = sum(p.days for p in periods)
        return numer / denom if denom > 0 else 0.0

    micro_periods = [calm]
    if recovery is not None:
        micro_periods.append(recovery)
    rates["micro"] = weighted_per_day("micro", micro_periods)

    stress_periods = [stress]
    if recovery is not None:
        stress_periods.append(recovery)
    for state in ("small", "medium", "large"):
        rates[state] = weighted_per_day(state, stress_periods)

    return rates


@dataclass
class ScenarioResult:
    name: ScenarioName
    state_probs: dict[str, float]
    daily_lp_surcharge: dict[str, float]
    annual_lp_surcharge_usd: float
    annual_always_on_usd: float
    annual_episodic_usd: float
    chainlink_days: int
    chainlink_sources: list[str]

    def apr_bps(self, tvl: float) -> float:
        return (self.annual_lp_surcharge_usd / tvl) * 10_000 if tvl else 0.0

    def always_on_apr_bps(self, tvl: float) -> float:
        return (self.annual_always_on_usd / tvl) * 10_000 if tvl else 0.0

    def episodic_apr_bps(self, tvl: float) -> float:
        return (self.annual_episodic_usd / tvl) * 10_000 if tvl else 0.0


def build_scenario(
    name: ScenarioName,
    daily_states: pd.Series,
    rates: dict[str, float],
    sources: list[str],
) -> ScenarioResult:
    probs = state_probabilities(daily_states)
    daily_by_state = {s: probs[s] * rates[s] for s, _, _ in DEV_BUCKETS}
    annual = 365.0 * sum(daily_by_state.values())
    always_on = 365.0 * sum(daily_by_state[s] for s in ALWAYS_ON_STATES)
    episodic = 365.0 * sum(daily_by_state[s] for s in EPISODIC_STATES)
    return ScenarioResult(
        name=name,
        state_probs=probs,
        daily_lp_surcharge=daily_by_state,
        annual_lp_surcharge_usd=annual,
        annual_always_on_usd=always_on,
        annual_episodic_usd=episodic,
        chainlink_days=len(daily_states),
        chainlink_sources=sources,
    )


def break_even_tvl(annual_lp_surcharge_usd: float, target_bps: float = 10.0) -> float | None:
    if annual_lp_surcharge_usd <= 0:
        return None
    return annual_lp_surcharge_usd * 10_000 / target_bps


def run_model(
    *,
    chainlink_files: dict[str, Path],
    calm_prepared: Path,
    stress_prepared: Path,
    recovery_prepared: Path | None = None,
    calm_label: str = "May 2026 calm",
    stress_label: str = "March 2023 stress",
    recovery_label: str = "March 2023 recovery",
    base_chainlink_keys: list[str] | None = None,
) -> tuple[PeriodSwapStats, PeriodSwapStats, PeriodSwapStats | None, dict[str, ScenarioResult]]:
    calm = analyze_prepared_period(calm_prepared, calm_label)
    stress = analyze_prepared_period(stress_prepared, stress_label)
    recovery = (
        analyze_prepared_period(recovery_prepared, recovery_label)
        if recovery_prepared is not None and recovery_prepared.exists()
        else None
    )
    rates = calibration_rates(calm, stress, recovery)

    loaded: dict[str, pd.Series] = {}
    for key, path in chainlink_files.items():
        if path.exists():
            loaded[key] = load_chainlink_daily_states(path)

    bear_states = loaded.get("2024", pd.Series(dtype=object))
    # BULL: March 2023 SVB window only (full-year 2023 file is used for BASE)
    bull_states = filter_daily_states(
        loaded.get("2023", pd.Series(dtype=object)),
        start=date(2023, 3, 1),
        end=date(2023, 3, 31),
    )
    if base_chainlink_keys is not None:
        base_series = [loaded[k] for k in base_chainlink_keys if k in loaded]
        base_states = merge_daily_state_series(base_series)
        base_sources = [
            str(chainlink_files[k]) for k in base_chainlink_keys if k in chainlink_files
        ]
    else:
        base_states = merge_daily_state_series(list(loaded.values()))
        base_sources = [str(p) for p in chainlink_files.values() if p.exists()]

    scenarios = {
        "BEAR": build_scenario(
            "BEAR",
            bear_states,
            rates,
            [str(chainlink_files.get("2024", ""))],
        ),
        "BASE": build_scenario(
            "BASE",
            base_states,
            rates,
            base_sources,
        ),
        "BULL": build_scenario(
            "BULL",
            bull_states,
            rates,
            [str(chainlink_files.get("2023", ""))],
        ),
    }
    return calm, stress, recovery, scenarios
