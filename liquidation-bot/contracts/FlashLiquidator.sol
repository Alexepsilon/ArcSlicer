// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./Interfaces.sol";

/// @title FlashLiquidator
/// @notice Liquidates an underwater position on an Aave-v3-style pool using a flash loan,
///         swaps the seized collateral back to the debt asset and sends the profit to the owner.
///         Everything happens in one transaction: if it would not be profitable, it reverts
///         and the only cost is the gas of the failed transaction.
contract FlashLiquidator {
    // Slot 0. The paper-trading/backtest code relies on this slot when it injects the
    // contract with an eth_call state override, so do not reorder storage.
    address public owner;
    // Non-zero only while a liquidation is running; authenticates the flash-loan callback.
    address private _activeFlashPool;

    uint8 internal constant SWAP_V2 = 0; // UniswapV2-style router, path = abi.encode(address[])
    uint8 internal constant SWAP_V3 = 1; // UniswapV3 SwapRouter (with deadline), packed path
    uint8 internal constant SWAP_V3_02 = 2; // UniswapV3 SwapRouter02 (no deadline), packed path

    struct Params {
        address flashPool; // pool that lends the debt asset (usually the same as `pool`)
        address pool; // pool holding the position to liquidate
        address collateral;
        address debt;
        address user;
        uint256 debtToCover;
        uint8 swapKind;
        address router;
        bytes path;
        uint256 minProfit; // in debt-asset units
    }

    error NotOwner();
    error BadCallback();
    error InsufficientProfit(uint256 profit, uint256 minProfit);
    error TokenCallFailed();

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @return profit amount of debt asset gained, after repaying the flash loan and its fee
    function liquidate(Params calldata p) external onlyOwner returns (uint256 profit) {
        uint256 balanceBefore = IERC20(p.debt).balanceOf(address(this));

        _activeFlashPool = p.flashPool;
        IAavePool(p.flashPool).flashLoanSimple(address(this), p.debt, p.debtToCover, abi.encode(p), 0);
        _activeFlashPool = address(0);

        uint256 balanceAfter = IERC20(p.debt).balanceOf(address(this));
        if (balanceAfter < balanceBefore) revert InsufficientProfit(0, p.minProfit);
        profit = balanceAfter - balanceBefore;
        if (profit < p.minProfit) revert InsufficientProfit(profit, p.minProfit);

        if (balanceAfter > 0) _transfer(p.debt, owner, balanceAfter);
        if (p.collateral != p.debt) {
            uint256 dust = IERC20(p.collateral).balanceOf(address(this));
            if (dust > 0) _transfer(p.collateral, owner, dust);
        }
    }

    /// @notice Aave flash-loan callback.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata data
    ) external returns (bool) {
        if (msg.sender != _activeFlashPool || initiator != address(this)) revert BadCallback();
        Params memory p = abi.decode(data, (Params));

        _approve(asset, p.pool, amount);
        IAavePool(p.pool).liquidationCall(p.collateral, asset, p.user, amount, false);
        _approve(asset, p.pool, 0);

        if (p.collateral != asset) {
            uint256 seized = IERC20(p.collateral).balanceOf(address(this));
            _approve(p.collateral, p.router, seized);
            _swap(p, seized);
            _approve(p.collateral, p.router, 0);
        }

        // The pool pulls amount + premium after this returns; if the swap did not
        // produce enough, that pull reverts the whole transaction.
        _approve(asset, msg.sender, amount + premium);
        return true;
    }

    /// @notice Recover any token left on the contract.
    function rescue(address token) external onlyOwner {
        _transfer(token, owner, IERC20(token).balanceOf(address(this)));
    }

    // amountOutMinimum is 0 on purpose: the profit check in `liquidate` is the slippage guard.
    function _swap(Params memory p, uint256 amountIn) private {
        if (p.swapKind == SWAP_V2) {
            address[] memory path = abi.decode(p.path, (address[]));
            IV2Router(p.router).swapExactTokensForTokens(amountIn, 0, path, address(this), block.timestamp);
        } else if (p.swapKind == SWAP_V3) {
            IV3Router(p.router).exactInput(
                IV3Router.ExactInputParams(p.path, address(this), block.timestamp, amountIn, 0)
            );
        } else {
            IV3Router02(p.router).exactInput(IV3Router02.ExactInputParams(p.path, address(this), amountIn, 0));
        }
    }

    // Low-level token calls so tokens that return nothing (USDT-style) also work.
    function _approve(address token, address spender, uint256 amount) private {
        if (amount > 0) _call(token, abi.encodeWithSelector(IERC20.approve.selector, spender, 0));
        _call(token, abi.encodeWithSelector(IERC20.approve.selector, spender, amount));
    }

    function _transfer(address token, address to, uint256 amount) private {
        _call(token, abi.encodeWithSelector(IERC20.transfer.selector, to, amount));
    }

    function _call(address token, bytes memory data) private {
        (bool ok, bytes memory ret) = token.call(data);
        if (!ok || (ret.length > 0 && !abi.decode(ret, (bool)))) revert TokenCallFailed();
    }
}
