"""Update platform for the Tigersecu DVR integration."""

from __future__ import annotations

import logging

from homeassistant.components.update import UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TigersecuDVR
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tigersecu DVR update platform."""
    dvr: TigersecuDVR = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TigersecuUpdateEntity(dvr)])


class TigersecuUpdateEntity(CoordinatorEntity, UpdateEntity):
    """An update entity for the Tigersecu DVR."""

    _attr_has_entity_name = True
    _attr_name = "Update"

    def __init__(self, dvr: TigersecuDVR) -> None:
        """Initialize the update entity."""
        super().__init__(dvr.coordinator)
        self._dvr = dvr
        self._attr_unique_id = f"{self._dvr.entry.entry_id}_update"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._dvr.entry.entry_id)},
        }

    @property
    def installed_version(self) -> str | None:
        """Return the installed version."""
        # Version information is not currently available from the API
        return "Unknown"

    @property
    def in_progress(self) -> int | bool:
        """Return the update progress."""
        progress = self.coordinator.data.get("update_progress")
        if progress is not None and 0 <= progress < 100:
            return progress
        return False
