"""
Volume retention when Oscillon fee exceeds a competitor (e.g. 1 bps Uniswap / Curve).

retained = volume × (f_comp / f_os)^η   capped at 1.0

η (eta) controls routing sensitivity; default 2.0 is a reasonable starting point
for stable routing bots — calibrate against aggregator data when available.
"""

from __future__ import annotations


def retained_volume_fraction(
    fee_os_bps: float,
    fee_comp_bps: float,
    eta: float = 2.0,
) -> float:
    if fee_os_bps <= 0:
        return 1.0
    if fee_comp_bps <= 0:
        return 0.0 if fee_os_bps > 0 else 1.0
    if fee_os_bps <= fee_comp_bps:
        return 1.0
    ratio = fee_comp_bps / fee_os_bps
    return min(1.0, ratio**eta)
