"""LAN exposure controls: private-network clients only, plus a key generated at each launch.

The laptop itself (loopback) needs no key. A phone gets the key once from the QR code / link shown on
the laptop; it is then kept in an HttpOnly cookie, so it never has to stay in the address bar.
"""
import hmac
import ipaddress
import json
import secrets
import socket

from .config import project_path

ACCESS_KEY = secrets.token_urlsafe(18)
COOKIE = "scanner_key"


def client_kind(host: str | None) -> str:
    """'local' for this computer, 'lan' for a private-network device, 'outside' for anything else."""
    try:
        address = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return "outside"
    if address.is_loopback:
        return "local"
    if address.is_private and not address.is_link_local:
        return "lan"
    return "outside"


def key_matches(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value.encode(), ACCESS_KEY.encode())


def lan_addresses() -> list[str]:
    """Private IPv4 addresses of this computer, the one used for the default route first."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # UDP connect picks a route without sending anything.
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    unique = list(dict.fromkeys(found))
    return [address for address in unique if client_kind(address) == "lan"]


def certificate_addresses(settings) -> list[str] | None:
    """Addresses recorded when scripts/make_cert.py created the certificate (None if unknown)."""
    try:
        return json.loads(project_path(settings.tls_cert).with_suffix(".json").read_text(encoding="utf-8"))["addresses"]
    except (OSError, ValueError, KeyError):
        return None
