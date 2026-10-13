"""Strict, offline validation of a private, single-code Portal invitation."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import ipaddress
import json
import re


_PREFIX = "DP1."
_MAX_CODE_LENGTH = 4096
_INVALID_CODE = "Invalid pairing code"
_PAIRING_FIELDS = (
    "ssh_host", "ssh_port", "ssh_user", "host_key", "project_ref", "device_ref", "portal_key",
)
_FIELDS = frozenset((*_PAIRING_FIELDS, "version"))
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_SSH_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_SAFE_REF = re.compile(r"[a-z][a-z0-9-]{2,95}\Z")
_PORTAL_KEY = re.compile(r"[A-Za-z0-9_-]{64}\Z")
_HOST_KEY = re.compile(r"ssh-ed25519 ([A-Za-z0-9+/]{68})\Z")
_CODE_PAYLOAD = re.compile(r"[A-Za-z0-9_-]+\Z")
_SSH_TYPE = b"ssh-ed25519"
_SSH_BLOB_PREFIX = len(_SSH_TYPE).to_bytes(4, "big") + _SSH_TYPE + (32).to_bytes(4, "big")


class PairingError(ValueError):
    """A constant, credential-free validation error."""


@dataclass(frozen=True)
class Pairing:
    ssh_host: str
    ssh_port: int
    ssh_user: str
    host_key: str
    project_ref: str
    device_ref: str
    portal_key: str = field(repr=False)


def _valid_host(host: object) -> bool:
    if not isinstance(host, str) or not 1 <= len(host) <= 253:
        return False
    labels = host.split(".")
    if not all(_DNS_LABEL.fullmatch(label) for label in labels):
        return False
    lower = host.lower()
    if lower in ("localhost", "localhost.localdomain") or lower.endswith((".localhost", ".localhost.localdomain")):
        return False
    # Reject noncanonical numeric-address spellings (including short, octal and
    # hexadecimal IPv4 forms), which some SSH resolvers interpret as loopback.
    numeric = [label.isascii() and label.isdecimal() for label in labels]
    hexadecimal = [bool(re.fullmatch(r"0[xX][0-9a-fA-F]+", label)) for label in labels]
    if all(decimal or hex_form for decimal, hex_form in zip(numeric, hexadecimal)):
        if not all(numeric):
            return False
        try:
            address = ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError:
            return False
        return not (address.is_loopback or address.is_unspecified or address.is_multicast)
    return True


def _valid_host_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = _HOST_KEY.fullmatch(value)
    if match is None:
        return False
    try:
        blob = base64.b64decode(match.group(1), validate=True)
    except binascii.Error:
        return False
    return (
        len(blob) == len(_SSH_BLOB_PREFIX) + 32
        and blob.startswith(_SSH_BLOB_PREFIX)
        and base64.b64encode(blob).decode("ascii") == match.group(1)
    )


def _validate(value: object) -> None:
    if not isinstance(value, Pairing) or not (
        _valid_host(value.ssh_host)
        and type(value.ssh_port) is int and 1 <= value.ssh_port <= 65535
        and isinstance(value.ssh_user, str) and _SSH_USER.fullmatch(value.ssh_user)
        and _valid_host_key(value.host_key)
        and isinstance(value.project_ref, str) and _SAFE_REF.fullmatch(value.project_ref)
        and isinstance(value.device_ref, str) and _SAFE_REF.fullmatch(value.device_ref)
        and isinstance(value.portal_key, str) and _PORTAL_KEY.fullmatch(value.portal_key)
    ):
        raise PairingError(_INVALID_CODE)


def _canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode_pairing(pairing: Pairing) -> str:
    """Encode only the fixed schema, in canonical JSON and unpadded base64url."""
    _validate(pairing)
    data = {name: getattr(pairing, name) for name in _PAIRING_FIELDS}
    data["version"] = 1
    code = _PREFIX + base64.urlsafe_b64encode(_canonical(data)).decode("ascii").rstrip("=")
    if len(code) > _MAX_CODE_LENGTH:
        raise PairingError(_INVALID_CODE)
    return code


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    data = {}
    for key, value in pairs:
        if key in data:
            raise PairingError(_INVALID_CODE)
        data[key] = value
    return data


def decode_pairing(code: str) -> Pairing:
    """Fail closed on tampering, duplicate keys, noncanonical encoding or schema."""
    if type(code) is not str or not len(_PREFIX) < len(code) <= _MAX_CODE_LENGTH or not code.startswith(_PREFIX):
        raise PairingError(_INVALID_CODE)
    payload = code[len(_PREFIX):]
    if not _CODE_PAYLOAD.fullmatch(payload):
        raise PairingError(_INVALID_CODE)
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != payload:
            raise PairingError(_INVALID_CODE)
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (binascii.Error, UnicodeError, ValueError):
        raise PairingError(_INVALID_CODE) from None
    if not isinstance(data, dict) or set(data) != _FIELDS or type(data["version"]) is not int or data["version"] != 1:
        raise PairingError(_INVALID_CODE)
    pairing = Pairing(*(data[name] for name in _PAIRING_FIELDS))
    _validate(pairing)
    if raw != _canonical(data):
        raise PairingError(_INVALID_CODE)
    return pairing
