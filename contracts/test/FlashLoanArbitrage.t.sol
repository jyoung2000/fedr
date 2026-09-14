// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {FlashLoanArbitrage, IPool} from "../src/FlashLoanArbitrage.sol";

/// Mock ERC20 + mock Aave pool + mock V2 router to test the revert paths without a fork.
contract MockToken {
    string public name = "Mock"; string public symbol = "MOCK"; uint8 public decimals = 18;
    mapping(address => uint256) public balanceOf; mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 a) external { balanceOf[to] += a; }
    function transfer(address to, uint256 a) external returns (bool) { balanceOf[msg.sender] -= a; balanceOf[to] += a; return true; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transferFrom(address f, address t, uint256 a) external returns (bool) { allowance[f][msg.sender] -= a; balanceOf[f] -= a; balanceOf[t] += a; return true; }
}

contract MockPool is IPool {
    uint128 public constant PREMIUM_BPS = 5;
    function FLASHLOAN_PREMIUM_TOTAL() external pure returns (uint128) { return PREMIUM_BPS; }
    function flashLoanSimple(address receiver, address asset, uint256 amount, bytes calldata params, uint16) external {
        uint256 premium = amount * PREMIUM_BPS / 10_000;
        MockToken(asset).transfer(receiver, amount);
        (bool ok, bytes memory ret) = receiver.call(abi.encodeWithSignature("executeOperation(address,uint256,uint256,address,bytes)", asset, amount, premium, receiver, params));
        if (!ok) { assembly { revert(add(ret, 32), mload(ret)) } }
        require(MockToken(asset).transferFrom(receiver, address(this), amount + premium), "repay failed");
    }
}

contract MockV2Router {
    uint256 public rateBps; // out = in * rateBps / 10000
    constructor(uint256 r) { rateBps = r; }
    function swapExactTokensForTokens(uint256 amountIn, uint256 amountOutMin, address[] calldata path, address to, uint256) external returns (uint256[] memory amounts) {
        MockToken(path[0]).transferFrom(msg.sender, address(this), amountIn);
        uint256 out = amountIn * rateBps / 10_000;
        require(out >= amountOutMin, "slippage");
        MockToken(path[1]).mint(to, out);
        amounts = new uint256[](2); amounts[0] = amountIn; amounts[1] = out;
    }
}

contract FlashLoanArbitrageTest is Test {
    MockToken usdc; MockToken weth; MockPool pool; MockV2Router a; MockV2Router b; FlashLoanArbitrage arb;

    function setUp() public {
        usdc = new MockToken(); weth = new MockToken(); pool = new MockPool();
        usdc.mint(address(pool), 1_000_000e18);
        a = new MockV2Router(10_100); // +1%
        b = new MockV2Router(10_100); // +1%
        arb = new FlashLoanArbitrage(address(pool), address(this));
        arb.setRouter(address(a), true); arb.setRouter(address(b), true);
        arb.setToken(address(usdc), true); arb.setToken(address(weth), true);
    }

    function _params(uint256 minProfit, uint256 deadline) internal view returns (FlashLoanArbitrage.ArbParams memory p) {
        p.intermediate = address(weth);
        p.legA = FlashLoanArbitrage.SwapLeg(address(a), FlashLoanArbitrage.RouterKind.UniswapV2, 0, 0);
        p.legB = FlashLoanArbitrage.SwapLeg(address(b), FlashLoanArbitrage.RouterKind.UniswapV2, 0, 0);
        p.minProfit = minProfit; p.deadline = deadline;
    }

    function testProfitableRouteExecutes() public {
        arb.executeArbitrage(address(usdc), 10_000e18, _params(100e18, block.timestamp + 60));
        assertGt(usdc.balanceOf(address(arb)), 190e18); // ~2.01% - 0.05% premium
    }

    function testRevertsWhenMinProfitNotMet() public {
        vm.expectRevert();
        arb.executeArbitrage(address(usdc), 10_000e18, _params(500e18, block.timestamp + 60));
    }

    function testRevertsWhenCannotRepay() public {
        MockV2Router bad = new MockV2Router(9_000); // -10%
        arb.setRouter(address(bad), true);
        FlashLoanArbitrage.ArbParams memory p = _params(0, block.timestamp + 60);
        p.legB.router = address(bad);
        vm.expectRevert();
        arb.executeArbitrage(address(usdc), 10_000e18, p);
    }

    function testRevertsOnDeadline() public {
        vm.expectRevert(FlashLoanArbitrage.DeadlinePassed.selector);
        arb.executeArbitrage(address(usdc), 10_000e18, _params(0, block.timestamp - 1));
    }

    function testRevertsForNonOwner() public {
        vm.prank(address(0xBEEF));
        vm.expectRevert();
        arb.executeArbitrage(address(usdc), 10_000e18, _params(0, block.timestamp + 60));
    }

    function testRevertsForUnlistedRouter() public {
        FlashLoanArbitrage.ArbParams memory p = _params(0, block.timestamp + 60);
        p.legA.router = address(0xDEAD);
        vm.expectRevert(abi.encodeWithSelector(FlashLoanArbitrage.RouterNotAllowed.selector, address(0xDEAD)));
        arb.executeArbitrage(address(usdc), 10_000e18, p);
    }

    function testCallbackOnlyFromPool() public {
        vm.expectRevert(FlashLoanArbitrage.NotPool.selector);
        arb.executeOperation(address(usdc), 1, 0, address(arb), "");
    }
}
