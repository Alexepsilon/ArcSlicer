// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./Interfaces.sol";

// Test-only contracts: a tiny fake lending protocol, DEX router and multicall,
// just faithful enough to exercise the liquidator and the bot end to end.

contract MockERC20 {
    string public symbol;
    uint8 public decimals;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory s, uint8 d) {
        symbol = s;
        decimals = d;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

interface IFlashReceiver {
    function executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes calldata data)
        external
        returns (bool);
}

/// Pool + addresses provider + data provider + oracle in one contract.
/// Each user has at most one collateral asset and one debt asset.
contract MockProtocol {
    event Borrow(
        address indexed reserve,
        address user,
        address indexed onBehalfOf,
        uint256 amount,
        uint8 interestRateMode,
        uint256 borrowRate,
        uint16 indexed referralCode
    );
    event LiquidationCall(
        address indexed collateralAsset,
        address indexed debtAsset,
        address indexed user,
        uint256 debtToCover,
        uint256 liquidatedCollateralAmount,
        address liquidator,
        bool receiveAToken
    );

    struct Position {
        address collateral;
        address debt;
        uint256 collateralAmount;
        uint256 debtAmount;
    }

    uint256 public constant BASE_CURRENCY_UNIT = 1e8;
    uint256 public constant LIQ_THRESHOLD = 8000;
    uint256 public constant LIQ_BONUS = 10500;
    uint256 public constant FLASH_PREMIUM_BPS = 5;

    address[] internal _reserves;
    mapping(address => uint256) public getAssetPrice;
    mapping(address => Position) public positions;

    function getPool() external view returns (address) {
        return address(this);
    }

    function getPriceOracle() external view returns (address) {
        return address(this);
    }

    function getPoolDataProvider() external view returns (address) {
        return address(this);
    }

    function addReserve(address asset, uint256 price) external {
        _reserves.push(asset);
        getAssetPrice[asset] = price;
    }

    function setPrice(address asset, uint256 price) external {
        getAssetPrice[asset] = price;
    }

    function setPosition(address user, address collateral, address debt, uint256 collAmount, uint256 debtAmount)
        external
    {
        positions[user] = Position(collateral, debt, collAmount, debtAmount);
        emit Borrow(debt, user, user, debtAmount, 2, 0, 0);
    }

    function getReservesList() external view returns (address[] memory) {
        return _reserves;
    }

    function getAssetsPrices(address[] calldata assets) external view returns (uint256[] memory out) {
        out = new uint256[](assets.length);
        for (uint256 i = 0; i < assets.length; i++) out[i] = getAssetPrice[assets[i]];
    }

    function _value(address asset, uint256 amount) internal view returns (uint256) {
        if (asset == address(0)) return 0;
        return (amount * getAssetPrice[asset]) / (10 ** MockERC20(asset).decimals());
    }

    function getUserAccountData(address user)
        public
        view
        returns (uint256 coll, uint256 debt, uint256, uint256, uint256, uint256 hf)
    {
        Position memory p = positions[user];
        coll = _value(p.collateral, p.collateralAmount);
        debt = _value(p.debt, p.debtAmount);
        hf = debt == 0 ? type(uint256).max : (coll * LIQ_THRESHOLD * 1e18) / (debt * 10000);
        return (coll, debt, 0, LIQ_THRESHOLD, 7500, hf);
    }

    function getUserReserveData(address asset, address user)
        external
        view
        returns (uint256, uint256, uint256, uint256, uint256, uint256, uint256, uint40, bool)
    {
        Position memory p = positions[user];
        uint256 aBal = asset == p.collateral ? p.collateralAmount : 0;
        uint256 vDebt = asset == p.debt ? p.debtAmount : 0;
        return (aBal, 0, vDebt, 0, 0, 0, 0, 0, aBal > 0);
    }

    function getReserveConfigurationData(address asset)
        external
        view
        returns (uint256, uint256, uint256, uint256, uint256, bool, bool, bool, bool, bool)
    {
        return (MockERC20(asset).decimals(), 7500, LIQ_THRESHOLD, LIQ_BONUS, 1000, true, true, false, true, false);
    }

    function flashLoanSimple(address receiver, address asset, uint256 amount, bytes calldata params, uint16)
        external
    {
        uint256 premium = (amount * FLASH_PREMIUM_BPS) / 10000;
        MockERC20(asset).transfer(receiver, amount);
        require(IFlashReceiver(receiver).executeOperation(asset, amount, premium, msg.sender, params), "callback");
        MockERC20(asset).transferFrom(receiver, address(this), amount + premium);
    }

    function liquidationCall(address collateral, address debt, address user, uint256 debtToCover, bool) external {
        Position storage p = positions[user];
        (,,,,, uint256 hf) = getUserAccountData(user);
        require(hf < 1e18, "HEALTHY");
        require(collateral == p.collateral && debt == p.debt, "ASSETS");

        uint256 maxCover = hf > 0.95e18 ? p.debtAmount / 2 : p.debtAmount;
        uint256 cover = debtToCover > maxCover ? maxCover : debtToCover;
        uint256 seized = (_value(debt, cover) * LIQ_BONUS * (10 ** MockERC20(collateral).decimals()))
            / (getAssetPrice[collateral] * 10000);
        if (seized > p.collateralAmount) {
            seized = p.collateralAmount;
            cover = (_value(collateral, seized) * 10000 * (10 ** MockERC20(debt).decimals()))
                / (getAssetPrice[debt] * LIQ_BONUS);
        }

        p.debtAmount -= cover;
        p.collateralAmount -= seized;
        MockERC20(debt).transferFrom(msg.sender, address(this), cover);
        MockERC20(collateral).mint(msg.sender, seized);
        emit LiquidationCall(collateral, debt, user, cover, seized, msg.sender, false);
    }
}

/// Swaps at the oracle price minus a configurable slippage. Speaks all three router dialects.
contract MockRouter {
    MockProtocol public oracle;
    uint256 public slippageBps;

    constructor(MockProtocol o) {
        oracle = o;
    }

    function setSlippage(uint256 bps) external {
        slippageBps = bps;
    }

    function _swap(address tokenIn, address tokenOut, uint256 amountIn, address to) internal returns (uint256 out) {
        MockERC20(tokenIn).transferFrom(msg.sender, address(this), amountIn);
        uint256 value = (amountIn * oracle.getAssetPrice(tokenIn)) / (10 ** MockERC20(tokenIn).decimals());
        out = (value * (10 ** MockERC20(tokenOut).decimals())) / oracle.getAssetPrice(tokenOut);
        out = (out * (10000 - slippageBps)) / 10000;
        MockERC20(tokenOut).mint(to, out);
    }

    function _ends(bytes memory path) internal pure returns (address tokenIn, address tokenOut) {
        uint256 len = path.length;
        assembly {
            tokenIn := shr(96, mload(add(path, 32)))
            tokenOut := shr(96, mload(add(add(path, 32), sub(len, 20))))
        }
    }

    function swapExactTokensForTokens(uint256 amountIn, uint256, address[] calldata path, address to, uint256)
        external
        returns (uint256[] memory amounts)
    {
        amounts = new uint256[](2);
        amounts[0] = amountIn;
        amounts[1] = _swap(path[0], path[path.length - 1], amountIn, to);
    }

    function exactInput(IV3Router.ExactInputParams calldata p) external payable returns (uint256) {
        (address a, address b) = _ends(p.path);
        return _swap(a, b, p.amountIn, p.recipient);
    }

    function exactInput(IV3Router02.ExactInputParams calldata p) external payable returns (uint256) {
        (address a, address b) = _ends(p.path);
        return _swap(a, b, p.amountIn, p.recipient);
    }
}

contract MockMulticall3 {
    function aggregate3(IMulticall3.Call3[] calldata calls)
        external
        payable
        returns (IMulticall3.Result[] memory results)
    {
        results = new IMulticall3.Result[](calls.length);
        for (uint256 i = 0; i < calls.length; i++) {
            (bool ok, bytes memory ret) = calls[i].target.call(calls[i].callData);
            require(ok || calls[i].allowFailure, "multicall");
            results[i] = IMulticall3.Result(ok, ret);
        }
    }
}
