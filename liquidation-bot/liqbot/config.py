"""Configuration loading. Secrets never live in the config file: the private key
is read from the LIQBOT_PRIVATE_KEY environment variable, and only in live mode."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
SWAP_KINDS = {"v2": 0, "v3": 1, "v3_02": 2}


@dataclass
class Dex:
    kind: str
    router: str
    fees: list[int] = field(default_factory=lambda: [500, 3000])

    def __post_init__(self):
        if self.kind not in SWAP_KINDS:
            raise ValueError(f"dex kind must be one of {sorted(SWAP_KINDS)}, got {self.kind!r}")


@dataclass
class Config:
    # chain
    rpc_url: str
    wrapped_native: str
    addresses_provider: str
    chain_id: int = 0  # 0 = do not check
    multicall3: str = MULTICALL3
    start_block: int = 0
    hubs: list[str] = field(default_factory=list)  # extra intermediate swap tokens
    dexes: list[Dex] = field(default_factory=list)
    # bot
    mode: str = "paper"  # "paper" or "live"
    liquidator: str = ""  # deployed FlashLiquidator; may be empty in paper mode
    owner: str = ""  # only needed in paper mode without a private key
    db_path: str = "liqbot.db"
    poll_seconds: float = 2.0
    full_rescan_every: int = 30  # ticks between checks of every known borrower
    hot_hf: float = 1.05  # borrowers below this health factor are checked every tick
    min_profit: float = 1.0  # in the pool's base currency (USD on almost every deployment)
    gas_safety: float = 1.5  # profit must cover gas cost times this factor
    gas_price_multiplier: float = 1.1
    gas_fallback: int = 900_000  # used when the RPC cannot estimate gas
    log_chunk: int = 2000
    multicall_batch: int = 150
    paper_start_balance: float = 550.0  # about EUR 500, in base currency

    def __post_init__(self):
        if self.mode not in ("paper", "live"):
            raise ValueError("mode must be 'paper' or 'live'")
        if self.mode == "live" and not self.liquidator:
            raise ValueError("live mode needs bot.liquidator (deploy the contract first)")

    @property
    def private_key(self) -> str | None:
        return os.environ.get("LIQBOT_PRIVATE_KEY") or None


def load(path: str) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    flat = {**raw.get("chain", {}), **raw.get("protocol", {}), **raw.get("bot", {})}
    flat["dexes"] = [Dex(**d) for d in raw.get("dex", [])]
    return Config(**flat)
