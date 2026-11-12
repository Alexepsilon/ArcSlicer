"""Demo on a fake in-memory market: no RPC, no money, no keys needed.

Runs the bot in paper mode through a simulated ETH price crash, lets a "rival" liquidator
take the same positions a few blocks later, then backtests the same history.
Afterwards look at the result with:  python -m liqbot monitor -c demo.toml

Usage: python scripts/demo.py
"""
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from liqbot import backtest  # noqa: E402
from liqbot.engine import Engine  # noqa: E402
from liqbot.sim import KEY, build_market  # noqa: E402

DB = "demo.db"


def main():
    for suffix in ("", "-wal", "-shm"):
        Path(DB + suffix).unlink(missing_ok=True)
    os.environ["LIQBOT_PRIVATE_KEY"] = KEY
    m = build_market(DB)
    cfg, chain, store = m.make("paper", full_rescan_every=1)
    engine = Engine(cfg, chain, store)
    rng = random.Random(7)

    # Borrowers with 0.2-6 ETH of collateral, borrowing 55-78% of its value (limit is 80%).
    users = m.w3.eth.accounts[1:10]
    for user in users:
        eth = rng.uniform(0.2, 6)
        m.open_position(user, eth, round(eth * 2000 * rng.uniform(0.55, 0.78)))
    m.open_position(m.w3.eth.accounts[0], 0.002, 3)  # dust the bot should ignore

    pending = {}  # user -> blocks until the rival liquidates it
    for price in [2000, 1980, 1940, 1900, 1850, 1800, 1760, 1700, 1650, 1600, 1540, 1480, 1500, 1550]:
        m.set_eth_price(price)
        engine.run_once()
        for row in store.rows("SELECT user FROM trades WHERE mode='paper' AND rival_block IS NULL"):
            pending.setdefault(row["user"], rng.choice([1, 1, 2, 4]))
        for user in [u for u, wait in pending.items() if wait == 0]:
            del pending[user]
            try:
                m.rival_liquidates(user)
            except Exception:
                pass  # the price recovered and the position is healthy again
        pending = {u: w - 1 for u, w in pending.items()}
        m.mine(1)
    engine.run_once()

    print("\n--- paper trading ---")
    s = store.summary("paper")
    print(f"{s['trades']} paper liquidations, net {s['net']:+.2f} after {s['gas']:.2f} gas")
    print("\n--- backtest of the same history ---")
    print(backtest.report(backtest.run(cfg, chain, store, 1, m.w3.eth.block_number), cfg))
    Path("demo.toml").write_text(
        f'[chain]\nrpc_url = ""\nwrapped_native = "{m.weth.address}"\n'
        f'[protocol]\naddresses_provider = "{m.protocol.address}"\n[bot]\ndb_path = "{DB}"\n'
    )
    print("\nNow run:  python -m liqbot monitor -c demo.toml")


if __name__ == "__main__":
    main()
