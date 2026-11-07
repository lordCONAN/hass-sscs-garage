"""Pure protocol logic for the Sysdes "SSCs" Smart Garage BLE controller.

Port of components/sscs_garage/sscs_protocol.* (the ESPHome component). No Home
Assistant or BLE imports, so it can be unit-tested on its own. See docs/protocol.md
for how each piece was established.
"""

from __future__ import annotations

from enum import Enum, IntEnum
from hashlib import sha256

# GATT layout (16-bit UUIDs on the Bluetooth base UUID).
SERVICE_UUID = "00003333-0000-1000-8000-00805f9b34fb"
CMD_UUID = "00009001-0000-1000-8000-00805f9b34fb"  # challenge, status, ACK and all writes
DATA_UUID = "00009002-0000-1000-8000-00805f9b34fb"  # subscribed like the app does; no traffic used

KEY_LEN = 32
CHALLENGE_PACKET_LEN = 20  # 16-byte challenge + 4-byte LE flags
TOKEN_LEN = 20
COMMAND_LEN = 4

# Passcode value meaning "no passcode" (the only mode verified on hardware).
NO_PASSCODE = -1

# Some historical firmware only sent the challenge after this write.
NUDGE = b"\x00\x00"

NAME_PREFIXES = ("SSCs", "SSCg")

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class Command(IntEnum):
    """Last byte of the c9 00 00 xx command."""

    UP = 0x00
    STOP = 0x01
    DOWN = 0x02

    def __str__(self) -> str:
        return self.name.lower()


def command_bytes(cmd: Command) -> bytes:
    """c9 00 00 <cmd>."""
    return bytes((0xC9, 0x00, 0x00, int(cmd)))


def is_ack(data: bytes) -> bool:
    """cc 00: the controller received the command (not proof the shutter moved)."""
    return len(data) == 2 and data[0] == 0xCC and data[1] == 0x00


def is_status(data: bytes) -> bool:
    """cb 00 ...: status notification the controller sends once authenticated."""
    return len(data) >= 2 and data[0] == 0xCB and data[1] == 0x00


def challenge_flags(packet20: bytes) -> int:
    """Little-endian flags in bytes 16..19 of the challenge packet."""
    return int.from_bytes(packet20[16:20], "little")


def make_token(key: bytes, packet20: bytes, passcode: int = NO_PASSCODE) -> bytes:
    """20-byte answer to a challenge packet.

    token[0..15]  = upper16(SHA256(key || challenge)) XOR lower16(...)
    token[16..19] = passcode tail (FF FF FF FF for NO_PASSCODE)
    Only the NO_PASSCODE path has been verified on hardware.
    """
    if len(key) != KEY_LEN:
        raise ValueError("key must be 32 bytes")
    if len(packet20) < CHALLENGE_PACKET_LEN:
        raise ValueError("challenge packet must be 20 bytes")
    challenge = bytes(packet20[:16])
    digest = sha256(bytes(key) + challenge).digest()
    # sscHARF128 with a destination buffer: XOR the halves, no byte reversal.
    core = bytes(a ^ b for a, b in zip(digest[:16], digest[16:]))

    if passcode == NO_PASSCODE:
        tail = 0xFFFFFFFF
    else:
        # Reconstructed from the app; NOT verified on hardware.
        p = passcode & 0xFFFFFFFF
        if challenge_flags(packet20) & 1:
            t = int.from_bytes(sha256(challenge + p.to_bytes(4, "little")).digest()[:4], "little")
            tail = 0xFFFFFFFE if t == 0xFFFFFFFF else t
        else:
            tail = p
    return core + tail.to_bytes(4, "little")


# ---------------------------------------------------------------- key input


class KeyParseResult(Enum):
    """Outcome of parse_key; the value is the reason shown to the user."""

    OK = "ok"
    EMPTY = "empty"
    BAD_FORMAT = "not 64 hex characters or a setup QR code"
    BAD_LENGTH = "QR code did not contain a 32-byte key"
    UNSUPPORTED_SLOT = "QR codes for remote slots 2-4 are not supported yet"
    SHARE_CODE = "shared (encrypted) QR codes are not supported"


class InvalidKey(ValueError):
    """Raised by parse_key with the KeyParseResult that rejected the text."""

    def __init__(self, result: KeyParseResult) -> None:
        super().__init__(result.value)
        self.result = result


def base58_decode(text: str) -> bytes | None:
    """Standard Base58 (Bitcoin alphabet). None on a bad character."""
    n = 0
    for c in text:
        v = BASE58_ALPHABET.find(c)
        if v < 0:
            return None
        n = n * 58 + v
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    zeros = len(text) - len(text.lstrip("1"))
    return b"\0" * zeros + raw


_SPACE = " \t\r\n"
_HEX_SEPARATORS = " :-"
_HEX = "0123456789abcdefABCDEF"


def parse_key_result(text: str) -> tuple[KeyParseResult, bytes | None]:
    """Like parse_key, but returns (result, key) instead of raising."""
    text = text.strip(_SPACE)
    if not text:
        return KeyParseResult.EMPTY, None

    # 64 hex characters, separators allowed.
    if all(c in _HEX or c in _HEX_SEPARATORS for c in text):
        digits = "".join(c for c in text if c in _HEX)
        if len(digits) == KEY_LEN * 2:
            return KeyParseResult.OK, bytes.fromhex(digits)

    # Setup QR code: 2-character type header + Base58(32-byte key).
    if text[0] == "0":
        return KeyParseResult.SHARE_CODE, None
    if len(text) < 3:
        return KeyParseResult.BAD_FORMAT, None
    if text[0] == "R" and text[1] in "234":
        return KeyParseResult.UNSUPPORTED_SLOT, None
    if text[:2] not in ("R1", "MS"):
        return KeyParseResult.BAD_FORMAT, None
    decoded = base58_decode(text[2:])
    if decoded is None:
        return KeyParseResult.BAD_FORMAT, None
    if len(decoded) != KEY_LEN:
        return KeyParseResult.BAD_LENGTH, None
    return KeyParseResult.OK, decoded


def parse_key(text: str) -> bytes:
    """Key from 64 hex characters (spaces, ':' and '-' ignored) or the setup QR text.

    The QR text is "R1" or "MS" followed by Base58. Raises InvalidKey with the reason
    otherwise; the exception message never contains the input.
    """
    result, key = parse_key_result(text)
    if key is None:
        raise InvalidKey(result)
    return key


def key_fingerprint(key: bytes) -> str:
    """First 4 bytes of SHA256(key) as 8 hex chars; safe to show to identify a key."""
    return sha256(bytes(key)).digest()[:4].hex()


def is_sscs_name(name: str | None) -> bool:
    """True for an advertised name the controller uses."""
    return bool(name) and name.startswith(NAME_PREFIXES)
