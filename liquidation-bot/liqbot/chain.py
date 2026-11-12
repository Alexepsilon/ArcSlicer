"""All on-chain reads and writes. Every read takes a block so the backtest can
replay history with exactly the same code the live bot uses."""
from __future__ import annotations

import json
from pathlib import Path

from eth_account import Account
from web3 import Web3

from .config import Config
from .planner import Reserve, UserReserve

ARTIFACTS = Path(__file__).parent / "artifacts"
# Where the liquidator "lives" when it is injected by state override instead of deployed.
VIRTUAL_LIQUIDATOR = Web3.to_checksum_address("0x00000000000000000000000000000000004c4951")
BORROW_TOPIC = Web3.to_hex(Web3.keccak(text="Borrow(address,address,address,uint256,uint8,uint256,uint16)"))
LIQUIDATION_TOPIC = Web3.to_hex(
    Web3.keccak(text="LiquidationCall(address,address,address,uint256,uint256,address,bool)")
)


def artifact(name: str) -> dict:
    return json.loads((ARTIFACTS / f"{name}.json").read_text())


def _topic_addr(topic) -> str:
    return Web3.to_checksum_address(bytes(topic)[-20:])


class Chain:
    def __init__(self, cfg: Config, w3: Web3 | None = None, force_virtual: bool = False):
        self.cfg = cfg
        self.w3 = w3 or Web3(Web3.HTTPProvider(cfg.rpc_url, request_kwargs={"timeout": 20}))
        self.chain_id = self.w3.eth.chain_id
        if cfg.chain_id and self.chain_id != cfg.chain_id:
            raise RuntimeError(f"RPC is chain {self.chain_id}, config expects {cfg.chain_id}")

        provider = self._contract("IAddressesProvider", cfg.addresses_provider)
        self.pool = self._contract("IAavePool", provider.functions.getPool().call())
        self.oracle = self._contract("IOracle", provider.functions.getPriceOracle().call())
        self.data = self._contract("IDataProvider", provider.functions.getPoolDataProvider().call())
        self.multicall3 = self._contract("IMulticall3", cfg.multicall3)
        self.native = Web3.to_checksum_address(cfg.wrapped_native)
        self.base_unit = self.oracle.functions.BASE_CURRENCY_UNIT().call()

        key = cfg.private_key
        self.account = Account.from_key(key) if key else None
        owner = self.account.address if self.account else cfg.owner
        if not owner:
            raise RuntimeError("set bot.owner in the config (any address you control) or LIQBOT_PRIVATE_KEY")
        self.owner = Web3.to_checksum_address(owner)

        # Paper trading and backtests do not need a deployed contract: the code is injected
        # into eth_call with a state override, with storage slot 0 (owner) set to us.
        self.override = None
        if cfg.liquidator and not force_virtual:
            address = cfg.liquidator
        else:
            address = VIRTUAL_LIQUIDATOR
            slot0 = "0x" + "00" * 31 + "00"
            owner_word = "0x" + "00" * 12 + self.owner[2:].lower()
            code = artifact("FlashLiquidator")["deployedBytecode"]
            self.override = {address: {"code": code, "stateDiff": {slot0: owner_word}}}
        self.liquidator = self._contract("FlashLiquidator", address)
        self.reserves: dict[str, Reserve] = {}
        self.refresh_reserves()

    def _contract(self, name: str, address: str):
        return self.w3.eth.contract(address=Web3.to_checksum_address(address), abi=artifact(name)["abi"])

    # --- generic helpers -------------------------------------------------
    def multicall(self, calls: list[tuple[str, bytes]], block="latest") -> list[tuple[bool, bytes]]:
        out: list[tuple[bool, bytes]] = []
        size = self.cfg.multicall_batch
        for i in range(0, len(calls), size):
            batch = [(target, True, data) for target, data in calls[i : i + size]]
            out += self.multicall3.functions.aggregate3(batch).call(block_identifier=block)
        return out

    def logs(self, from_block: int, to_block: int, topics: list) -> list:
        """eth_getLogs over a range, shrinking the chunk when the RPC complains."""
        out, start, chunk = [], from_block, self.cfg.log_chunk
        while start <= to_block:
            end = min(start + chunk - 1, to_block)
            try:
                out += self.w3.eth.get_logs(
                    {"address": self.pool.address, "fromBlock": start, "toBlock": end, "topics": [topics]}
                )
            except Exception:
                if chunk <= 10:
                    raise
                chunk //= 2
                continue
            start = end + 1
        return out

    @staticmethod
    def decode_log(log) -> dict:
        """Returns {'type': 'borrow', 'user'} or {'type': 'liquidation', ...}."""
        topics = log["topics"]
        if Web3.to_hex(topics[0]) == BORROW_TOPIC:
            return {"type": "borrow", "user": _topic_addr(topics[2])}
        data = bytes(log["data"])
        return {
            "type": "liquidation",
            "collateral": _topic_addr(topics[1]),
            "debt": _topic_addr(topics[2]),
            "user": _topic_addr(topics[3]),
            "liquidator": Web3.to_checksum_address(data[64 + 12 : 96]),
            "block": log["blockNumber"],
            "tx_hash": bytes(log["transactionHash"]).hex(),
            "log_index": log["logIndex"],
        }

    # --- protocol reads --------------------------------------------------
    def refresh_reserves(self) -> None:
        assets = self.pool.functions.getReservesList().call()
        erc20 = self._contract("IERC20", assets[0]) if assets else None
        calls = []
        for a in assets:
            calls.append((self.data.address, self.data.encode_abi("getReserveConfigurationData", args=[a])))
            calls.append((a, erc20.encode_abi("symbol", args=[])))
        res = self.multicall(calls)
        for i, a in enumerate(assets):
            ok, cfg_data = res[2 * i]
            if not ok:
                continue
            decimals, _, _, bonus = self.w3.codec.decode(["uint256"] * 4, cfg_data[:128])
            try:
                symbol = self.w3.codec.decode(["string"], res[2 * i + 1][1])[0]
            except Exception:
                symbol = a[:8]  # non-standard token (bytes32 symbol or none)
            self.reserves[a] = Reserve(a, symbol, decimals, bonus)

    def prices(self, block="latest") -> dict[str, int]:
        assets = list(self.reserves)
        if self.native not in self.reserves:
            assets.append(self.native)
        values = self.oracle.functions.getAssetsPrices(assets).call(block_identifier=block)
        return dict(zip(assets, values))

    def health(self, users: list[str], block="latest") -> dict[str, tuple[int, int, int]]:
        """user -> (collateral in base units, debt in base units, health factor in wad)."""
        calls = [(self.pool.address, self.pool.encode_abi("getUserAccountData", args=[u])) for u in users]
        out = {}
        for user, (ok, data) in zip(users, self.multicall(calls, block)):
            if ok:
                coll, debt, _, _, _, hf = self.w3.codec.decode(["uint256"] * 6, data)
                out[user] = (coll, debt, hf)
        return out

    def positions(self, user: str, block="latest") -> list[UserReserve]:
        assets = list(self.reserves)
        calls = [(self.data.address, self.data.encode_abi("getUserReserveData", args=[a, user])) for a in assets]
        types = ["uint256"] * 7 + ["uint40", "bool"]
        out = []
        for asset, (ok, data) in zip(assets, self.multicall(calls, block)):
            if not ok:
                continue
            row = self.w3.codec.decode(types, data)
            atoken, stable, variable, as_collateral = row[0], row[1], row[2], row[8]
            if atoken or stable or variable:
                out.append(UserReserve(asset, atoken if as_collateral else 0, stable + variable))
        return out

    def gas_price(self, block="latest") -> int:
        if block == "latest":
            return int(self.w3.eth.gas_price * self.cfg.gas_price_multiplier)
        base_fee = self.w3.eth.get_block(block).get("baseFeePerGas")
        return int((base_fee or self.w3.eth.gas_price) * self.cfg.gas_price_multiplier)

    # --- liquidator ------------------------------------------------------
    def simulate(self, params: tuple, block="latest") -> int | None:
        """Profit in debt-asset units if the liquidation would succeed, else None."""
        try:
            return self.liquidator.functions.liquidate(params).call(
                {"from": self.owner}, block_identifier=block, state_override=self.override
            )
        except Exception:
            return None

    def estimate_gas(self, params: tuple, block="latest") -> int:
        try:
            tx = self.liquidator.functions.liquidate(params).build_transaction(
                {"from": self.owner, "gas": 5_000_000, "gasPrice": 0, "nonce": 0, "chainId": self.chain_id}
            )
            call = {"from": tx["from"], "to": tx["to"], "data": tx["data"]}
            return self.w3.eth.estimate_gas(call, block, self.override)
        except Exception:
            return self.cfg.gas_fallback

    def send(self, params: tuple, gas: int, gas_price: int):
        """Sign and broadcast a live liquidation. Returns the receipt."""
        tx = self.liquidator.functions.liquidate(params).build_transaction(
            {
                "from": self.owner,
                "nonce": self.w3.eth.get_transaction_count(self.owner),
                "gas": gas,
                "gasPrice": gas_price,
                "chainId": self.chain_id,
            }
        )
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
