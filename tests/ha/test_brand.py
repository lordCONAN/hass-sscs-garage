"""The integration ships its own icon (custom_components/sscs_garage/brand/)."""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.sscs_garage.const import DOMAIN

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


async def test_brand_icon(hass: HomeAssistant) -> None:
    integration = await async_get_integration(hass, DOMAIN)
    assert integration.has_branding
    brand = Path(integration.file_path) / "brand"
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        data = (brand / name).read_bytes()
        assert data[:8] == PNG_SIGNATURE
        # IHDR width/height, big-endian, right after the signature and chunk header.
        assert int.from_bytes(data[16:20], "big") == size
        assert int.from_bytes(data[20:24], "big") == size
