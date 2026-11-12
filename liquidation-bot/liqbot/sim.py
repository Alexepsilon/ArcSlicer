"""A complete fake market on an in-memory EVM (lending pool, DEX, multicall, liquidator).
Used by the tests and by scripts/demo.py. Needs the dev dependencies (eth-tester)."""
from __future__ import annotations

from types import SimpleNamespace

from eth_account import Account
from web3 import EthereumTesterProvider, Web3

from .chain import Chain, artifact
from .config import Config, Dex
from .store import Store

KEY = "0x" + "11" * 32  # throwaway test key, never use on a real chain
USD = 10**8


def deploy(w3, name, *args):
    art = artifact(name)
    tx = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]).constructor(*args).transact(
        {"from": w3.eth.accounts[0]}
    )
    return w3.eth.contract(address=w3.eth.wait_for_transaction_receipt(tx)["contractAddress"], abi=art["abi"])


def build_market(db_path: str) -> SimpleNamespace:
    w3 = Web3(EthereumTesterProvider())
    admin = w3.eth.accounts[0]
    bot = Account.from_key(KEY)
    w3.eth.send_transaction({"from": admin, "to": bot.address, "value": 10**18})

    weth = deploy(w3, "MockERC20", "WETH", 18)
    usdc = deploy(w3, "MockERC20", "USDC", 6)
    protocol = deploy(w3, "MockProtocol")
    router = deploy(w3, "MockRouter", protocol.address)
    multicall = deploy(w3, "MockMulticall3")
    protocol.functions.addReserve(weth.address, 2000 * USD).transact({"from": admin})
    protocol.functions.addReserve(usdc.address, 1 * USD).transact({"from": admin})
    usdc.functions.mint(protocol.address, 10**6 * 10**6).transact({"from": admin})  # flash-loan liquidity
    usdc.functions.mint(admin, 10**6 * 10**6).transact({"from": admin})  # for the "rival" liquidator
    usdc.functions.approve(protocol.address, 2**255).transact({"from": admin})

    # The liquidator must be owned by the bot key, so it is deployed from that account.
    art = artifact("FlashLiquidator")
    raw = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]).constructor().build_transaction(
        {"from": bot.address, "nonce": 0, "gas": 3_000_000, "gasPrice": w3.eth.gas_price}
    )
    tx = w3.eth.send_raw_transaction(bot.sign_transaction(raw).raw_transaction)
    liquidator = w3.eth.contract(address=w3.eth.wait_for_transaction_receipt(tx)["contractAddress"], abi=art["abi"])

    def make(mode="paper", **overrides):
        cfg = Config(
            rpc_url="", wrapped_native=weth.address, addresses_provider=protocol.address,
            multicall3=multicall.address, liquidator=liquidator.address, mode=mode, start_block=1,
            dexes=[Dex("v3_02", router.address, [500]), Dex("v3", router.address, [3000]), Dex("v2", router.address)],
            db_path=db_path, **overrides,
        )
        return cfg, Chain(cfg, w3), Store(db_path)

    def open_position(user, weth_amount=1.0, usdc_debt=1500):
        protocol.functions.setPosition(
            user, weth.address, usdc.address, int(weth_amount * 10**18), int(usdc_debt * 10**6)
        ).transact({"from": admin})

    def set_eth_price(usd):
        protocol.functions.setPrice(weth.address, int(usd * USD)).transact({"from": admin})

    def rival_liquidates(user, usdc_amount=10**9):
        """Someone else liquidates the position for real."""
        protocol.functions.liquidationCall(
            weth.address, usdc.address, user, int(usdc_amount * 10**6), False
        ).transact({"from": admin})

    return SimpleNamespace(
        w3=w3, admin=admin, bot=bot, weth=weth, usdc=usdc, protocol=protocol, router=router,
        liquidator=liquidator, make=make, open_position=open_position, set_eth_price=set_eth_price,
        rival_liquidates=rival_liquidates, mine=w3.provider.ethereum_tester.mine_blocks,
    )
