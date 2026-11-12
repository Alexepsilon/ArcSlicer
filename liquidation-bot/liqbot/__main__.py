"""Command line: python -m liqbot <run|backtest|monitor|deploy> ..."""
from __future__ import annotations

import argparse

from . import backtest, config
from .chain import Chain, artifact
from .engine import Engine
from .store import Store


def main() -> None:
    ap = argparse.ArgumentParser(prog="liqbot", description=__doc__)
    ap.add_argument("-c", "--config", default="config.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the bot (paper or live, per the config)")
    bt = sub.add_parser("backtest", help="replay historical liquidations with paper money")
    bt.add_argument("--from-block", type=int, required=True)
    bt.add_argument("--to-block", type=int, default=0, help="default: latest")
    mon = sub.add_parser("monitor", help="serve the read-only dashboard")
    mon.add_argument("--host", default="127.0.0.1")
    mon.add_argument("--port", type=int, default=8787)
    sub.add_parser("deploy", help="deploy the FlashLiquidator contract (needs LIQBOT_PRIVATE_KEY)")
    args = ap.parse_args()
    cfg = config.load(args.config)

    if args.cmd == "monitor":
        from .monitor import serve

        server = serve(cfg.db_path, args.host, args.port)
        print(f"monitor on http://{args.host}:{args.port}  (database: {cfg.db_path})")
        server.serve_forever()
    elif args.cmd == "run":
        Engine(cfg, Chain(cfg), Store(cfg.db_path)).run_forever()
    elif args.cmd == "backtest":
        chain = Chain(cfg, force_virtual=True)
        to_block = args.to_block or chain.w3.eth.block_number
        print(backtest.report(backtest.run(cfg, chain, Store(cfg.db_path), args.from_block, to_block), cfg))
    elif args.cmd == "deploy":
        if not cfg.private_key:
            raise SystemExit("set LIQBOT_PRIVATE_KEY in your environment first")
        cfg.mode, cfg.liquidator = "paper", ""
        chain = Chain(cfg)
        art = artifact("FlashLiquidator")
        w3 = chain.w3
        tx = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]).constructor().build_transaction({
            "from": chain.owner, "nonce": w3.eth.get_transaction_count(chain.owner),
            "gasPrice": chain.gas_price(), "chainId": chain.chain_id,
        })
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(chain.account.sign_transaction(tx).raw_transaction), timeout=180
        )
        print(f"FlashLiquidator deployed at {receipt['contractAddress']}  (owner {chain.owner})")
        print("Put that address in config.toml under [bot] liquidator = ...")


if __name__ == "__main__":
    main()
