"""Shared helpers for the HA tests."""

from __future__ import annotations

import time

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak


def service_info(address: str, name: str, rssi: int = -60, connectable: bool = True) -> BluetoothServiceInfoBleak:
    """An advertisement as Home Assistant's Bluetooth integration reports it."""
    adv = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        tx_power=None,
        rssi=rssi,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=rssi,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=adv,
        connectable=connectable,
        time=time.monotonic(),
        tx_power=None,
    )
