"""Camera platform for the Tigersecu DVR integration."""

import asyncio
import logging
from time import monotonic

import aiohttp
from yarl import URL

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    cameras = [TigersecuCamera(dvr, channel_id) for channel_id in sorted(dvr.channels)]
    if cameras:
        _LOGGER.info("Adding %d camera entities", len(cameras))
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

        self._fetch_lock = asyncio.Lock()
        self._fetch_task: asyncio.Task | None = None
        self._last_image: bytes | None = None
        self._last_fetch_completion_time: float = 0.0

    @property
    def available(self) -> bool:
        """Return True if the entity is available."""
        # The camera is unavailable if there is video loss.
        return not self.coordinator.data["channels"][self._channel_id]["vloss"]

    @property
    def is_recording(self) -> bool:
        """Return true if the camera is recording."""
        return (
            self.coordinator.data["channels"][self._channel_id].get("record_type")
            != "None"
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()
            try:
                await self._fetch_task
            except asyncio.CancelledError:
                pass
        await super().async_will_remove_from_hass()

    async def _execute_fetch(self) -> None:
        """Execute the image fetch and update the cache."""
        # The DVR uses a self-signed certificate, so we must disable SSL verification.
        session = async_get_clientsession(self.hass, verify_ssl=False)
        url = f"https://{self._dvr.host}/cgi-bin/net_jpeg.cgi?ch={self._channel_id}"
        auth = aiohttp.BasicAuth(self._dvr.username, password=self._dvr.password)

        try:
            async with session.get(
                url, auth=auth, timeout=self._dvr.still_image_timeout
            ) as response:
                response.raise_for_status()
                self._last_image = await response.read()
                _LOGGER.debug(
                    "Successfully fetched new image for channel %d",
                    self._channel_id + 1,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "Error getting camera image for channel %d: %s",
                self._channel_id + 1,
                str(err) or type(err).__name__,
            )
        finally:
            self._last_fetch_completion_time = monotonic()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image for the camera."""
        task_to_wait_on: asyncio.Task | None = None

        async with self._fetch_lock:
            if self._fetch_task and not self._fetch_task.done():
                # A fetch is already in progress.
                task_to_wait_on = self._fetch_task
            else:
                # No fetch in progress. Check if we should start a new one.
                if monotonic() - self._last_fetch_completion_time < 10:
                    # It's too soon since the last fetch completed.
                    return self._last_image

                # Start a new fetch.
                self._fetch_task = self.hass.async_create_task(self._execute_fetch())
                task_to_wait_on = self._fetch_task

        if task_to_wait_on:
            try:
                # Wait up to 10 seconds for the fetch to complete.
                await asyncio.wait_for(asyncio.shield(task_to_wait_on), timeout=10)
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "Image fetch for channel %d is ongoing, returning cached image after 10s wait.",
                    self._channel_id + 1,
                )

        return self._last_image

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return str(
            URL.build(
                scheme="rtsp",
                user=self._dvr.username,
                password=self._dvr.password,
                host=self._dvr.host,
                path=f"/main_{self._channel_id}",
                query={"transport": "tcp"},
                fragment=f"timeout={self._dvr.rtsp_timeout}",
            )
        )

    @property
    def use_stream_for_stills(self) -> bool:
        """Return True if the stream should be used for still images."""
        return False
