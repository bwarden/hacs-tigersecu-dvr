"""Camera platform for the Tigersecu DVR integration."""
import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from . import TigersecuDVR
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tigersecu DVR camera platform."""
    dvr: TigersecuDVR = hass.data[DOMAIN][entry.entry_id]

    # Create a camera entity for each discovered channel
    cameras = [
        TigersecuCamera(dvr, channel_id) for channel_id in sorted(dvr.channels)
    ]
    async_add_entities(cameras)


class TigersecuCamera(CoordinatorEntity[DataUpdateCoordinator], Camera):
    """A camera entity for a Tigersecu DVR channel."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, dvr: TigersecuDVR, channel_id: int) -> None:
        """Initialize the camera."""
        CoordinatorEntity.__init__(self, dvr.coordinator)
        Camera.__init__(self)
        self._dvr = dvr
        self._channel_id = channel_id
        # The name will be like "Channel 1", "Channel 2", etc.
        self._attr_name = f"CH{self._channel_id + 1:02d}"
        self._attr_unique_id = f"{self._dvr.entry.entry_id}_channel_{self._channel_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._dvr.entry.entry_id)},
            "name": self._dvr.host,
            "manufacturer": "Tigersecu",
        }

    @property
    def available(self) -> bool:
        """Return True if the entity is available."""
        # The camera is unavailable if there is video loss.
        return not self.coordinator.data["channels"][self._channel_id]["vloss"]

    @property
    def is_recording(self) -> bool:
        """Return true if the camera is recording."""
        return self.coordinator.data["channels"][self._channel_id].get("record_type") != "None"

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return f"rtsp://{self._dvr.username}:{self._dvr.password}@{self._dvr.host}/main_{self._channel_id}?transport=tcp"
    
    @property
    def use_stream_for_stills(self) -> bool:
        """Return True if the stream should be used for still images."""
        return True