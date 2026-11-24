"""Economic model of the liquidation-bot strategy, for parameter sweeps.

This is NOT a backtest on real data. It is a Monte Carlo model that answers
"under which conditions does this strategy make money, and how much?".
Every number it produces is conditional on the assumptions in `BASE`.

Structure (so sweeps are fast):
  1. price paths     - one year of 5-minute prices with fat tails and volatility clustering
  2. market events   - which loans become liquidatable, when, and how large (depends on the
                       market parameters only)
  3. economics       - what the bot earns from those events (depends on protocol, cost,
                       competition and bot parameters)
"""
from __future__ import annotations

import numpy as np

STEPS_PER_DAY = 288  # 5-minute steps
T = 365 * STEPS_PER_DAY

# Share of small loans (under 500 USD) that nobody rescues before liquidation, per borrower type.
# Larger loans are rescued more often (see `whale_care`). Calibrated against Aave's published
# lifetime figures: about 3.3% of borrow transactions end in liquidation, but only about 0.45%
# of borrowed volume, i.e. liquidations are many and small. "typical" reproduces those ratios.
RISK_UNRESCUED = {"cautious": 0.20, "typical": 0.50, "reckless": 0.90}

BASE = dict(
    # --- market ---
    market_debt=5_000_000,   # total borrowed in the target market, USD
    mean_debt=5_000,         # average loan size, USD (sizes are log-normal, heavy tailed)
    risk="typical",          # how close to the limit borrowers sit
    whale_care=0.6,          # how much more carefully large loans are managed (0 = same as small)
    turnover_days=30,        # average life of a loan
    vol=0.70,                # annualised volatility of the collateral asset
    # --- protocol ---
    liq_threshold=0.80,
    bonus=0.06,              # liquidation bonus
    protocol_fee=0.10,       # share of the bonus kept by the protocol
    flash_fee=0.0005,
    small_full_close=True,   # Aave v3.3 rule: loans under 2000 USD are closed 100%
    # --- swap ---
    dex_fee=0.003,
    dex_depth=300_000,       # USD on each side of the pool you sell collateral into
    oracle_lag=0.25,         # how much of the last 5-min price drop the DEX is ahead of the oracle
    # --- costs ---
    gas_usd=0.05,            # cost of one successful liquidation transaction
    fail_gas_share=0.5,      # a reverted transaction costs this share of the gas
    infra_month=10.0,        # RPC plan and server, USD per month
    # --- competition ---
    pro_threshold=50.0,      # USD profit at which professional bots reliably win instantly
    rival_delay=30.0,        # seconds amateur rivals typically take to react
    latency=6.0,             # seconds this bot takes from price update to landed transaction
    send_on_loss=0.5,        # when a rival wins, how often we had already sent (and pay failed gas)
    # --- bot settings ---
    min_profit=1.0,
    gas_safety=1.5,
)


def price_paths(n: int, seed: int = 1) -> np.ndarray:
    """n unit-volatility log-return paths (multiply by `vol` before use)."""
    rng = np.random.default_rng(seed)
    df = 4
    z = rng.standard_t(df, size=(n, T)) * np.sqrt((df - 2) / df)
    # daily volatility regime: AR(1) in log-vol, so calm months and violent weeks both occur
    h = np.zeros((n, 365))
    eps = rng.normal(0, 0.25, size=(n, 365))
    for d in range(1, 365):
        h[:, d] = 0.95 * h[:, d - 1] + eps[:, d]
    scale = np.exp(h)
    scale /= np.sqrt((scale**2).mean(axis=1, keepdims=True))
    return z * np.repeat(scale, STEPS_PER_DAY, axis=1) / np.sqrt(365 * STEPS_PER_DAY)


def _min_tables(price: np.ndarray) -> list[np.ndarray]:
    """tables[j][t] = min(price[t : t + 2**j])."""
    tables, k = [price], 1
    while k < len(price):
        prev = tables[-1]
        tables.append(np.minimum(prev, np.concatenate([prev[k:], np.full(k, np.inf)])))
        k *= 2
    return tables


def _first_at_or_below(tables, start: np.ndarray, level: np.ndarray) -> np.ndarray:
    """For each query, the first step >= start where price <= level (>= T if never)."""
    pos = start.copy()
    for j in range(len(tables) - 1, -1, -1):
        idx = np.minimum(pos, T - 1)
        skip = (pos < T) & (tables[j][idx] > level)
        pos = np.where(skip, pos + (1 << j), pos)
    return pos


def market_events(unit_returns: np.ndarray, p: dict, seed: int) -> dict:
    """Simulates one year of one market. Returns the liquidation opportunities:
    arrays `cover` (USD of debt repayable), `step`, and `drop` (the 5-min price fall at that moment)."""
    rng = np.random.default_rng(seed)
    price = np.exp(np.cumsum(unit_returns * p["vol"]))
    tables = _min_tables(price)

    slots = max(int(p["market_debt"] / p["mean_debt"]), 1)
    turn = p["turnover_days"] * STEPS_PER_DAY
    k = int(T / turn * 2 + 12)
    start = np.cumsum(rng.exponential(turn, size=(slots, k)), axis=1) - rng.uniform(0, turn, size=(slots, 1))
    end = np.concatenate([start[:, 1:], np.full((slots, 1), np.inf)], axis=1)
    keep = (end > 0) & (start < T)
    start = np.clip(start[keep], 0, T - 1).astype(np.int64)
    end = np.minimum(end[keep], T).astype(np.int64)
    n = len(start)

    sigma = 2.0
    debt = p["mean_debt"] * np.exp(sigma * rng.standard_normal(n) - sigma**2 / 2)
    debt = np.minimum(debt, 0.2 * p["market_debt"])
    buffer = 0.5 * np.exp(0.7 * rng.standard_normal(n))  # health factor minus 1 when opened
    liq_price = price[start] / (1 + buffer)  # price at which health factor hits 1
    collateral = debt / (liq_price * p["liq_threshold"])  # in units of the asset
    # The rest repay or top up in time; the bigger the loan, the more likely.
    unrescued = RISK_UNRESCUED[p["risk"]] * np.minimum(1.0, (500 / debt) ** p["whale_care"])
    alive = rng.random(n) < unrescued
    start, end, debt, collateral, liq_price = (a[alive] for a in (start, end, debt, collateral, liq_price))

    covers, steps, drops = [], [], []
    for _ in range(4):  # a loan can be partially liquidated several times
        if len(start) == 0:
            break
        hit = _first_at_or_below(tables, start, liq_price)
        ok = hit < end
        hit, debt, collateral, liq_price, end = (a[ok] for a in (hit, debt, collateral, liq_price, end))
        health = price[hit] / liq_price
        full = health < 0.95
        if p["small_full_close"]:
            full |= debt < 2000
        cover = np.where(full, debt, debt / 2)
        # seized collateral cannot exceed what is there
        cover = np.minimum(cover, collateral * price[hit] / (1 + p["bonus"]))
        covers.append(cover)
        steps.append(hit)
        drops.append(np.maximum(0.0, 1 - price[hit] / price[np.maximum(hit - 1, 0)]))
        debt = debt - cover
        collateral = collateral - cover * (1 + p["bonus"]) / price[hit]
        again = (debt > 1) & (collateral > 0)
        start, end, debt, collateral = hit[again] + 1, end[again], debt[again], collateral[again]
        liq_price = debt / (collateral * p["liq_threshold"])
    if not covers:
        z = np.zeros(0)
        return dict(cover=z, step=z.astype(int), drop=z, loans=n)
    return dict(cover=np.concatenate(covers), step=np.concatenate(steps), drop=np.concatenate(drops), loans=n)


def liquidation_profit(cover, p: dict, drop=0.0):
    """Profit in USD of one liquidation before gas. Mirrors FlashLiquidator.sol:
    repay `cover`, receive cover*(1+bonus) of collateral, sell it, repay the flash loan."""
    cover = np.asarray(cover, dtype=float)
    seized = cover * (1 + p["bonus"] * (1 - p["protocol_fee"]))
    slippage = p["dex_fee"] + seized / (p["dex_depth"] + seized) + p["oracle_lag"] * drop
    return seized * (1 - slippage) - cover * (1 + p["flash_fee"])


def year_result(events: dict, p: dict, seed: int) -> dict:
    """What the bot earns from one simulated year of events."""
    rng = np.random.default_rng(seed + 7)
    cover = events["cover"]
    profit = liquidation_profit(cover, p, events["drop"])
    gas = p["gas_usd"]
    attempt = profit - gas * p["gas_safety"] >= p["min_profit"]
    p_win = np.exp(-np.maximum(profit, 0) / p["pro_threshold"]) * np.exp(-p["latency"] / p["rival_delay"])
    win = attempt & (rng.random(len(cover)) < p_win)
    lost_sent = attempt & ~win & (rng.random(len(cover)) < p["send_on_loss"])
    gross = profit[win].sum()
    gas_paid = gas * win.sum() + gas * p["fail_gas_share"] * lost_sent.sum()
    infra = 12 * p["infra_month"]
    # largest run of gas spent on failures between two wins: how deep the gas wallet must be
    flow = np.where(win, profit - gas, np.where(lost_sent, -gas * p["fail_gas_share"], 0.0))[np.argsort(events["step"])]
    cum = np.cumsum(flow) if len(flow) else np.zeros(1)
    drawdown = float((np.maximum.accumulate(np.maximum(cum, 0)) - cum).max()) if len(flow) else 0.0
    return dict(
        net=gross - gas_paid - infra,
        gross=gross, gas=gas_paid, infra=infra,
        events=len(cover), attempts=int(attempt.sum()), wins=int(win.sum()), failed=int(lost_sent.sum()),
        volume=float(cover.sum()), drawdown=drawdown,
        biggest_win=float(profit[win].max()) if win.any() else 0.0,
    )
