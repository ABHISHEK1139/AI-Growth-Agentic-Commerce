"""URL safety and strict anti-SSRF policy enforcement."""

from __future__ import annotations

import ipaddress
import urllib.parse

_BLOCKED_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"})  # noqa: S104


def is_safe_public_url(url: str) -> bool:
    """Validate that a URL uses http(s) and does not target loopback, link-local, private, or metadata IPs."""
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        lower_host = hostname.lower()
        if lower_host in _BLOCKED_HOSTNAMES:
            return False

        # Reject local domains ending with .local, .internal, .localhost
        if any(
            lower_host.endswith(suffix) for suffix in (".local", ".internal", ".localhost", ".lan")
        ):
            return False

        # Check integer/hex/octal IP representation (e.g. 2130706433, 0x7f000001)
        try:
            int_val = int(hostname, 0)
            ip = ipaddress.ip_address(int_val)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
            ):
                return False
        except (ValueError, OverflowError):
            pass

        try:
            ip = ipaddress.ip_address(hostname)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
            ):
                return False
        except ValueError:
            # Hostname is a standard domain name, not an IP literal
            pass

        return True
    except Exception:
        return False
