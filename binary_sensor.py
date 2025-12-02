"""Binary sensor platform for the Tigersecu DVR integration."""
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TigersecuDVR
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tigersecu DVR binary sensor platform."""
    dvr: TigersecuDVR = hass.data[DOMAIN][entry.entry_id]

    sensors: list[BinarySensorEntity] = []
    for channel_id in sorted(dvr.channels):
        sensors.append(TigersecuMotionSensor(dvr, channel_id))
        sensors.append(TigersecuVlossSensor(dvr, channel_id))

    async_add_entities(sensors)


class TigersecuMotionSensor(CoordinatorEntity, BinarySensorEntity):
    """A motion sensor for a Tigersecu DVR channel."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, dvr: TigersecuDVR, channel_id: int) -> None:
        """Initialize the motion sensor."""
        super().__init__(dvr.coordinator)
        self._dvr = dvr
        self._channel_id = channel_id
        self._attr_name = f"{self._dvr.host} Motion {self._channel_id + 1}"
        self._attr_unique_id = f"{self._dvr.entry.entry_id}_motion_{self._channel_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._dvr.entry.entry_id)},
        }

    @property
    def is_on(self) -> bool:
        """Return true if motion is detected."""
        return self.coordinator.data["channels"][self._channel_id]["motion_detected"]


class TigersecuVlossSensor(CoordinatorEntity, BinarySensorEntity):
    """A video loss sensor for a Tigersecu DVR channel."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, dvr: TigersecuDVR, channel_id: int) -> None:
        """Initialize the video loss sensor."""
        super().__init__(dvr.coordinator)
        self._dvr = dvr
        self._channel_id = channel_id
        self._attr_name = f"{self._dvr.host} Video Loss {self._channel_id + 1}"
        self._attr_unique_id = (
            f"{self._dvr.entry.entry_id}_vloss_{self._channel_id}"
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._dvr.entry.entry_id)},
        }

    @property
    def is_on(self) -> bool:
        """Return true if video loss is detected."""
        return self.coordinator.data["channels"][self._channel_id]["vloss"]