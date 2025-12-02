import asyncio
import logging
from xml.etree import ElementTree as ET
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryNotReady

from .api import TigersecuDVRAPI

_LOGGER = logging.getLogger(__name__)

DOMAIN = "tigersecu_dvr"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Tigersecu DVR from a config entry."""
    host = entry.data["host"]
    username = entry.data["username"]
    password = entry.data["password"]

    dvr = TigersecuDVR(hass, host, username, password)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = dvr

    try:
        await dvr.async_connect()
    except ConnectionError as err:
        raise ConfigEntryNotReady(f"Failed to connect to DVR: {err}") from err

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
        await dvr.api.async_disconnect()

    return unload_ok


class TigersecuDVR:
    """Manages the Tigersecu DVR API and coordinates updates."""

    def __init__(self, hass: HomeAssistant, host: str, username: str, password: str):
        """Initialize the DVR connection."""
        self.hass = hass
        self.coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({host})",
            # The websocket pushes updates, so we don't need polling.
            update_interval=timedelta(days=365),
        )
        self.api = TigersecuDVRAPI(
            host,
            username,
            password,
            session=async_get_clientsession(hass),
            update_callback=self._handle_xml_update,
        )

    async def async_connect(self):
        """Connect to the DVR."""
        # Initialize with empty data
        self.coordinator.async_set_updated_data({})
        await self.api.async_connect()

    @callback
    async def _handle_xml_update(self, xml_string: str):
        """Parse XML and update the coordinator."""
        # This is where you would parse the XML and update your state.
        # For now, we'll just log it and pass it to the coordinator.
        self.coordinator.async_set_updated_data({"last_xml": xml_string})