from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse


class NetworkAccessDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class NetworkPolicy:
    offline: bool
    allow_loopback_services: bool = False
    allowed_loopback_urls: tuple[str, ...] = ()

    def assert_url_allowed(self, url: str, *, purpose: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise NetworkAccessDenied(f"Unsupported URL for {purpose}.")
        if not self.offline:
            return
        if self.allow_loopback_services and _is_loopback(parsed.hostname):
            if not self.allowed_loopback_urls or url in self.allowed_loopback_urls:
                return
            raise NetworkAccessDenied(
                f"Offline mode permits only the selected local endpoint for {purpose}."
            )
        raise NetworkAccessDenied(
            f"Offline mode blocks outbound network access for {purpose}."
        )


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
