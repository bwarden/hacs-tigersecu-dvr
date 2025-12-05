import asyncio
import logging
from xml.etree import ElementTree as ET
from datetime import timedelta

import aiohttp
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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

    # Wait for the websocket to confirm it is connected and authenticated.
    try:
        await asyncio.wait_for(dvr.api.connected.wait(), timeout=10)
    except asyncio.TimeoutError as err:
        raise ConfigEntryNotReady("Connection to DVR timed out") from err

    # Wait for the initial data (like camera channels) to be populated
    try:
        await asyncio.wait_for(dvr.initial_data_received.wait(), timeout=30)
    except asyncio.TimeoutError as err:
        raise ConfigEntryNotReady(
            "Did not receive initial channel data from DVR in time"
        ) from err

    # Setup platforms now that we have the initial data
    await hass.config_entries.async_forward_entry_setups(
        entry, ["camera", "binary_sensor", "sensor"]
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "camera")
    unload_ok = unload_ok and await hass.config_entries.async_forward_entry_unload(
        entry, "binary_sensor"
    )
    unload_ok = unload_ok and await hass.config_entries.async_forward_entry_unload(
        entry, "sensor"
    )

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
        self._async_add_binary_sensors: AddEntitiesCallback | None = None
        self._created_sensor_ids = set()

        self.coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({self.host})",
            # The websocket pushes updates, so we don't need polling.
            update_interval=timedelta(days=365),
        )
        # Initialize with a structure to hold channel states
        self.coordinator.data = {
            "channels": {},
            "disks": {},
            "network": {},
            "sensors": {},
            "last_login": None,
        }
        self.api = TigersecuDVRAPI(
            self.host,
            self.username,
            self.password,
            session=async_get_clientsession(hass),
            update_callback=self._handle_trigger_update,
        )

    async def async_connect(self):
        """Connect to the DVR."""
        # Initialize with empty data
        await self.api.async_connect()

    def set_binary_sensor_adder(self, async_add_entities: AddEntitiesCallback):
        """Set the callback for adding binary sensors."""
        self._async_add_binary_sensors = async_add_entities

    @callback
    async def _handle_trigger_update(self, trigger_data: dict):
        """Handle a trigger update from the API and dispatch it."""
        event_type = trigger_data.get("event")
        if not event_type:
            return

        _LOGGER.debug("Received event: %s", event_type)

        # A burst of VideoInput events is a good marker for the end of the initial state dump.
        if event_type == "channels_discovered":
            _LOGGER.debug("Channels discovered: %s", trigger_data.get("channels"))
            self.channels = trigger_data.get("channels", [])
            for channel_id in self.channels:
                if channel_id not in self.coordinator.data["channels"]:
                    self.coordinator.data["channels"][channel_id] = {
                        "record_type": "None",
                        "motion_detected": False,
                        "vloss": False,
                    }
            self.initial_data_received.set()
            # We don't need to process this special event further.
            return

        current_data = self.coordinator.data
        updated = False

        if event_type == "video_input":
            data = trigger_data.get("data", {})
            channel_id = int(data.get("CH"))
            if channel_id not in self.channels:
                self.channels.append(channel_id)
                current_data["channels"][channel_id] = {
                    "record_type": "None",
                    "motion_detected": False,
                    "vloss": False,
                }
                updated = True

        elif event_type == "motion":
            channel_id = trigger_data.get("channel")
            state = trigger_data.get("state")
            if channel_id not in current_data["channels"]:
                _LOGGER.debug(
                    "Initializing state for channel %s on motion event", channel_id
                )
                current_data["channels"][channel_id] = {
                    "motion_detected": not state,
                    "vloss": False,
                    "record_type": "None",
                }

            if current_data["channels"][channel_id]["motion_detected"] != state:
                current_data["channels"][channel_id]["motion_detected"] = state
                updated = True

        elif event_type == "vloss":
            channel_id = trigger_data.get("channel")
            state = trigger_data.get("state")
            if channel_id not in current_data["channels"]:
                _LOGGER.debug(
                    "Initializing state for channel %s on vloss event", channel_id
                )
                current_data["channels"][channel_id] = {
                    "motion_detected": False,
                    "vloss": not state,
                    "record_type": "None",
                }

            if current_data["channels"][channel_id]["vloss"] != state:
                current_data["channels"][channel_id]["vloss"] = state
                updated = True

        elif event_type == "record":
            channel_id = trigger_data.get("channel")
            record_type = trigger_data.get("type")
            if channel_id not in current_data["channels"]:
                _LOGGER.debug(
                    "Initializing state for channel %s on record event", channel_id
                )
                current_data["channels"][channel_id] = {
                    "motion_detected": False,
                    "vloss": False,
                    "record_type": "None",
                }

            if current_data["channels"][channel_id]["record_type"] != record_type:
                current_data["channels"][channel_id]["record_type"] = record_type
                updated = True

        elif event_type == "login":
            data = trigger_data.get("data", {})
            current_data["last_login"] = {
                "user": data.get("User"),
                "from": data.get("From"),
            }
            updated = True

        elif event_type == "network":
            data = trigger_data.get("data", {})
            current_data["network"] = {
                "link": data.get("Link") == "True",
                "ip": data.get("IP"),
                "mac": data.get("MAC"),
                "gateway": data.get("DHCP_Gateway"),
                "netmask": data.get("DHCP_Netmask"),
                "external_ip": data.get("GIP"),
                "speed": data.get("SPD"),
            }
            updated = True

        elif event_type == "disk":
            data = trigger_data.get("data", {})
            disk_id = int(data.get("ID"))
            if disk_id not in current_data["disks"]:
                current_data["disks"][disk_id] = {"smart_attributes": {}}

            # This part is a bit tricky as Disk events are separate from SMART events.
            # We can merge the data.
            current_data["disks"][disk_id].update(
                {
                    "model": data.get("Model"),
                    "status": data.get("Status"),
                    "capacity_gb": round(int(data.get("Capacity", 0)) / (1024**3), 2),
                    "available_gb": round(int(data.get("Available", 0)) / (1024**3), 2),
                }
            )
            updated = True

        elif event_type == "smart":
            disk_id = trigger_data.get("disk_id")
            if disk_id not in current_data["disks"]:
                current_data["disks"][disk_id] = {"smart_attributes": {}}

            for attr_id, attr_data in trigger_data.get("attributes", {}).items():
                current_data["disks"][disk_id]["smart_attributes"][attr_id] = {
                    "value": attr_data.get("Value"),
                    "worst": attr_data.get("Worst"),
                    "threshold": attr_data.get("Thresh"),
                    "raw": attr_data.get("RAW"),
                }
            updated = True

        elif event_type == "sensor":
            sensor_id = trigger_data.get("sensor_id")
            state = trigger_data.get("state")

            # If this is the first time we see this sensor, create the entity.
            if (
                sensor_id not in self._created_sensor_ids
                and self._async_add_binary_sensors
            ):
                _LOGGER.info("Discovered new alarm sensor: %s", sensor_id)
                # Import here to avoid circular dependency
                from .binary_sensor import TigersecuAlarmSensor

                new_sensor = TigersecuAlarmSensor(self, sensor_id)
                self._async_add_binary_sensors([new_sensor])
                self._created_sensor_ids.add(sensor_id)
                # Initialize state
                current_data["sensors"][sensor_id] = not state

            if current_data["sensors"].get(sensor_id) != state:
                current_data["sensors"][sensor_id] = state
                updated = True

        if updated:
            self.coordinator.async_set_updated_data(self.coordinator.data)
