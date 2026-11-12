"""The bot loop: discover borrowers, watch health factors, evaluate and act."""
from __future__ import annotations

import time
from dataclasses import dataclass

from .chain import BORROW_TOPIC, LIQUIDATION_TOPIC, Chain
from .config import Config
from .planner import WAD, Candidate, candidates, from_base, to_base
from .routes import Route, build_routes
from .store import Store

MAX_BONUS = 0.2  # no sane market pays more; used to skip dust positions cheaply
RETRY_TICKS = 15  # wait this long before re-evaluating a position that was not profitable


@dataclass
class Opportunity:
    user: str
    block: int
    candidate: Candidate
    route: Route
    params: tuple
    profit: int  # debt-asset units
    profit_base: float
    gas: int
    gas_price: int
    gas_base: float

    @property
    def net_base(self) -> float:
        return self.profit_base - self.gas_base


def hf_float(hf: int) -> float:
    return min(hf / WAD, 1e6)


class Engine:
    def __init__(self, cfg: Config, chain: Chain, store: Store):
        self.cfg, self.chain, self.store = cfg, chain, store
        self.tick = 0
        self.retry_at: dict[str, int] = {}
        # Borrowers already paper-liquidated; the chain state never changes in paper mode,
        # so without this the same position would be "liquidated" on every tick.
        traded = {r["user"] for r in store.rows("SELECT DISTINCT user FROM trades WHERE mode='paper'")}
        self.paper_done = traded & set(store.borrowers(below_hf=1.0))

    # --- discovery -------------------------------------------------------
    def sync_logs(self, head: int) -> None:
        last = self.store.get("last_block")
        if last is None:
            last = (self.cfg.start_block or max(head - 100_000, 0)) - 1
            self.store.log(f"first run: scanning pool events from block {last + 1}")
        if head <= last:
            return
        borrowers = set()
        for log in self.chain.logs(last + 1, head, [BORROW_TOPIC, LIQUIDATION_TOPIC]):
            event = self.chain.decode_log(log)
            if event.pop("type") == "borrow":
                borrowers.add(event["user"])
            else:
                self.store.add_market_liq(**event)
        self.store.add_borrowers(borrowers)
        self.store.set("last_block", head)

    def refresh_health(self, users: list[str], block="latest") -> list[tuple[str, int, int]]:
        """Updates stored health and returns the liquidatable (user, hf, debt_base)."""
        health = self.chain.health(users, block)
        unit = self.chain.base_unit
        rows, gone, under = [], [], []
        for user, (coll, debt, hf) in health.items():
            if debt == 0:
                gone.append(user)
                continue
            rows.append((user, hf_float(hf), coll / unit, debt / unit))
            if hf < WAD:
                under.append((user, hf, debt))
            else:
                self.paper_done.discard(user)
        self.store.update_health(rows)
        self.store.remove_borrowers(gone)
        return under

    # --- evaluation ------------------------------------------------------
    def evaluate(self, user: str, hf: int, block="latest", block_number: int = 0) -> Opportunity | None:
        """Simulates every candidate/route and returns the most profitable one, if any."""
        chain = self.chain
        prices = chain.prices(block)
        positions = chain.positions(user, block)
        hubs = [chain.native, *self.cfg.hubs]
        pool = chain.pool.address

        best = None
        for cand in candidates(positions, chain.reserves, prices, hf):
            for route in build_routes(self.cfg.dexes, cand.collateral, cand.debt, hubs):
                params = (pool, pool, cand.collateral, cand.debt, user, cand.debt_to_cover,
                          route.kind, route.router, route.path, 0)
                profit = chain.simulate(params, block)
                if profit is None:
                    continue
                value = to_base(profit, prices[cand.debt], chain.reserves[cand.debt].decimals)
                if best is None or value > best[0]:
                    best = (value, cand, route, params, profit)
        if best is None:
            return None

        value, cand, route, params, profit = best
        gas = chain.estimate_gas(params, block)
        gas_price = chain.gas_price(block)
        gas_base = gas * gas_price * prices[chain.native] / 10**18 / chain.base_unit
        return Opportunity(user, block_number, cand, route, params, profit,
                           value / chain.base_unit, gas, gas_price, gas_base)

    def is_profitable(self, opp: Opportunity) -> bool:
        return opp.profit_base - opp.gas_base * self.cfg.gas_safety >= self.cfg.min_profit

    def trade_row(self, opp: Opportunity) -> dict:
        symbol = lambda a: self.chain.reserves[a].symbol
        return dict(
            block=opp.block, user=opp.user, collateral=symbol(opp.candidate.collateral),
            debt=symbol(opp.candidate.debt), debt_to_cover=str(opp.candidate.debt_to_cover),
            route=opp.route.label, profit_base=opp.profit_base, gas_base=opp.gas_base, net_base=opp.net_base,
        )

    # --- acting ----------------------------------------------------------
    def act(self, opp: Opportunity) -> None:
        row = self.trade_row(opp)
        if self.cfg.mode == "paper":
            self.paper_done.add(opp.user)
            self.store.add_trade(mode="paper", status="paper", **row)
            self.store.log(f"PAPER liquidation of {opp.user}: net {opp.net_base:+.2f} "
                           f"({row['collateral']}->{row['debt']}, {opp.route.label})", "trade")
            return

        # Live: the contract itself reverts unless profit covers gas (with margin) plus the minimum.
        chain, debt = self.chain, opp.candidate.debt
        need_base = int((opp.gas_base * self.cfg.gas_safety + self.cfg.min_profit) * chain.base_unit)
        min_profit = from_base(need_base, chain.prices()[debt], chain.reserves[debt].decimals)
        params = (*opp.params[:-1], min_profit)
        try:
            receipt = chain.send(params, int(opp.gas * 1.25), opp.gas_price)
        except Exception as exc:
            self.store.log(f"send failed for {opp.user}: {exc}", "error")
            return
        paid = receipt["gasUsed"] * receipt.get("effectiveGasPrice", opp.gas_price)
        row["gas_base"] = opp.gas_base * paid / (opp.gas * opp.gas_price)
        ok = receipt["status"] == 1
        row["profit_base"] = opp.profit_base if ok else 0.0
        row["net_base"] = row["profit_base"] - row["gas_base"]
        tx_hash = bytes(receipt["transactionHash"]).hex()
        self.store.add_trade(mode="live", status="success" if ok else "reverted", tx_hash=tx_hash, **row)
        self.store.log(f"LIVE liquidation of {opp.user} {'succeeded' if ok else 'REVERTED'}: "
                       f"net {row['net_base']:+.2f} tx {tx_hash}", "trade" if ok else "warn")

    # --- main loop -------------------------------------------------------
    def run_once(self) -> None:
        started = time.time()
        head = self.chain.w3.eth.block_number
        self.sync_logs(head)

        full = self.tick % self.cfg.full_rescan_every == 0
        users = self.store.borrowers() if full else self.store.borrowers(below_hf=self.cfg.hot_hf)
        under = self.refresh_health(users) if users else []

        for user, hf, debt in sorted(under, key=lambda u: -u[2]):
            if user in self.paper_done or self.retry_at.get(user, 0) > self.tick:
                continue
            if debt / self.chain.base_unit * MAX_BONUS < self.cfg.min_profit:
                self.retry_at[user] = self.tick + 10 * RETRY_TICKS  # dust: bonus cannot cover costs
                continue
            opp = self.evaluate(user, hf, block_number=head)
            if opp and self.is_profitable(opp):
                self.act(opp)
            else:
                self.retry_at[user] = self.tick + RETRY_TICKS
                why = "no working swap route" if opp is None else f"net {opp.net_base:+.2f} too small"
                self.store.log(f"skip {user} (HF {hf_float(hf):.3f}): {why}")

        lowest = self.store.rows("SELECT MIN(hf) hf FROM borrowers WHERE hf IS NOT NULL")[0]["hf"]
        self.store.set("status", {
            "ts": time.time(), "mode": self.cfg.mode, "chain_id": self.chain.chain_id, "block": head,
            "borrowers": len(self.store.borrowers()), "hot": len(self.store.borrowers(self.cfg.hot_hf)),
            "liquidatable": len(under), "lowest_hf": lowest, "tick_ms": int((time.time() - started) * 1000),
            "start_balance": self.cfg.paper_start_balance,
        })
        self.tick += 1

    def run_forever(self) -> None:
        self.store.log(f"bot started in {self.cfg.mode.upper()} mode on chain {self.chain.chain_id}, "
                       f"{len(self.chain.reserves)} reserves, owner {self.chain.owner}")
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # RPC hiccups must not kill the bot
                self.store.log(f"tick failed: {type(exc).__name__}: {exc}", "error")
                time.sleep(min(30, self.cfg.poll_seconds * 5))
            time.sleep(self.cfg.poll_seconds)
