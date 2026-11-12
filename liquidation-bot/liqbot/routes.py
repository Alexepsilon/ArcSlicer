"""Builds the swap routes (collateral -> debt asset) the bot will try."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from eth_abi import encode

from .config import SWAP_KINDS, Dex


@dataclass(frozen=True)
class Route:
    kind: int
    router: str
    path: bytes
    label: str


def _addr(a: str) -> bytes:
    return bytes.fromhex(a[2:])


def v3_path(tokens: list[str], fees: list[int]) -> bytes:
    out = _addr(tokens[0])
    for token, fee in zip(tokens[1:], fees):
        out += fee.to_bytes(3, "big") + _addr(token)
    return out


def build_routes(dexes: list[Dex], token_in: str, token_out: str, hubs: list[str]) -> list[Route]:
    """Direct routes plus one-hop routes through each hub token (wrapped native etc.)."""
    if token_in.lower() == token_out.lower():
        # No swap needed; the contract ignores the route.
        return [Route(0, "0x" + "00" * 20, b"", "no swap")]
    mids = [h for h in dict.fromkeys(hubs) if h.lower() not in (token_in.lower(), token_out.lower())]
    routes: list[Route] = []
    for dex in dexes:
        kind = SWAP_KINDS[dex.kind]
        if dex.kind == "v2":
            for hops in [[token_in, token_out]] + [[token_in, m, token_out] for m in mids]:
                label = f"v2 {len(hops) - 1}-hop"
                routes.append(Route(kind, dex.router, encode(["address[]"], [hops]), label))
        else:
            for fee in dex.fees:
                routes.append(Route(kind, dex.router, v3_path([token_in, token_out], [fee]), f"{dex.kind} {fee}"))
            for m, (f1, f2) in product(mids, product(dex.fees, repeat=2)):
                path = v3_path([token_in, m, token_out], [f1, f2])
                routes.append(Route(kind, dex.router, path, f"{dex.kind} {f1}/{f2} via hub"))
    return routes
