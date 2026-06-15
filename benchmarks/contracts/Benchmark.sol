// SPDX-License-Identifier: MIT
pragma solidity 0.8.23; // Mantle-recommended compiler (v0.8.23 or below)

// Benchmark fixtures for rule MNT001 (.transfer() / .send() 2300-gas stipend).
//
// The "Before" vault pays out native MNT with `.transfer()`, forwarding the
// fixed 2300-gas stipend. The "After" vault uses `call{value:}` with an explicit
// success check (the Mantle-safe pattern). Two recipients model the two cases:
// a minimal recipient that fits in the stipend, and a "greedy" recipient whose
// receive() does real work (an SSTORE) and therefore needs more than 2300 gas.

/// Accepts MNT with negligible work (fits well under the 2300-gas stipend).
contract MinimalReceiver {
    receive() external payable {}
}

/// receive() performs an SSTORE (>2300 gas), modelling a contract wallet / proxy
/// / accounting hook on the receiving end. A 2300-gas stipend is insufficient,
/// so a `.transfer()`/`.send()` to this contract reverts.
contract GreedyReceiver {
    uint256 public pings;

    receive() external payable {
        pings += 1;
    }
}

/// L1-style payout: uses `.transfer()` (fixed 2300-gas stipend).
contract VaultBefore {
    function fund() external payable {}

    function payout(address to) external {
        payable(to).transfer(address(this).balance);
    }

    receive() external payable {}
}

/// Mantle-safe payout: uses `call{value:}` and checks success.
contract VaultAfter {
    function fund() external payable {}

    function payout(address to) external {
        (bool ok, ) = payable(to).call{value: address(this).balance}("");
        require(ok, "native transfer failed");
    }

    receive() external payable {}
}
