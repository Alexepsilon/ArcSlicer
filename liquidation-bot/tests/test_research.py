"""The economic model must price a liquidation exactly like the real contract does."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "research"))
import model  # noqa: E402

from test_bot import params, underwater  # noqa: E402


@pytest.mark.parametrize("slippage_bps", [0, 30, 100, 300])
@pytest.mark.parametrize("cover", [100, 750])
def test_model_profit_matches_contract(market, slippage_bps, cover):
    user = underwater(market)
    market.router.functions.setSlippage(slippage_bps).transact({"from": market.admin})
    on_chain = market.liquidator.functions.liquidate(params(market, user, cover * 10**6)).call(
        {"from": market.bot.address}
    ) / 10**6
    p = {**model.BASE, "bonus": 0.05, "protocol_fee": 0.0, "flash_fee": 0.0005,
         "dex_fee": slippage_bps / 10_000, "dex_depth": np.inf, "oracle_lag": 0.0}
    assert float(model.liquidation_profit(cover, p)) == pytest.approx(on_chain, abs=1e-5)


def test_first_passage_search_is_exact():
    rng = np.random.default_rng(0)
    price = np.exp(np.cumsum(rng.normal(0, 0.01, model.T)))
    tables = model._min_tables(price)
    start = rng.integers(0, model.T - 1, 200)
    level = price[start] * rng.uniform(0.7, 1.0, 200)
    got = model._first_at_or_below(tables, start, level)
    for s, lv, g in zip(start, level, got):
        hits = np.nonzero(price[s:] <= lv)[0]
        assert g == (s + hits[0] if len(hits) else g) and (len(hits) > 0) == (g < model.T)


def test_no_competition_and_no_costs_captures_everything():
    p = {**model.BASE, "pro_threshold": 1e12, "latency": 0.0, "gas_usd": 0.0, "infra_month": 0.0, "min_profit": -1e9}
    events = model.market_events(model.price_paths(1)[0], p, 0)
    year = model.year_result(events, p, 0)
    assert year["wins"] == year["events"] > 0
    assert year["net"] == pytest.approx(model.liquidation_profit(events["cover"], p, events["drop"]).sum())
