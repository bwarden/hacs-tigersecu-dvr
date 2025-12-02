import asyncio
import logging
import ssl
import base64
from xml.etree import ElementTree as ET

import aiohttp
import async_timeout

from homeassistant.components.camera import Camera
from homeassistant.components.binary_sensor import BinarySensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

DOMAIN = "tigersecu_dvr"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Tigersecu DVR from a config entry."""
    host = entry.data["host"]
    username = entry.data["username"]
    password = entry.data["password"]

    dvr = TigersecuDVR(hass, host, username, password)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = dvr

    await dvr.async_connect()

    # Setup platforms
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "camera")
    )
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "binary_sensor")
    )

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "camera")
    unload_ok = unload_ok and await hass.config_entries.async_forward_entry_unload(entry, "binary_sensor")

    if unload_ok:
        dvr: TigersecuDVR = hass.data[DOMAIN].pop(entry.entry_id)
        await dvr.async_disconnect()

    return unload_ok


class TigersecuDVR:
    """Manages connection and data for the Tigersecu DVR."""

    def __init__(self, hass: HomeAssistant, host: str, username: str, password: str):
        """Initialize the DVR connection."""
        self.hass