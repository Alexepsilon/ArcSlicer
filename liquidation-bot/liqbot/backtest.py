"""Backtest: replay every liquidation that really happened in a block range and ask
"would this bot have found it, and what would it have earned with paper money?"

For each historical liquidation at block B we look at the state one block earlier:
  * position already liquidatable at B-1 -> we had at least a block to act; simulate our
    own liquidation there and book the result ("capturable" or "unprofitable")
  * still healthy at B-1 -> it was taken in the same block the price moved ("same_block").
    Winning those needs pure speed, so they are counted separately and never booked.

The result is an UPPER BOUND: it assumes we beat every competitor we had time to beat.
`lead_blocks` (how long a position sat liquidatable before someone took it) tells you
how realistic that is. Needs an archive RPC that supports eth_call state overrides.
"""
from __future__ import annotations

from .chain import LIQUIDATION_TOPIC, Chain
from .config import Config
from .engine import Engine
from .planner import WAD
from .store import Store

LOOKBACKS = [1, 2, 3, 5, 10, 25, 100]


def lead_blocks(chain: Chain, user: str, block: int) -> int:
    """Roughly how many blocks before `block` the user was already liquidatable."""
    lead = 0
    for back in LOOKBACKS:
        if block - back < 0:
            break
        health = chain.health([user], block - back).get(user)
        if not health or health[2] >= WAD:
            break
        lead = back
    return lead


def run(cfg: Config, chain: Chain, store: Store, from_block: int, to_block: int) -> dict:
    engine = Engine(cfg, chain, store)
    store.clear_mode("backtest")
    logs = chain.logs(from_block, to_block, [LIQUIDATION_TOPIC])
    store.log(f"backtest: {len(logs)} liquidation events in blocks {from_block}-{to_block}")

    seen = set()
    for log in logs:
        event = chain.decode_log(log)
        user, block = event["user"], event["block"]
        if (user, block) in seen:
            continue
        seen.add((user, block))
        base = dict(mode="backtest", block=block, user=user, rival_block=block, note=event["liquidator"])

        lead = lead_blocks(chain, user, block)
        if lead == 0:
            store.add_trade(status="same_block", lead_blocks=0, net_base=0, **base)
            continue
        hf = chain.health([user], block - 1)[user][2]
        opp = engine.evaluate(user, hf, block=block - 1, block_number=block - 1)
        if opp is None:
            store.add_trade(status="no_route", lead_blocks=lead, net_base=0, **base)
            continue
        row = engine.trade_row(opp)
        row.update(base)
        status = "capturable" if engine.is_profitable(opp) else "unprofitable"
        store.add_trade(status=status, lead_blocks=lead, **row)

    summary = store.summary("backtest")
    summary["events"] = len(seen)
    summary["balance"] = cfg.paper_start_balance + summary["net"]
    return summary


def report(summary: dict, cfg: Config) -> str:
    by = summary["by_status"]
    lines = [
        f"Historical liquidations replayed : {summary['events']}",
        f"  capturable and profitable      : {by.get('capturable', 0)}",
        f"  had time, but not profitable   : {by.get('unprofitable', 0)}",
        f"  had time, no working swap route: {by.get('no_route', 0)}",
        f"  same-block (speed race only)   : {by.get('same_block', 0)}",
        f"Paper result (upper bound)       : {summary['net']:+.2f} after {summary['gas']:.2f} gas",
        f"Paper balance                    : {cfg.paper_start_balance:.2f} -> {summary['balance']:.2f}",
    ]
    return "\n".join(lines)
