// Example contracts mirrored from examples/*.sol so the page works offline.
// (Kept identical to the canonical fixtures; parity.mjs tests against those.)

export const VULNERABLE = `// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice A staking/rewards contract written for Ethereum mainnet.
/// Deploying it unchanged on Mantle introduces several silent bugs — run
/// \`mantle-migrate-lint\` against it to see them.
contract VulnerableStaking {
    // L1 assumption: ~12s blocks. On Mantle this constant is meaningless.
    uint256 public constant BLOCKS_PER_DAY = 7200;

    // Hardcoded WETH mainnet address — does not hold WETH on Mantle.
    address public constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;

    mapping(address => uint256) public stakeBlock;
    mapping(address => uint256) public balance;

    function stake() external payable {
        balance[msg.sender] += msg.value;     // native value is MNT on Mantle, not ETH
        stakeBlock[msg.sender] = block.number;
    }

    // Reward math keyed off block height — wrong cadence on Mantle.
    function pendingReward(address user) public view returns (uint256) {
        uint256 blocksStaked = block.number - stakeBlock[user];
        return (blocksStaked * 1e18) / BLOCKS_PER_DAY;
    }

    // Deadline expressed in blocks assuming 12s/block.
    function deadlineFor(uint256 daysFromNow) external view returns (uint256) {
        return block.number + (daysFromNow * BLOCKS_PER_DAY);
    }

    function withdraw(uint256 amount) external {
        require(balance[msg.sender] >= amount, "insufficient");
        balance[msg.sender] -= amount;
        // 2300-gas stipend transfer — risky under Mantle gas scaling.
        payable(msg.sender).transfer(amount);
    }

    // Insecure randomness from block values — even weaker on an L2 sequencer.
    function drawWinner(uint256 seed) external view returns (uint256) {
        return uint256(keccak256(abi.encodePacked(block.prevrandao, blockhash(block.number - 1), seed)));
    }

    // Hardcoded gas + mainnet-only branch + tx.origin auth + ETH-balance assumption.
    function adminSweep(address target) external {
        require(tx.origin == msg.sender, "no contracts");
        require(block.chainid == 1, "mainnet only");
        uint256 fee = tx.gasprice * 21000;
        (bool ok, ) = target.call{gas: 2300}(abi.encodeWithSignature("ping()"));
        require(ok, "ping failed");
        uint256 bal = address(this).balance - fee;
        payable(target).transfer(bal);
    }
}
`;

export const CLEAN = `// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice The same staking logic written to be Mantle-safe: time comes from
/// block.timestamp, value transfers use call{value:} with checks, and no L1
/// constants or mainnet-only branches are baked in.
contract CleanStaking {
    mapping(address => uint256) public stakeTime;
    mapping(address => uint256) public balance;

    error InsufficientBalance();
    error TransferFailed();

    function stake() external payable {
        balance[msg.sender] += msg.value;
        stakeTime[msg.sender] = block.timestamp; // time, not block height
    }

    function pendingReward(address user) public view returns (uint256) {
        uint256 secondsStaked = block.timestamp - stakeTime[user];
        return (secondsStaked * 1e18) / 1 days;
    }

    function withdraw(uint256 amount) external {
        if (balance[msg.sender] < amount) revert InsufficientBalance();
        balance[msg.sender] -= amount;
        (bool ok, ) = payable(msg.sender).call{value: amount}("");
        if (!ok) revert TransferFailed();
    }
}
`;
