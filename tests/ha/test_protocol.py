"""protocol.py against the vectors tests/reference.py generates for the C++ test."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import subprocess
import sys

import pytest

from custom_components.sscs_garage import protocol
from custom_components.sscs_garage.protocol import Command, KeyParseResult

REFERENCE = Path(__file__).resolve().parents[1] / "reference.py"


def _c_bytes(text: str) -> bytes:
    return bytes(int(x, 16) for x in re.findall(r"0x([0-9a-f]{2})", text))


@pytest.fixture(scope="module")
def reference_output() -> str:
    # Same default seed the Makefile uses, so these are the C++ test's vectors.
    return subprocess.run(
        [sys.executable, "-I", str(REFERENCE)], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(scope="module")
def token_vectors(reference_output: str) -> list[tuple[bytes, bytes, int, bytes]]:
    rows = re.findall(r"^  \{(\{[^}]*\}), (\{[^}]*\}), (-?\d+)LL, (\{[^}]*\})\},$", reference_output, re.M)
    assert len(rows) == 12
    return [(_c_bytes(k), _c_bytes(p), int(pc), _c_bytes(t)) for k, p, pc, t in rows]


@pytest.fixture(scope="module")
def qr_vectors(reference_output: str) -> list[tuple[str, bytes]]:
    rows = re.findall(r'^  \{"([^"]+)", (\{[^}]*\})\},$', reference_output, re.M)
    assert len(rows) == 8
    return [(text, _c_bytes(key)) for text, key in rows]


def test_tokens_match_reference(token_vectors) -> None:
    for key, packet, passcode, token in token_vectors:
        assert len(key) == 32 and len(packet) == 20 and len(token) == 20
        assert protocol.make_token(key, packet, passcode) == token


def test_verified_token_path() -> None:
    """passcode -1: XOR of the digest halves plus FF FF FF FF (the hardware-verified path)."""
    key = bytes(range(32))
    packet = bytes(range(100, 116)) + bytes.fromhex("12100900")
    d = sha256(key + packet[:16]).digest()
    expected = bytes(a ^ b for a, b in zip(d[:16], d[16:])) + b"\xff\xff\xff\xff"
    assert protocol.make_token(key, packet) == expected
    assert protocol.make_token(key, packet, protocol.NO_PASSCODE) == expected


def test_token_rejects_bad_lengths() -> None:
    with pytest.raises(ValueError):
        protocol.make_token(bytes(31), bytes(20))
    with pytest.raises(ValueError):
        protocol.make_token(bytes(32), bytes(19))


def test_notifications() -> None:
    assert protocol.is_ack(b"\xcc\x00")
    assert not protocol.is_ack(b"\xcc\x01")
    assert not protocol.is_ack(b"\xcb\x00\x01\x02")
    assert protocol.is_status(b"\xcb\x00\x01\x02")
    assert protocol.is_status(b"\xcb\x00")
    assert not protocol.is_status(b"\xcb")
    assert not protocol.is_status(b"\xcc\x00")


def test_commands() -> None:
    assert protocol.command_bytes(Command.UP) == bytes([0xC9, 0, 0, 0])
    assert protocol.command_bytes(Command.STOP) == bytes([0xC9, 0, 0, 1])
    assert protocol.command_bytes(Command.DOWN) == bytes([0xC9, 0, 0, 2])
    assert str(Command.DOWN) == "down"


def test_challenge_flags() -> None:
    assert protocol.challenge_flags(bytes(16) + bytes.fromhex("12100900")) == 0x00091012


def test_uuids() -> None:
    assert protocol.SERVICE_UUID.startswith("00003333-")
    assert protocol.CMD_UUID.startswith("00009001-")
    assert protocol.DATA_UUID.startswith("00009002-")


def test_qr_and_hex_keys(qr_vectors) -> None:
    for text, key in qr_vectors:
        assert protocol.parse_key(text) == key
        assert protocol.parse_key(f"  {text}\n") == key
        # Same formatting as the C++ test: lower hex, ':' every 4 bytes, leading spaces.
        hex_text = "  " + "".join(f"{b:02X}:" if i % 4 == 3 else f"{b:02x}" for i, b in enumerate(key))
        assert protocol.parse_key(hex_text) == key
        assert protocol.parse_key(key.hex()) == key
        assert protocol.parse_key(key.hex("-").upper()) == key


def test_leading_zero_qr_keys(qr_vectors) -> None:
    assert qr_vectors[1][1][:1] == b"\0"
    assert qr_vectors[2][1][:2] == b"\0\0"


@pytest.mark.parametrize(
    ("make_text", "result"),
    [
        (lambda qr: "R2" + qr[2:], KeyParseResult.UNSUPPORTED_SLOT),
        (lambda qr: "R3" + qr[2:], KeyParseResult.UNSUPPORTED_SLOT),
        (lambda qr: "R4" + qr[2:], KeyParseResult.UNSUPPORTED_SLOT),
        (lambda qr: "0" + qr, KeyParseResult.SHARE_CODE),
        (lambda qr: qr + "0", KeyParseResult.BAD_FORMAT),  # '0' is not Base58
        (lambda qr: qr + "z", KeyParseResult.BAD_LENGTH),  # too long for 32 bytes
        (lambda qr: "XY" + qr[2:], KeyParseResult.BAD_FORMAT),
        (lambda qr: "   ", KeyParseResult.EMPTY),
        (lambda qr: "abc", KeyParseResult.BAD_FORMAT),
        (lambda qr: "ab" * 31, KeyParseResult.BAD_FORMAT),  # 62 hex digits
    ],
)
def test_rejected_keys(qr_vectors, make_text, result) -> None:
    text = make_text(qr_vectors[0][0])
    assert protocol.parse_key_result(text) == (result, None)
    with pytest.raises(protocol.InvalidKey) as err:
        protocol.parse_key(text)
    assert err.value.result is result
    # The reason never echoes the input.
    assert str(err.value) == result.value
    if len(text.strip()) > 3:
        assert text.strip() not in str(err.value)


def test_fingerprint(qr_vectors) -> None:
    key = qr_vectors[0][1]
    fp = protocol.key_fingerprint(key)
    assert fp == sha256(key).hexdigest()[:8]
    assert re.fullmatch(r"[0-9a-f]{8}", fp)
    assert protocol.key_fingerprint(protocol.parse_key(qr_vectors[0][0])) == fp


def test_names() -> None:
    assert protocol.is_sscs_name("SSCs12345")
    assert protocol.is_sscs_name("SSCg")
    assert not protocol.is_sscs_name("sscs")
    assert not protocol.is_sscs_name(None)
    assert not protocol.is_sscs_name("")
