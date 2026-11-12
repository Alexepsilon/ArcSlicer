from liqbot.planner import WAD, Reserve, UserReserve, candidates
from liqbot.routes import build_routes, v3_path
from liqbot.config import Dex

A, B, C = "0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "cc" * 20
RESERVES = {A: Reserve(A, "WETH", 18, 10500), B: Reserve(B, "USDC", 6, 10450), C: Reserve(C, "DAI", 18, 10000)}
PRICES = {A: 1800 * 10**8, B: 10**8, C: 10**8}


def test_healthy_position_yields_nothing():
    pos = [UserReserve(A, 10**18, 0), UserReserve(B, 0, 1000 * 10**6)]
    assert candidates(pos, RESERVES, PRICES, WAD) == []


def test_slightly_underwater_offers_full_and_half():
    pos = [UserReserve(A, 10**18, 0), UserReserve(B, 0, 1500 * 10**6)]
    out = candidates(pos, RESERVES, PRICES, 96 * WAD // 100)
    assert [c.debt_to_cover for c in out] == [1500 * 10**6, 750 * 10**6]
    assert out[0].est_bonus_base == 75 * 10**8  # 5% of 1500


def test_deeply_underwater_offers_full_only():
    pos = [UserReserve(A, 10**18, 0), UserReserve(B, 0, 1500 * 10**6)]
    assert len(candidates(pos, RESERVES, PRICES, 90 * WAD // 100)) == 1


def test_cover_is_capped_by_available_collateral():
    # 0.1 WETH = 180 of collateral backs 1500 of debt: at most 180 / 1.05 can be repaid.
    pos = [UserReserve(A, 10**17, 0), UserReserve(B, 0, 1500 * 10**6)]
    out = candidates(pos, RESERVES, PRICES, 50 * WAD // 100)
    assert out[0].debt_to_cover == 180 * 10**6 * 10000 // 10500


def test_collateral_without_bonus_is_ignored():
    pos = [UserReserve(C, 5000 * 10**18, 0), UserReserve(B, 0, 4500 * 10**6)]
    assert candidates(pos, RESERVES, PRICES, 90 * WAD // 100) == []


def test_routes():
    dexes = [Dex("v3", "0x" + "01" * 20, [500, 3000]), Dex("v2", "0x" + "02" * 20)]
    routes = build_routes(dexes, A, B, hubs=[A, C])
    # v3: 2 direct + 4 via hub C (hub A is the input token, so skipped); v2: direct + via C
    assert len(routes) == 8
    assert v3_path([A, B], [500]) == bytes.fromhex("aa" * 20 + "0001f4" + "bb" * 20)
    assert build_routes(dexes, A, A, hubs=[])[0].label == "no swap"
