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

    dvr = TigersecuDVR(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = dvr

    try:
        await dvr.async_connect()
    except ConnectionError as err:
        raise ConfigEntryNotReady(f"Failed to connect to DVR: {err}") from err

    # Wait for the initial data (like camera channels) to be populated
    await dvr.initial_data_received.wait()

    # Setup platforms now that we have the initial data
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "camera")
    )
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "binary_sensor")
    )
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "sensor")
    )

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "camera")
    unload_ok = unload_ok and await hass.config_entries.async_forward_entry_unload(entry, "binary_sensor")
    unload_ok = unload_ok and await hass.config_entries.async_forward_entry_unload(entry, "sensor")

    if unload_ok:
        dvr: TigersecuDVR = hass.data[DOMAIN].pop(entry.entry_id)
        await dvr.api.async_disconnect()

    return unload_ok


class TigersecuDVR:
    """Manages the Tigersecu DVR API and coordinates updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialize the DVR connection."""
        self.hass = hass
        self.entry = entry
        self.host = entry.data["host"]
        self.username = entry.data["username"]
        self.password = entry.data["password"]
        self.channels = []
        self.initial_data_received = asyncio.Event()

        self._trigger_handlers = {
            "Motion": self._handle_motion_event,
            "VLOSS": self._handle_vloss_event,
            "Disk": self._handle_disk_event,
            "Record": self._handle_record_event,
            "Login": self._handle_login_event,
            "Network": self._handle_network_event,
            "SMART": self._handle_smart_event,
            "VideoInput": self._handle_video_input_event,
        }
        self.coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({host})",
            # The websocket pushes updates, so we don't need polling.
            update_interval=timedelta(days=365),
            # Initialize with a structure to hold channel states
            data={
                "channels": {},
                "disks": {},
                "network": {},
                "last_login": None,
            },
        )
        self.api = TigersecuDVRAPI(
            host,
            self.username,
            self.password,
            session=async_get_clientsession(hass),
            update_callback=self._handle_trigger_update,
        )

    async def async_connect(self):
        """Connect to the DVR."""
        # Initialize with empty data
        await self.api.async_connect()

    @callback
    async def _handle_trigger_update(self, trigger_data: dict):
        """Handle a trigger update from the API and dispatch it."""
        if not trigger_data:
            return

        # The "Login" event is a good marker for the end of the initial state dump.
        if trigger_data.get("Event") == "Login":
            self.initial_data_received.set()

        # We don't update the coordinator here directly.
        # The specific event handlers will update the coordinator's state
        # with meaningful data for the entities.
        # self.coordinator.async_set_updated_data(trigger_data.copy())

        self._dispatch_trigger(trigger_data)

    def _dispatch_trigger(self, trigger_data: dict):
        """Dispatch a trigger element to the appropriate handler."""
        event_type = trigger_data.get("Event")
        if not event_type:
            _LOGGER.debug("Received a trigger with no Event attribute: %s", trigger_data)
            return

        handler = self._trigger_handlers.get(event_type)
        if handler:
            handler(trigger_data)
        else:
            _LOGGER.debug("No handler for event type '%s'", event_type)

    def _handle_motion_event(self, trigger_data: dict):
        """Handle a motion detection event."""
        _LOGGER.debug("Motion Event: %s", trigger_data)
        try:
            motion_mask = int(trigger_data.get("Value", "0"))
            current_data = self.coordinator.data.copy()
            for channel_id in self.channels:
                # Check if the bit for this channel is set in the mask
                is_motion = bool(motion_mask & (1 << channel_id))
                if current_data["channels"][channel_id]["motion_detected"] != is_motion:
                    current_data["channels"][channel_id]["motion_detected"] = is_motion
            self.coordinator.async_set_updated_data(current_data)
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid Motion event: %s", trigger_data)

    def _handle_vloss_event(self, trigger_data: dict):
        """Handle a video loss event."""
        _LOGGER.debug("Video Loss Event: %s", trigger_data)
        try:
            vloss_mask = int(trigger_data.get("Value", "0"))
            current_data = self.coordinator.data.copy()
            for channel_id in self.channels:
                # Check if the bit for this channel is set in the mask
                is_vloss = bool(vloss_mask & (1 << channel_id))
                if current_data["channels"][channel_id]["vloss"] != is_vloss:
                    current_data["channels"][channel_id]["vloss"] = is_vloss
            self.coordinator.async_set_updated_data(current_data)
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid VLOSS event: %s", trigger_data)

    def _handle_disk_event(self, trigger_data: dict):
        """Handle a disk status event."""
        _LOGGER.info("Disk Event: %s", trigger_data)

    def _handle_login_event(self, trigger_data: dict):
        """Handle a user login event."""
        _LOGGER.info("Login Event: %s", trigger_data)
        current_data = self.coordinator.data.copy()
        current_data["last_login"] = {
            "user": trigger_data.get("User"),
            "from": trigger_data.get("From"),
        }
        self.coordinator.async_set_updated_data(current_data)

    def _handle_network_event(self, trigger_data: dict):
        """Handle a network status event."""
        _LOGGER.info("Network Event: %s", trigger_data)
        current_data = self.coordinator.data.copy()
        current_data["network"] = {
            "link": trigger_data.get("Link") == "True",
            "ip": trigger_data.get("IP"),
            "mac": trigger_data.get("MAC"),
            "gateway": trigger_data.get("DHCP_Gateway"),
            "netmask": trigger_data.get("DHCP_Netmask"),
            "external_ip": trigger_data.get("GIP"),
            "speed": trigger_data.get("SPD"),
        }
        self.coordinator.async_set_updated_data(current_data)

    def _handle_smart_event(self, trigger: ET.Element):
        """Handle a disk SMART status event."""
        _LOGGER.info("SMART Event: %s", trigger.attrib)
        try:
            disk_id = int(trigger.get("ID"))
            current_data = self.coordinator.data.copy()

            if disk_id not in current_data["disks"]:
                current_data["disks"][disk_id] = {"smart_attributes": {}}

            for attr in trigger:
                if attr.tag == "Attribute":
                    attr_id = int(attr.get("ID"))
                    current_data["disks"][disk_id]["smart_attributes"][attr_id] = {
                        "value": attr.get("Value"),
                        "worst": attr.get("Worst"),
                        "threshold": attr.get("Thresh"),
                        "raw": attr.get("RAW"),
                    }
            self.coordinator.async_set_updated_data(current_data)
        except (ValueError, KeyError, TypeError):
            _LOGGER.warning("Received invalid SMART event: %s", trigger.attrib)

    def _handle_record_event(self, trigger_data: dict):
        """Handle a record status event, which indicates motion."""
        _LOGGER.info("Record Event: %s", trigger_data)
        try:
            channel_id = int(trigger_data["ID"])
            record_type = trigger_data.get("Type")

            # Update the state for the specific channel
            current_data = self.coordinator.data.copy()
            if channel_id in current_data["channels"]:
                current_data["channels"][channel_id]["record_type"] = record_type
                self.coordinator.async_set_updated_data(current_data)

        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid Record event: %s", trigger_data)

    def _handle_video_input_event(self, trigger_data: dict):
        """Handle a video input discovery event."""
        _LOGGER.info("Video Input Event: %s", trigger_data)
        try:
            channel_id = int(trigger_data["CH"])
            if channel_id not in self.channels:
                self.channels.append(channel_id)
                # Initialize the state for this new channel
                current_data = self.coordinator.data.copy()
                current_data["channels"][channel_id] = {
                    "record_type": "None",
                    "motion_detected": False,
                    "vloss": False,
                }
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid VideoInput event: %s", trigger_data)