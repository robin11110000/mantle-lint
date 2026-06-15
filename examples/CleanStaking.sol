// SPDX-License-Identifier: MIT
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
