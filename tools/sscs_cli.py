"""Send one command to the SSCs garage controller from a PC (bench testing).

    pip install bleak
    set SSCS_KEY=<64 hex chars>        (PowerShell: $env:SSCS_KEY="...")
    python sscs_cli.py stop [--address AA:BB:CC:DD:EE:FF]

This moves a real door. Only run it where you can see the shutter, with the
phone app closed (the controller accepts one connection). Disconnect the ESP32
too, or it may be connected at the same moment.
"""

import argparse
import asyncio
from hashlib import sha256
import os
import sys

from bleak import BleakClient, BleakScanner

CMD_UUID = "00009001-0000-1000-8000-00805f9b34fb"
DATA_UUID = "00009002-0000-1000-8000-00805f9b34fb"
COMMANDS = {"up": 0x00, "stop": 0x01, "down": 0x02}


def make_token(key32: bytes, pkt20: bytes) -> bytes:
    """passcode -1 path (the only verified one)."""
    d = sha256(key32 + pkt20[:16]).digest()
    return bytes(a ^ b for a, b in zip(d[:16], d[16:])) + b"\xff\xff\xff\xff"


async def find(address: str | None):
    if address:
        dev = await BleakScanner.find_device_by_address(address, timeout=8.0)
        if dev:
            return dev
    for d in await BleakScanner.discover(timeout=8.0):
        if (d.name or "").startswith(("SSCs", "SSCg")):
            return d
    return None


async def run(action: str, address: str | None, key: bytes) -> int:
    dev = await find(address)
    if not dev:
        print("Shutter not found. Close the phone app and move closer.")
        return 2

    challenge: asyncio.Queue[bytes] = asyncio.Queue()
    status = asyncio.Event()
    ack = asyncio.Event()
    waiting_for_challenge = True

    def on_cmd(_char, data: bytearray) -> None:
        b = bytes(data)
        if waiting_for_challenge and len(b) == 20:
            challenge.put_nowait(b)
        elif b[:2] == b"\xcb\x00":
            status.set()
        elif b == b"\xcc\x00":
            ack.set()

    async with BleakClient(dev) as client:
        await client.start_notify(DATA_UUID, lambda *_: None)
        await client.start_notify(CMD_UUID, on_cmd)
        try:
            pkt = await asyncio.wait_for(challenge.get(), timeout=5.0)
        except asyncio.TimeoutError:
            print("No challenge received.")
            return 3
        waiting_for_challenge = False
        print(f"Challenge received (flags 0x{int.from_bytes(pkt[16:], 'little'):08x})")

        await client.write_gatt_char(CMD_UUID, make_token(key, pkt), response=True)
        try:
            await asyncio.wait_for(status.wait(), timeout=1.0)
            print("Authenticated (cb00)")
        except asyncio.TimeoutError:
            print("No cb00 status, sending command anyway")

        await client.write_gatt_char(CMD_UUID, bytes([0xC9, 0, 0, COMMANDS[action]]), response=True)
        try:
            await asyncio.wait_for(ack.wait(), timeout=3.0)
            print(f"'{action}' acknowledged (cc00)")
            return 0
        except asyncio.TimeoutError:
            print(f"'{action}' sent, no acknowledgement")
            return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--address", default=os.environ.get("SSCS_ADDRESS"))
    args = parser.parse_args()

    key_hex = os.environ.get("SSCS_KEY", "").replace(" ", "").replace(":", "")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        key = b""
    if len(key) != 32:
        print("Set SSCS_KEY to the 64-hex-character shutter key.")
        return 64
    return asyncio.run(run(args.command, args.address, key))


if __name__ == "__main__":
    sys.exit(main())
