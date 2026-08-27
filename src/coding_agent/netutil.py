"""Shared network validation helpers.

Loopback checks are used by both the profile URL validator and the web
security middleware. They must be exact: an IPv4/IPv6 literal is resolved
with :mod:`ipaddress` (no string-prefix shortcuts that accept names like
``127.0.0.1.evil.com``) and ``localhost`` is matched case-insensitively.
"""

from __future__ import annotations

import ipaddress
from typing import Optional


def is_loopback_host(hostname: Optional[str]) -> bool:
    if not hostname:
        return False
    value = hostname.strip().strip("[]").strip()
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False
