// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// Test doubles used by the py-evm test-suite (backend/tests/test_contract_evm.py) and Foundry tests.
contract MockToken {
    string public name = "Mock"; string public symbol = "MOCK"; uint8 public decimals = 18;
    mapping(address => uint256) public balanceOf; mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 a) external { balanceOf[to] += a; }
    function transfer(address to, uint256 a) external returns (bool) { balanceOf[msg.sender] -= a; balanceOf[to] += a; return true; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transferFrom(address f, address t, uint256 a) external returns (bool) { allowance[f][msg.sender] -= a; balanceOf[f] -= a; balanceOf[t] += a; return true; }
}

/// Aave V3-like pool: transfers the loan, calls executeOperation, pulls amount + premium via transferFrom.
contract MockPool {
    uint128 public premiumBps;
    constructor(uint128 _premiumBps) { premiumBps = _premiumBps; }
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128) { return premiumBps; }
    function flashLoanSimple(address receiver, address asset, uint256 amount, bytes calldata params, uint16) external {
        uint256 premium = amount * premiumBps / 10_000;
        require(MockToken(asset).transfer(receiver, amount), "pool: transfer");
        (bool ok, bytes memory ret) = receiver.call(abi.encodeWithSignature("executeOperation(address,uint256,uint256,address,bytes)", asset, amount, premium, receiver, params));
        if (!ok) { assembly { revert(add(ret, 32), mload(ret)) } }
        require(MockToken(asset).transferFrom(receiver, address(this), amount + premium), "pool: repay failed");
    }
}

/// Malicious pool: calls executeOperation with a foreign initiator.
contract EvilPool {
    function attack(address receiver, address asset, uint256 amount, bytes calldata params) external {
        (bool ok, bytes memory ret) = receiver.call(abi.encodeWithSignature("executeOperation(address,uint256,uint256,address,bytes)", asset, amount, 0, address(0xBEEF), params));
        if (!ok) { assembly { revert(add(ret, 32), mload(ret)) } }
    }
}

/// Uniswap V2-like router with a fixed conversion rate (out = in * rateBps / 10000).
contract MockV2Router {
    uint256 public rateBps;
    constructor(uint256 r) { rateBps = r; }
    function setRate(uint256 r) external { rateBps = r; }
    function swapExactTokensForTokens(uint256 amountIn, uint256 amountOutMin, address[] calldata path, address to, uint256) external returns (uint256[] memory amounts) {
        MockToken(path[0]).transferFrom(msg.sender, address(this), amountIn);
        uint256 out = amountIn * rateBps / 10_000;
        require(out >= amountOutMin, "slippage");
        MockToken(path[1]).mint(to, out);
        amounts = new uint256[](2); amounts[0] = amountIn; amounts[1] = out;
    }
}

/// Uniswap V3 SwapRouter02-like router with a fixed conversion rate.
contract MockV3Router {
    uint256 public rateBps;
    constructor(uint256 r) { rateBps = r; }
    struct ExactInputSingleParams { address tokenIn; address tokenOut; uint24 fee; address recipient; uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96; }
    function exactInputSingle(ExactInputSingleParams calldata p) external payable returns (uint256 amountOut) {
        MockToken(p.tokenIn).transferFrom(msg.sender, address(this), p.amountIn);
        amountOut = p.amountIn * rateBps / 10_000;
        require(amountOut >= p.amountOutMinimum, "Too little received");
        MockToken(p.tokenOut).mint(p.recipient, amountOut);
    }
}

/// Router that re-enters executeArbitrage during a swap (must be rejected by ReentrancyGuard / onlyOwner).
contract ReentrantRouter {
    address public target;
    bytes public payload;
    function arm(address t, bytes calldata p) external { target = t; payload = p; }
    function swapExactTokensForTokens(uint256 amountIn, uint256, address[] calldata path, address to, uint256) external returns (uint256[] memory amounts) {
        MockToken(path[0]).transferFrom(msg.sender, address(this), amountIn);
        (bool ok, ) = target.call(payload);
        require(!ok, "reentrancy succeeded");
        MockToken(path[1]).mint(to, amountIn);
        amounts = new uint256[](2); amounts[0] = amountIn; amounts[1] = amountIn;
    }
}
