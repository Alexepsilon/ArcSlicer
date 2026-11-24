"""Runs every sweep and writes research/results.json and research/TABLES.md.
Usage: python research/run_sweep.py   (about 5-10 minutes on 4 cores)"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import model as m  # noqa: E402

HERE = Path(__file__).parent
N_PATHS = 40
PATHS = m.price_paths(N_PATHS, seed=1)
MARKET_KEYS = ("market_debt", "mean_debt", "risk", "whale_care", "turnover_days", "vol",
               "liq_threshold", "bonus", "small_full_close")
_cache: dict = {}


def run(p: dict, n_paths: int = N_PATHS) -> dict:
    key = tuple(p[k] for k in MARKET_KEYS) + (n_paths,)
    if key not in _cache:
        _cache.clear() if len(_cache) > 40 else None
        _cache[key] = [m.market_events(PATHS[i], p, i) for i in range(n_paths)]
    years = [m.year_result(e, p, i) for i, e in enumerate(_cache[key])]
    net = np.array([y["net"] for y in years])
    mean = lambda k: float(np.mean([y[k] for y in years]))
    return dict(
        median=float(np.median(net)), p10=float(np.percentile(net, 10)), p90=float(np.percentile(net, 90)),
        mean=float(net.mean()), worst=float(net.min()), p_loss=float((net < 0).mean()),
        events=mean("events"), attempts=mean("attempts"), wins=mean("wins"), failed=mean("failed"),
        volume_pct=100 * mean("volume") / p["market_debt"], gas=mean("gas"), gross=mean("gross"),
        drawdown=float(max(y["drawdown"] for y in years)), biggest_win=float(np.median([y["biggest_win"] for y in years])),
    )


ONE_AT_A_TIME = {
    "market_debt": [300_000, 1_000_000, 5_000_000, 20_000_000, 50_000_000],
    "mean_debt": [1_000, 2_500, 5_000, 10_000, 20_000],
    "risk": ["cautious", "typical", "reckless"],
    "whale_care": [0.3, 0.6, 0.9],
    "turnover_days": [15, 30, 60, 90],
    "vol": [0.4, 0.55, 0.7, 0.9, 1.1],
    "liq_threshold": [0.7, 0.8, 0.85],
    "bonus": [0.03, 0.05, 0.06, 0.075, 0.10],
    "protocol_fee": [0.0, 0.1, 0.2, 0.3],
    "flash_fee": [0.0, 0.0005, 0.0009, 0.003],
    "small_full_close": [True, False],
    "dex_fee": [0.0005, 0.003, 0.01],
    "dex_depth": [20_000, 100_000, 300_000, 1_000_000, 5_000_000],
    "oracle_lag": [0.0, 0.25, 0.5, 1.0],
    "gas_usd": [0.005, 0.05, 0.5, 2.0, 10.0, 30.0],
    "fail_gas_share": [0.3, 0.5, 0.8],
    "infra_month": [0, 10, 30, 100],
    "pro_threshold": [2, 5, 20, 50, 150, 500],
    "rival_delay": [1, 3, 10, 30, 120, 600],
    "latency": [1, 3, 6, 15, 60],
    "send_on_loss": [0.1, 0.5, 0.9],
    "min_profit": [0.1, 0.5, 1.0, 3.0, 10.0],
    "gas_safety": [1.0, 1.5, 3.0],
}


def sample_params(rng) -> dict:
    lu = lambda a, b: float(np.exp(rng.uniform(np.log(a), np.log(b))))
    return {**m.BASE,
            "market_debt": round(lu(3e5, 5e7), -3), "mean_debt": round(lu(1e3, 2e4), -1),
            "risk": str(rng.choice(["cautious", "typical", "reckless"], p=[0.35, 0.45, 0.20])),
            "whale_care": float(rng.uniform(0.3, 0.9)), "turnover_days": float(rng.uniform(15, 90)),
            "vol": float(rng.uniform(0.4, 1.1)), "bonus": float(rng.uniform(0.04, 0.10)),
            "protocol_fee": float(rng.choice([0.0, 0.1, 0.2])), "flash_fee": float(rng.uniform(0, 0.001)),
            "small_full_close": bool(rng.random() < 0.5), "dex_fee": float(rng.choice([0.0005, 0.003, 0.01])),
            "dex_depth": lu(3e4, 3e6), "oracle_lag": float(rng.uniform(0, 0.6)), "gas_usd": lu(0.005, 5),
            "fail_gas_share": float(rng.uniform(0.3, 0.8)), "infra_month": float(rng.uniform(0, 50)),
            "pro_threshold": lu(3, 300), "rival_delay": lu(2, 300), "latency": lu(2, 30),
            "send_on_loss": float(rng.uniform(0.2, 0.9))}


def _mc_one(seed: int) -> dict:
    p = sample_params(np.random.default_rng(10_000 + seed))
    years = [m.year_result(m.market_events(PATHS[i], p, i), p, i) for i in range(8)]
    out = {k: v for k, v in p.items() if k not in ("min_profit", "gas_safety", "liq_threshold")}
    out["net"] = float(np.median([y["net"] for y in years]))
    out["wins"] = float(np.mean([y["wins"] for y in years]))
    return out


def spearman(x, y) -> float:
    rank = lambda a: np.argsort(np.argsort(a)).astype(float)
    return float(np.corrcoef(rank(np.asarray(x, dtype=float)), rank(np.asarray(y)))[0, 1])


def main():
    res = {"base_params": m.BASE, "base": run(m.BASE)}
    net = np.array([m.year_result(e, m.BASE, i)["net"] for i, e in enumerate(next(iter(_cache.values())))])
    res["base_years"] = sorted(round(float(v)) for v in net)
    covers = np.concatenate([e["cover"] for e in next(iter(_cache.values()))])
    profit = m.liquidation_profit(covers, m.BASE)
    edges = [-1e9, 0, 1, 5, 20, 50, 200, 1000, 1e12]
    res["profit_hist"] = [dict(lo=edges[i], hi=edges[i + 1], share=float(((profit >= edges[i]) & (profit < edges[i + 1])).mean()),
                               value_share=float(np.maximum(profit, 0)[(profit >= edges[i]) & (profit < edges[i + 1])].sum() / np.maximum(profit, 0).sum()))
                          for i in range(len(edges) - 1)]

    print("one-at-a-time", flush=True)
    res["one_at_a_time"] = {k: [dict(value=v, **run({**m.BASE, k: v})) for v in vals] for k, vals in ONE_AT_A_TIME.items()}

    print("grids", flush=True)
    grid = lambda a, av, b, bv, extra={}: [[run({**m.BASE, **extra, a: x, b: y})["median"] for y in bv] for x in av]
    sizes, pros, delays = [1e6, 5e6, 20e6, 50e6], [5, 20, 50, 150, 500], [3, 10, 30, 120, 600]
    res["grid_competition"] = dict(rows=delays, cols=pros, cells=grid("rival_delay", delays, "pro_threshold", pros))
    res["grid_size_pro"] = dict(rows=sizes, cols=pros, cells=grid("market_debt", sizes, "pro_threshold", pros))
    gases = [0.005, 0.05, 0.5, 2.0, 10.0, 30.0]
    res["grid_gas_pro"] = dict(rows=gases, cols=pros, cells=grid("gas_usd", gases, "pro_threshold", pros))
    mins, safeties = [0.1, 0.5, 1.0, 3.0, 10.0], [1.0, 1.5, 3.0]
    res["grid_bot_cheap"] = dict(rows=mins, cols=safeties, cells=grid("min_profit", mins, "gas_safety", safeties))
    res["grid_bot_dear"] = dict(rows=mins, cols=safeties, cells=grid("min_profit", mins, "gas_safety", safeties, {"gas_usd": 2.0}))
    scenarios = {
        "Sleepy market (few slow rivals)": dict(pro_threshold=150, rival_delay=120),
        "Base case": {},
        "Contested (pros take anything over 20 USD, rivals react in 10 s)": dict(pro_threshold=20, rival_delay=10),
        "Fully professional (over 5 USD taken, 3 s)": dict(pro_threshold=5, rival_delay=3),
        "Ethereum mainnet gas (10 USD) and professional": dict(gas_usd=10, pro_threshold=5, rival_delay=3),
    }
    res["scenarios"] = {name: run({**m.BASE, **over}) for name, over in scenarios.items()}

    print("monte carlo", flush=True)
    with Pool(4) as pool:
        mc = pool.map(_mc_one, range(3000), chunksize=20)
    nets = np.array([r["net"] for r in mc])
    numeric = [k for k in mc[0] if k not in ("net", "wins", "risk", "small_full_close")]
    corr = {k: spearman([r[k] for r in mc], nets) for k in numeric}
    corr["risk"] = spearman([["cautious", "typical", "reckless"].index(r["risk"]) for r in mc], nets)
    res["mc"] = dict(
        n=len(mc), p_loss=float((nets < 0).mean()),
        percentiles={q: float(np.percentile(nets, q)) for q in (5, 10, 25, 50, 75, 90, 95, 99)},
        share_over={t: float((nets > t).mean()) for t in (0, 100, 500, 1000, 5000, 20000)},
        mean=float(nets.mean()), correlations=dict(sorted(corr.items(), key=lambda kv: -abs(kv[1]))),
    )
    # which conditions separate winners from losers
    def split(name, test):
        a = nets[[test(r) for r in mc]]
        return dict(name=name, n=len(a), median=float(np.median(a)), p_loss=float((a < 0).mean()), over_1000=float((a > 1000).mean()))
    res["mc"]["splits"] = [
        split("pros ignore profits under 50 USD", lambda r: r["pro_threshold"] >= 50),
        split("pros take everything over 10 USD", lambda r: r["pro_threshold"] < 10),
        split("rivals slower than 60 s", lambda r: r["rival_delay"] >= 60),
        split("rivals faster than 10 s", lambda r: r["rival_delay"] < 10),
        split("market under 2M USD", lambda r: r["market_debt"] < 2e6),
        split("market over 10M USD", lambda r: r["market_debt"] > 1e7),
        split("gas over 1 USD", lambda r: r["gas_usd"] > 1),
        split("gas under 0.05 USD", lambda r: r["gas_usd"] < 0.05),
        split("sleepy AND over 5M: pros >= 50, rivals >= 60 s", lambda r: r["pro_threshold"] >= 50 and r["rival_delay"] >= 60 and r["market_debt"] > 5e6),
        split("professional: pros < 10 or rivals < 5 s", lambda r: r["pro_threshold"] < 10 or r["rival_delay"] < 5),
    ]
    (HERE / "results.json").write_text(json.dumps(res, indent=1))
    print("done", flush=True)


if __name__ == "__main__":
    main()
