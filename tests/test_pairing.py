"""Offline, credential-free fixtures for the versioned pairing invitation."""

import base64
from dataclasses import asdict, replace
import json
import unittest

from dev_portal.pairing import Pairing, PairingError, decode_pairing, encode_pairing


def _host_key(key=b"K" * 32, *, kind=b"ssh-ed25519", declared_length=None, suffix=b""):
    length = len(key) if declared_length is None else declared_length
    blob = len(kind).to_bytes(4, "big") + kind + length.to_bytes(4, "big") + key + suffix
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def _pairing():
    return Pairing(
        ssh_host="ssh.example.org", ssh_port=22, ssh_user="portal_user",
        host_key=_host_key(), project_ref="game-dev-ue", device_ref="tester-laptop",
        portal_key="P" * 62 + "-_",
    )


def _data():
    return {**asdict(_pairing()), "version": 1}


def _wire(raw):
    return "DP1." + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _json_code(data):
    return _wire(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"))


class PairingTests(unittest.TestCase):
    def assert_invalid_code(self, code):
        with self.assertRaises(PairingError) as caught:
            decode_pairing(code)
        self.assertEqual(str(caught.exception), "Invalid pairing code")
        self.assertIsNone(caught.exception.__cause__)

    def test_roundtrip_exact_canonical_schema_and_secret_free_repr(self):
        pairing = _pairing()
        self.assertTrue(Pairing.__dataclass_params__.frozen)
        self.assertNotIn(pairing.portal_key, repr(pairing))
        code = encode_pairing(pairing)
        self.assertTrue(code.startswith("DP1."))
        self.assertNotIn("=", code)
        self.assertLessEqual(len(code), 4096)
        self.assertEqual(decode_pairing(code), pairing)
        payload = code[4:]
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        self.assertEqual(raw, json.dumps(_data(), sort_keys=True, separators=(",", ":")).encode("utf-8"))
        self.assertEqual(set(json.loads(raw)), {
            "version", "ssh_host", "ssh_port", "ssh_user", "host_key",
            "project_ref", "device_ref", "portal_key",
        })

    def test_valid_dns_and_nonloopback_ipv4(self):
        for host in ("ssh.example.org", "a-b.example", "203.0.113.17", "192.168.1.25"):
            with self.subTest(host=host):
                pairing = replace(_pairing(), ssh_host=host)
                self.assertEqual(decode_pairing(encode_pairing(pairing)), pairing)

    def test_invalid_envelope_base64_and_tampering(self):
        code = encode_pairing(_pairing())
        for candidate in (
            "", "DP1.", code.replace("DP1.", "DP2.", 1), code + " ",
            " " + code, code + "\n", "DP1.!", "DP1.é", "DP1.YQ==",
            "DP1.YR", "DP1.__", "DP1." + "A" * 4093,
            code[:4] + "A" + code[5:],
        ):
            with self.subTest(case=len(candidate)):
                self.assert_invalid_code(candidate)

    def test_duplicate_keys_noncanonical_json_and_unsupported_version(self):
        data = _data()
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        for raw in (
            canonical.replace('"version":1', '"version":1,"version":1').encode("utf-8"),
            json.dumps(data, indent=1, sort_keys=True).encode("utf-8"),
            json.dumps(data, separators=(",", ":")).encode("utf-8"),
            canonical.replace('"ssh_user"', '"ssh_\\u0075ser"').encode("utf-8"),
            b"\xef\xbb\xbf" + canonical.encode("utf-8"),
        ):
            with self.subTest(size=len(raw)):
                self.assert_invalid_code(_wire(raw))
        for change in ({"version": True}, {"version": 2}, {"version": "1"}, {"unused": "extra"}):
            self.assert_invalid_code(_json_code({**data, **change}))
        missing = dict(data)
        del missing["device_ref"]
        self.assert_invalid_code(_json_code(missing))
        self.assert_invalid_code(_wire(b"[]"))

    def test_invalid_host_and_option_injection(self):
        bad_hosts = (
            "", "localhost", "LOCALHOST", "a.localhost", "localhost.localdomain",
            "127.0.0.1", "127.23.4.5", "127.1", "2130706433",
            "0177.0.0.1", "0x7f000001", "0.0.0.0", "224.0.0.1",
            "user@ssh.example.org", "ssh.example.org:22", "ssh.example.org/path",
            "ssh.example.org\\name", "-oProxyCommand=evil", "ssh..example",
            "a" * 64 + ".example", "é.example", "ssh.example.org\t",
        )
        for index, host in enumerate(bad_hosts):
            with self.subTest(index=index):
                self.assert_invalid_code(_json_code({**_data(), "ssh_host": host}))
                with self.assertRaises(PairingError):
                    encode_pairing(replace(_pairing(), ssh_host=host))

    def test_invalid_port_user_references_and_portal_key(self):
        cases = (
            ("ssh_port", True), ("ssh_port", 0), ("ssh_port", 65536), ("ssh_port", 22.0),
            ("ssh_port", "22"), ("ssh_user", ""), ("ssh_user", "-oProxyCommand=evil"),
            ("ssh_user", "user@host"), ("ssh_user", "User"), ("ssh_user", "user name"),
            ("ssh_user", "u" * 33), ("ssh_user", "user\x00"),
            ("project_ref", "ab"), ("project_ref", "a_b-c"), ("project_ref", "A-b"),
            ("project_ref", "a" * 97), ("device_ref", "dev/ref"),
            ("device_ref", "dev ref"), ("device_ref", "-dev"),
            ("portal_key", "P" * 63), ("portal_key", "P" * 65),
            ("portal_key", "P" * 63 + "+"), ("portal_key", "P" * 63 + "\n"),
        )
        for index, (field, bad) in enumerate(cases):
            with self.subTest(index=index):
                self.assert_invalid_code(_json_code({**_data(), field: bad}))
                with self.assertRaises(PairingError):
                    encode_pairing(replace(_pairing(), **{field: bad}))

    def test_full_ssh_ed25519_blob_required(self):
        original = _host_key()
        bad_keys = (
            "", "ssh-rsa " + original.split()[1],
            original + " extra-comment", "@cert-authority " + original,
            original.replace("ssh-ed25519 ", "ssh-ed25519  "),
            _host_key(kind=b"ssh-rsa"), _host_key(kind=b"ssh-ed25518"),
            _host_key(key=b"K" * 31),
            _host_key(declared_length=31), _host_key(suffix=b"X"),
            original[:-1] + "!",
        )
        for index, key in enumerate(bad_keys):
            with self.subTest(index=index):
                self.assert_invalid_code(_json_code({**_data(), "host_key": key}))
                with self.assertRaises(PairingError):
                    encode_pairing(replace(_pairing(), host_key=key))

    def test_errors_never_echo_code_or_bearer(self):
        sentinel = "NEVER_PRINT_PRIVATE_CODE"
        with self.assertRaises(PairingError) as caught:
            decode_pairing("DP1." + sentinel)
        self.assertEqual(str(caught.exception), "Invalid pairing code")
        self.assertNotIn(sentinel, repr(caught.exception))
        with self.assertRaises(PairingError) as caught:
            encode_pairing(replace(_pairing(), portal_key=sentinel))
        self.assertEqual(str(caught.exception), "Invalid pairing code")
        self.assertNotIn(sentinel, repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
