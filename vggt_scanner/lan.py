"""Private LAN pairing; no external service or forwarded headers are used."""
import hmac
import ipaddress
import secrets
import socket

ACCESS_KEY = secrets.token_urlsafe(24)
COOKIE = "vggt_pairing"


def client_kind(host):
    try:
        address = ipaddress.ip_address((host or "").split("%")[0])
        if address.is_loopback:
            return "local"
        networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
        if any(address in ipaddress.ip_network(n) for n in networks):
            return "lan"
    except ValueError:
        pass
    return "outside"


def key_matches(value):
    return isinstance(value, str) and hmac.compare_digest(value.encode(), ACCESS_KEY.encode())


def lan_addresses():
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # Select the route; no packet is sent.
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found.extend(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return [ip for ip in dict.fromkeys(found) if client_kind(ip) == "lan"]


def allowed_host(host):
    return host in {"localhost", "127.0.0.1", "::1", socket.gethostname().lower(), *lan_addresses()}
