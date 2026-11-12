"""End-to-end tests on the fake market: contract safety, paper mode, live mode, backtest."""
import pytest

from liqbot import backtest
from liqbot.engine import Engine
from liqbot.routes import v3_path


def params(m, user, cover, min_profit=0, kind=2):
    pool = m.protocol.address
    path = v3_path([m.weth.address, m.usdc.address], [500])
    return (pool, pool, m.weth.address, m.usdc.address, user, cover, kind, m.router.address, path, min_profit)


def underwater(m, index=5):
    user = m.w3.eth.accounts[index]
    m.open_position(user)  # 1 WETH at 2000 vs 1500 USDC: health factor 1.07
    m.set_eth_price(1800)  # health factor 0.96
    return user


# --- contract ---------------------------------------------------------------

def test_contract_liquidates_and_pays_owner(market):
    user = underwater(market)
    call = market.liquidator.functions.liquidate(params(market, user, 750 * 10**6))
    profit = call.call({"from": market.bot.address})
    # 750 repaid -> 787.5 of collateral seized -> 37.5 gross, minus 0.05% flash fee (0.375)
    assert profit == 37_125_000


def test_contract_reverts_when_profit_below_minimum(market):
    user = underwater(market)
    call = market.liquidator.functions.liquidate(params(market, user, 750 * 10**6, min_profit=40 * 10**6))
    with pytest.raises(Exception):
        call.call({"from": market.bot.address})


def test_contract_reverts_when_swap_loses_money(market):
    user = underwater(market)
    market.router.functions.setSlippage(600).transact({"from": market.admin})  # 6% > 5% bonus
    with pytest.raises(Exception):
        market.liquidator.functions.liquidate(params(market, user, 750 * 10**6)).call({"from": market.bot.address})


def test_contract_rejects_strangers(market):
    user = underwater(market)
    with pytest.raises(Exception):
        market.liquidator.functions.liquidate(params(market, user, 750 * 10**6)).call({"from": market.admin})
    with pytest.raises(Exception):  # callback cannot be invoked directly
        market.liquidator.functions.executeOperation(
            market.usdc.address, 1, 0, market.liquidator.address, b""
        ).call({"from": market.admin})


@pytest.mark.parametrize("kind", [0, 1, 2])
def test_all_router_dialects(market, kind):
    from eth_abi import encode
    user = underwater(market)
    p = list(params(market, user, 750 * 10**6, kind=kind))
    if kind == 0:
        p[8] = encode(["address[]"], [[market.weth.address, market.usdc.address]])
    assert market.liquidator.functions.liquidate(tuple(p)).call({"from": market.bot.address}) == 37_125_000


# --- bot --------------------------------------------------------------------

def test_paper_mode_records_once_and_moves_no_money(market):
    cfg, chain, store = market.make("paper")
    engine = Engine(cfg, chain, store)
    healthy = market.w3.eth.accounts[6]
    market.open_position(healthy, usdc_debt=500)
    user = underwater(market)

    engine.run_once()
    engine.run_once()  # second tick must not book the same position again

    trades = store.rows("SELECT * FROM trades")
    assert len(trades) == 1
    t = trades[0]
    assert (t["mode"], t["status"], t["user"]) == ("paper", "paper", user)
    assert t["profit_base"] == pytest.approx(37.125)
    assert 0 < t["gas_base"] < 1 and t["net_base"] == pytest.approx(t["profit_base"] - t["gas_base"])
    assert market.usdc.functions.balanceOf(market.bot.address).call() == 0  # nothing real happened
    status = store.get("status")
    assert status["borrowers"] == 2 and status["liquidatable"] == 1
    assert store.summary("paper")["trades"] == 1


def test_paper_mode_notes_when_a_rival_takes_the_position(market):
    cfg, chain, store = market.make("paper")
    engine = Engine(cfg, chain, store)
    user = underwater(market)
    engine.run_once()
    market.rival_liquidates(user)  # a competitor liquidates for real a little later
    engine.run_once()
    t = store.rows("SELECT * FROM trades")[0]
    assert t["rival_block"] > t["block"] and t["lead_blocks"] == t["rival_block"] - t["block"]
    assert store.rows("SELECT liquidator FROM market_liqs")[0]["liquidator"] == market.admin


def test_live_mode_sends_transaction_and_collects_profit(market):
    cfg, chain, store = market.make("live")
    engine = Engine(cfg, chain, store)
    user = underwater(market)
    engine.run_once()

    t = store.rows("SELECT * FROM trades")[0]
    assert (t["mode"], t["status"]) == ("live", "success") and t["tx_hash"]
    assert market.usdc.functions.balanceOf(market.bot.address).call() == 37_125_000
    # Position was half repaid, so it is healthy again and nothing more is attempted.
    engine.run_once()
    assert len(store.rows("SELECT * FROM trades")) == 1
    assert market.protocol.functions.positions(user).call()[3] == 750 * 10**6


def test_unprofitable_position_is_skipped(market):
    cfg, chain, store = market.make("live", min_profit=100.0)  # 37 of profit is not enough
    engine = Engine(cfg, chain, store)
    underwater(market)
    engine.run_once()
    assert store.rows("SELECT * FROM trades") == []
    assert any("too small" in e["msg"] for e in store.rows("SELECT msg FROM events"))


def test_dust_position_is_ignored_without_simulation(market):
    cfg, chain, store = market.make("paper")
    engine = Engine(cfg, chain, store)
    user = market.w3.eth.accounts[5]
    market.open_position(user, weth_amount=0.001, usdc_debt=2)  # worth 2 dollars
    market.set_eth_price(1800)
    engine.run_once()
    assert store.rows("SELECT * FROM trades") == [] and user in engine.retry_at


def test_backtest_replays_a_historical_liquidation(market):
    cfg, chain, store = market.make("paper")
    user = underwater(market)
    market.mine(3)  # position sits liquidatable for a while
    market.rival_liquidates(user)
    head = market.w3.eth.block_number

    summary = backtest.run(cfg, chain, store, 1, head)
    assert summary["events"] == 1 and summary["by_status"] == {"capturable": 1}
    t = store.rows("SELECT * FROM trades WHERE mode='backtest'")[0]
    assert t["profit_base"] == pytest.approx(37.125) and t["lead_blocks"] >= 3
    assert summary["balance"] == pytest.approx(cfg.paper_start_balance + t["net_base"])
    assert "capturable and profitable      : 1" in backtest.report(summary, cfg)
