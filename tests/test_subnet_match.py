"""
Unit tests for subnet_match.ip_in_any_subnet.

Covers: interior IPs, network/broadcast boundaries, IPs just outside,
/32 single-host, /0 everything, multiple subnets, and bad input.
"""

import sys
import os

# Allow running from any directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from subnet_match import ip_in_any_subnet


# ---------------------------------------------------------------------------
# Basic single-subnet matching
# ---------------------------------------------------------------------------

class TestSingleSubnet:
    SUBNET = "5.45.192.0/18"  # 5.45.192.0 – 5.45.255.255

    def test_first_address_in_subnet(self):
        # Network address itself is inside the subnet
        assert ip_in_any_subnet("5.45.192.0", [self.SUBNET]) == self.SUBNET

    def test_last_address_in_subnet(self):
        # Broadcast address is inside the subnet
        assert ip_in_any_subnet("5.45.255.255", [self.SUBNET]) == self.SUBNET

    def test_interior_address(self):
        assert ip_in_any_subnet("5.45.200.42", [self.SUBNET]) == self.SUBNET

    def test_one_below_network_address(self):
        # 5.45.191.255 is just below 5.45.192.0
        assert ip_in_any_subnet("5.45.191.255", [self.SUBNET]) is None

    def test_one_above_broadcast_address(self):
        # 5.46.0.0 is just above 5.45.255.255
        assert ip_in_any_subnet("5.46.0.0", [self.SUBNET]) is None

    def test_completely_different_subnet(self):
        assert ip_in_any_subnet("8.8.8.8", [self.SUBNET]) is None


# ---------------------------------------------------------------------------
# Multiple subnets — returns the FIRST match
# ---------------------------------------------------------------------------

YANDEX_SUBNETS = [
    "5.45.192.0/18",
    "77.88.0.0/18",
    "213.180.192.0/19",
]

class TestMultipleSubnets:
    def test_matches_first_subnet(self):
        assert ip_in_any_subnet("5.45.200.1", YANDEX_SUBNETS) == "5.45.192.0/18"

    def test_matches_second_subnet(self):
        assert ip_in_any_subnet("77.88.0.1", YANDEX_SUBNETS) == "77.88.0.0/18"

    def test_matches_third_subnet(self):
        assert ip_in_any_subnet("213.180.192.1", YANDEX_SUBNETS) == "213.180.192.0/19"

    def test_no_match_in_any_subnet(self):
        assert ip_in_any_subnet("1.1.1.1", YANDEX_SUBNETS) is None

    def test_boundary_of_second_subnet_lower(self):
        # 77.88.0.0 is the network address of the second subnet
        assert ip_in_any_subnet("77.88.0.0", YANDEX_SUBNETS) == "77.88.0.0/18"

    def test_boundary_of_second_subnet_upper(self):
        # 77.88.63.255 is broadcast of 77.88.0.0/18  (77.88.0.0 + 2^14 - 1)
        assert ip_in_any_subnet("77.88.63.255", YANDEX_SUBNETS) == "77.88.0.0/18"

    def test_just_above_third_subnet(self):
        # 213.180.192.0/19 → last addr = 213.180.223.255; +1 = 213.180.224.0
        assert ip_in_any_subnet("213.180.224.0", YANDEX_SUBNETS) is None

    def test_just_below_third_subnet(self):
        # 213.180.191.255 is one below network address
        assert ip_in_any_subnet("213.180.191.255", YANDEX_SUBNETS) is None


# ---------------------------------------------------------------------------
# Edge case subnets
# ---------------------------------------------------------------------------

class TestEdgeCaseSubnets:
    def test_slash_32_matches_exact_ip(self):
        assert ip_in_any_subnet("1.2.3.4", ["1.2.3.4/32"]) == "1.2.3.4/32"

    def test_slash_32_does_not_match_neighbor(self):
        assert ip_in_any_subnet("1.2.3.5", ["1.2.3.4/32"]) is None

    def test_slash_0_matches_any_ipv4(self):
        # 0.0.0.0/0 contains all IPv4 addresses
        assert ip_in_any_subnet("99.99.99.99", ["0.0.0.0/0"]) == "0.0.0.0/0"

    def test_slash_0_matches_broadcast(self):
        assert ip_in_any_subnet("255.255.255.255", ["0.0.0.0/0"]) == "0.0.0.0/0"

    def test_slash_0_matches_loopback(self):
        assert ip_in_any_subnet("127.0.0.1", ["0.0.0.0/0"]) == "0.0.0.0/0"


# ---------------------------------------------------------------------------
# Malformed / edge inputs
# ---------------------------------------------------------------------------

class TestBadInput:
    def test_invalid_ip_returns_none(self):
        assert ip_in_any_subnet("not-an-ip", ["5.45.192.0/18"]) is None

    def test_empty_ip_returns_none(self):
        assert ip_in_any_subnet("", ["5.45.192.0/18"]) is None

    def test_invalid_cidr_is_skipped(self):
        # Bad CIDR should be silently skipped; second entry should still match
        result = ip_in_any_subnet("5.45.200.1", ["not-a-cidr", "5.45.192.0/18"])
        assert result == "5.45.192.0/18"

    def test_empty_subnet_list_returns_none(self):
        assert ip_in_any_subnet("5.45.200.1", []) is None

    def test_ipv4_mapped_string(self):
        # Plain dotted-decimal — should work normally
        assert ip_in_any_subnet("77.88.0.100", ["77.88.0.0/18"]) == "77.88.0.0/18"


# ---------------------------------------------------------------------------
# Correct bitmask behaviour (not string-prefix matching)
# ---------------------------------------------------------------------------

class TestBitmaskCorrectness:
    def test_high_octet_boundary(self):
        # 192.168.0.0/16 should NOT match 192.169.0.1 even though "192.168" is
        # a prefix of "192.168" — verify we don't do string prefix matching
        assert ip_in_any_subnet("192.169.0.1", ["192.168.0.0/16"]) is None

    def test_prefix_16_upper_boundary(self):
        # 192.168.255.255 is broadcast of /16 — must match
        assert ip_in_any_subnet("192.168.255.255", ["192.168.0.0/16"]) == "192.168.0.0/16"

    def test_prefix_16_one_above_broadcast(self):
        # 192.169.0.0 is one network above — must NOT match
        assert ip_in_any_subnet("192.169.0.0", ["192.168.0.0/16"]) is None

    def test_non_octet_boundary_prefix(self):
        # /18 cuts inside the third octet; 5.45.128.0 is below 5.45.192.0
        assert ip_in_any_subnet("5.45.128.0", ["5.45.192.0/18"]) is None

    def test_non_octet_boundary_interior(self):
        # 5.45.193.0 is inside 5.45.192.0/18
        assert ip_in_any_subnet("5.45.193.0", ["5.45.192.0/18"]) == "5.45.192.0/18"
