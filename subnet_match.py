"""
CIDR subnet matching using Python's built-in ipaddress module.
No string prefix tricks — uses actual bitmask comparison.
"""

import ipaddress
from typing import Optional


def ip_in_any_subnet(ip: str, subnets: list[str]) -> Optional[str]:
    """
    Return the first CIDR from `subnets` that contains `ip`, or None.

    Uses ipaddress.ip_network for correct prefix-length bitmask matching,
    so edge cases (broadcast address, network address, /32, /0) all work.
    """
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        return None

    for cidr in subnets:
        try:
            # strict=False allows host bits to be set in the network address
            network = ipaddress.ip_network(cidr, strict=False)
            if ip_obj in network:
                return cidr
        except ValueError:
            continue

    return None
