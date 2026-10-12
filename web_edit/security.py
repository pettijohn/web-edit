"""Exact host matching for requests to the unauthenticated file server."""

import ipaddress
import re

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
UNSPECIFIED_HOSTS = {"0.0.0.0", "::"}
HOST_HEADER = re.compile(r"(?:\[([0-9a-fA-F:.]+)\]|([a-zA-Z0-9.-]+))(?::([0-9]{1,5}))?")
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def normalize_host(value):
    """Accept an IP or exact DNS name, without a scheme, port, or wildcard."""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        name = value.encode("idna").decode("ascii").lower().removesuffix(".")
    except UnicodeError:
        name = ""
    if (
        not name
        or len(name) > 253
        or not all(DNS_LABEL.fullmatch(label) for label in name.split("."))
    ):
        raise ValueError(
            "Use an IP address or exact hostname, without a scheme, port, path, or wildcard."
        )
    return name


def allowed_hosts_for(host, extra_hosts=()):
    hosts = LOCAL_HOSTS | {normalize_host(name) for name in extra_hosts}
    bound_host = normalize_host(host)
    # Listening on every interface must not mean trusting every Host header.
    if bound_host not in UNSPECIFIED_HOSTS:
        hosts.add(bound_host)
    return frozenset(hosts)


def is_allowed_host(headers, allowed_hosts):
    values = [value for name, value in headers if name.lower() == b"host"]
    if len(values) != 1:
        return False
    try:
        match = HOST_HEADER.fullmatch(values[0].decode("ascii"))
        if match is None:
            return False
        ipv6, hostname, port = match.groups()
        if port is not None and not 1 <= int(port) <= 65535:
            return False
        host = str(ipaddress.IPv6Address(ipv6)) if ipv6 else normalize_host(hostname)
    except (ValueError, UnicodeError):
        return False
    return host in allowed_hosts
