"""Config flow for Tigersecu DVR integration."""

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .pytigersecu import TigersecuDVRAPI
from .const import CONF_RTSP_TIMEOUT, DEFAULT_RTSP_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_RTSP_TIMEOUT, default=DEFAULT_RTSP_TIMEOUT): int,
    }
)


async def validate_input(hass: HomeAssistant, data: dict) -> None:
    """Validate the user input allows us to connect."""
    session = async_get_clientsession(hass)
    api = TigersecuDVRAPI(
        host=data[CONF_HOST],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        session=session,
    )
    try:
        # Use the dedicated validation method which connects, authenticates, and disconnects.
        await api.async_validate_connection()
    except asyncio.TimeoutError as err:
        raise ConnectionError("Connection timed out") from err


class TigersecuDVRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tigersecu DVR."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
            except (aiohttp.ClientError, ConnectionError):
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the reconfiguration step."""
        errors = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            if entry.state == config_entries.ConfigEntryState.LOADED:
                await self.hass.config_entries.async_unload(entry.entry_id)

            try:
                await validate_input(self.hass, user_input)
            except (aiohttp.ClientError, ConnectionError):
                errors["base"] = "cannot_connect"
                await self.hass.config_entries.async_reload(entry.entry_id)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
                await self.hass.config_entries.async_reload(entry.entry_id)
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                vol.Required(CONF_PASSWORD, default=entry.data[CONF_PASSWORD]): str,
                vol.Optional(
                    CONF_RTSP_TIMEOUT,
                    default=entry.data.get(CONF_RTSP_TIMEOUT, DEFAULT_RTSP_TIMEOUT),
                ): int,
            }
        )

        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )
