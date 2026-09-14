// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable2Step, Ownable} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

/// Minimal Aave V3 interfaces (aave-v3-origin IPool / IFlashLoanSimpleReceiver).
interface IPool {
    function flashLoanSimple(address receiverAddress, address asset, uint256 amount, bytes calldata params, uint16 referralCode) external;
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);
}

/// Uniswap V3 SwapRouter02-compatible single-hop swap (no deadline in struct; deadline enforced here).
interface IV3SwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

/// Uniswap V2-compatible router.
interface IV2Router {
    function swapExactTokensForTokens(uint256 amountIn, uint256 amountOutMin, address[] calldata path, address to, uint256 deadline) external returns (uint256[] memory amounts);
}

/**
 * @title FlashLoanArbitrage
 * @notice Owner-only, allowlist-restricted, atomic two-swap arbitrage funded by an Aave V3 simple flash loan.
 *
 * Route: asset --(legA on routerA)--> intermediate --(legB on routerB)--> asset, then repay amount + premium.
 * The transaction REVERTS (and the flash loan is unwound) if:
 *  - either swap returns less than its minimum output,
 *  - the final balance cannot cover amount + premium + minProfit,
 *  - the deadline has passed,
 *  - a router/token is not allowlisted, or the caller is not the owner.
 *
 * Deliberately NO generic `call(target, data)` primitive: the contract only knows the two router ABIs above.
 * A reverted transaction still costs gas - the off-chain Profit Guard accounts for that.
 */
contract FlashLoanArbitrage is Ownable2Step, ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    enum RouterKind { UniswapV3, UniswapV2 }

    struct SwapLeg {
        address router;
        RouterKind kind;
        uint24 fee;            // V3 pool fee tier (ignored for V2)
        uint256 minAmountOut;  // enforced on-chain
    }

    struct ArbParams {
        address intermediate;  // token bought in leg A and sold in leg B
        SwapLeg legA;
        SwapLeg legB;
        uint256 minProfit;     // in loan asset units, net of premium
        uint256 deadline;      // unix timestamp
    }

    IPool public immutable POOL;
    mapping(address => bool) public allowedRouters;
    mapping(address => bool) public allowedTokens;
    address private _executingAsset; // transient guard so only our own flash loan can call back

    event RouterAllowed(address indexed router, bool allowed);
    event TokenAllowed(address indexed token, bool allowed);
    event ArbitrageExecuted(address indexed asset, uint256 amount, uint256 premium, uint256 profit);

    error NotPool();
    error BadInitiator();
    error RouterNotAllowed(address router);
    error TokenNotAllowed(address token);
    error DeadlinePassed();
    error InsufficientProfit(uint256 have, uint256 need);
    error ZeroAmount();

    constructor(address pool, address initialOwner) Ownable(initialOwner) {
        POOL = IPool(pool);
    }

    // ------------------------------------------------------------------ admin
    function setRouter(address router, bool allowed) external onlyOwner {
        allowedRouters[router] = allowed;
        emit RouterAllowed(router, allowed);
    }

    function setToken(address token, bool allowed) external onlyOwner {
        allowedTokens[token] = allowed;
        emit TokenAllowed(token, allowed);
    }

    function pause() external onlyOwner { _pause(); }
    function unpause() external onlyOwner { _unpause(); }

    /// @notice Withdraw accumulated profit or rescue stuck tokens (owner only).
    function rescueERC20(address token, address to, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(to, amount);
    }

    // ------------------------------------------------------------------ entry
    /// @notice Start the atomic arbitrage. Only the owner (the bot signer) may call.
    function executeArbitrage(address asset, uint256 amount, ArbParams calldata p) external onlyOwner nonReentrant whenNotPaused {
        if (amount == 0) revert ZeroAmount();
        if (block.timestamp > p.deadline) revert DeadlinePassed();
        if (!allowedTokens[asset]) revert TokenNotAllowed(asset);
        if (!allowedTokens[p.intermediate]) revert TokenNotAllowed(p.intermediate);
        if (!allowedRouters[p.legA.router]) revert RouterNotAllowed(p.legA.router);
        if (!allowedRouters[p.legB.router]) revert RouterNotAllowed(p.legB.router);
        _executingAsset = asset;
        POOL.flashLoanSimple(address(this), asset, amount, abi.encode(p), 0);
        _executingAsset = address(0);
    }

    /// @notice Aave V3 callback. Must repay amount + premium via approval to the Pool.
    function executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes calldata params) external returns (bool) {
        if (msg.sender != address(POOL)) revert NotPool();
        if (initiator != address(this) || asset != _executingAsset) revert BadInitiator();
        ArbParams memory p = abi.decode(params, (ArbParams));

        uint256 intermediateOut = _swap(p.legA, asset, p.intermediate, amount);
        uint256 assetBack = _swap(p.legB, p.intermediate, asset, intermediateOut);

        uint256 owed = amount + premium;
        uint256 balance = IERC20(asset).balanceOf(address(this));
        if (balance < owed + p.minProfit) revert InsufficientProfit(balance > owed ? balance - owed : 0, p.minProfit);
        // exact-amount approval for repayment (no infinite approvals)
        IERC20(asset).forceApprove(address(POOL), owed);
        emit ArbitrageExecuted(asset, amount, premium, balance - owed);
        assetBack; // silence unused warning; profit is measured from balance
        return true;
    }

    // ------------------------------------------------------------------ swaps
    function _swap(SwapLeg memory leg, address tokenIn, address tokenOut, uint256 amountIn) internal returns (uint256 amountOut) {
        IERC20(tokenIn).forceApprove(leg.router, amountIn);
        if (leg.kind == RouterKind.UniswapV3) {
            amountOut = IV3SwapRouter(leg.router).exactInputSingle(
                IV3SwapRouter.ExactInputSingleParams({
                    tokenIn: tokenIn,
                    tokenOut: tokenOut,
                    fee: leg.fee,
                    recipient: address(this),
                    amountIn: amountIn,
                    amountOutMinimum: leg.minAmountOut,
                    sqrtPriceLimitX96: 0
                })
            );
        } else {
            address[] memory path = new address[](2);
            path[0] = tokenIn;
            path[1] = tokenOut;
            uint256[] memory amounts = IV2Router(leg.router).swapExactTokensForTokens(amountIn, leg.minAmountOut, path, address(this), block.timestamp);
            amountOut = amounts[amounts.length - 1];
        }
        IERC20(tokenIn).forceApprove(leg.router, 0);
    }
}
