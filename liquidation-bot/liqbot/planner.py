"""Pure decision logic: given a borrower's balances and prices, which
(collateral, debt, size) combinations are worth simulating? No I/O here."""
from __future__ import annotations

from dataclasses import dataclass

WAD = 10**18
BPS = 10_000


@dataclass(frozen=True)
class Reserve:
    address: str
    symbol: str
    decimals: int
    bonus_bps: int  # e.g. 10500 = liquidator receives 5% extra collateral


@dataclass(frozen=True)
class UserReserve:
    asset: str
    collateral: int  # aToken balance, 0 when not enabled as collateral
    debt: int  # stable + variable debt


@dataclass(frozen=True)
class Candidate:
    collateral: str
    debt: str
    debt_to_cover: int
    est_bonus_base: int  # rough gross profit in base-currency units, for ranking only


def to_base(amount: int, price: int, decimals: int) -> int:
    return amount * price // 10**decimals


def from_base(value: int, price: int, decimals: int) -> int:
    return value * 10**decimals // price if price else 0


def candidates(
    positions: list[UserReserve],
    reserves: dict[str, Reserve],
    prices: dict[str, int],
    health_factor: int,
    max_pairs: int = 2,
) -> list[Candidate]:
    """Rank liquidation options, best estimated bonus first.

    For each (collateral, debt) pair we offer the full debt and, when the position is
    only slightly underwater, half of it: Aave allows 50% above HF 0.95 and 100% below,
    but forks and newer versions differ, so the on-chain simulation has the final say.
    """
    if health_factor >= WAD:
        return []
    colls = [p for p in positions if p.collateral > 0 and prices.get(p.asset)]
    debts = [p for p in positions if p.debt > 0 and prices.get(p.asset)]
    pairs: list[tuple[int, UserReserve, UserReserve, int]] = []
    for c in colls:
        rc = reserves[c.asset]
        if rc.bonus_bps <= BPS:
            continue  # asset cannot be seized
        coll_value = to_base(c.collateral, prices[c.asset], rc.decimals)
        for d in debts:
            rd = reserves[d.asset]
            # The seized collateral (debt value * bonus) cannot exceed what the user has.
            cap = from_base(coll_value * BPS // rc.bonus_bps, prices[d.asset], rd.decimals)
            cover = min(d.debt, cap)
            if cover <= 0:
                continue
            bonus = to_base(cover, prices[d.asset], rd.decimals) * (rc.bonus_bps - BPS) // BPS
            pairs.append((bonus, c, d, cover))
    pairs.sort(key=lambda t: t[0], reverse=True)

    out: list[Candidate] = []
    for bonus, c, d, cover in pairs[:max_pairs]:
        out.append(Candidate(c.asset, d.asset, cover, bonus))
        if health_factor > 95 * WAD // 100 and cover // 2 > 0:
            out.append(Candidate(c.asset, d.asset, cover // 2, bonus // 2))
    return out
