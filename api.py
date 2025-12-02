"""API for communicating with a Tigersecu DVR."""
import asyncio
import base64
import logging
import ssl
from typing import Awaitable, Callable

import aiohttp
from aiohttp.hdrs import METH_GET
from multidict import CIMultiDict

_LOGGER = logging.getLogger(__name__)


class TigersecuDVRAPI:
    """A class to communicate with the Tigersecu DVR."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        update_callback: Callable[[str], Awaitable[None]] = None,
    ):
        """Initialize the API."""
        self.host = host
        self.username = username
        self.password = password
        self._session = session
        self._update_callback = update_callback
        self._ws = None
        self._listen_task = None
        self._buffer = b""
        self._boundary = None

    async def async_connect(self):
        """Establish and authenticate a persistent websocket connection."""
        _LOGGER.debug("Connecting to websocket at wss://%s/streaming", self.host)
        url = f"wss://{self.host}/streaming"
        auth_str = base64.b64encode(f"{self.username}:{self.password}".encode()).decode(
            "ascii"
        )

        # DVRs often use self-signed certificates
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        try:
            # Establish the connection without any authentication headers.
            self._ws = await self._session.ws_connect(
                url, protocols=("message",), ssl=ssl_context
            )

            # Send the authentication string as the first message.
            _LOGGER.debug("Websocket connected, sending auth string")
            auth_message = f"Basic {auth_str}"
            await self._ws.send_str(auth_message)

            # Wait for the two confirmation messages from the DVR: "wsev" and "emsg".
            _LOGGER.debug("Waiting for authentication confirmation messages.")

            # First message should be "wsev"
            wsev_response = await self._ws.receive(timeout=10)
            wsev_data = wsev_response.data
            if wsev_response.type == aiohttp.WSMsgType.BINARY:
                # Decode with errors ignored, as the message may be wrapped in other binary data
                wsev_data = wsev_data.decode("utf-8", errors="ignore")

            if wsev_response.type not in (
                aiohttp.WSMsgType.TEXT,
                aiohttp.WSMsgType.BINARY,
            ) or "wsev" not in wsev_data:
                _LOGGER.error(
                    "Authentication failed: 'wsev' confirmation not found in message. Got: %s",
                    wsev_response.data,
                )
                await self.async_disconnect()
                raise ConnectionError("Authentication failed on 'wsev' confirmation")

            _LOGGER.debug("Authentication successful, starting listener")
            self._listen_task = asyncio.create_task(self._listen())

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Failed to connect or authenticate with DVR: %s", err)
            await self.async_disconnect()
            raise ConnectionError("Failed to connect to DVR") from err

    async def async_disconnect(self):
        """Disconnect from the DVR and clean up."""
        _LOGGER.debug("Disconnecting from DVR")
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def _listen(self):
        """Listen for incoming messages from the websocket."""
        _LOGGER.debug("Websocket listener started")
        self._buffer = b""
        self._boundary = None

        async for msg in self._ws:
            # The DVR sends a non-standard multipart stream as binary data.
            if msg.type == aiohttp.WSMsgType.BINARY:
                self._buffer += msg.data
                try:
                    # The boundary is defined once in an HTTP response at the start of the stream.
                    if not self._boundary:
                        boundary_marker = b"boundary="
                        boundary_start_index = self._buffer.find(boundary_marker)
                        if boundary_start_index != -1:
                            boundary_end_index = self._buffer.find(
                                b"\r\n", boundary_start_index
                            )
                            if boundary_end_index != -1:
                                self._boundary = self._buffer[
                                    boundary_start_index
                                    + len(boundary_marker) : boundary_end_index
                                ]
                                _LOGGER.debug("Found multipart boundary: %s", self._boundary)
                        else:
                            # Wait for more data if boundary not found yet
                            continue

                    # Split the buffer by the boundary to get the individual parts
                    parts = self._buffer.split(self._boundary)
                    # The last part might be incomplete, so we keep it in the buffer.
                    self._buffer = parts.pop()

                    if parts:
                        for part in parts:
                            # Each valid part contains 'Content-Type: text/plain'
                            content_type_marker = b"Content-Type: text/plain"
                            content_type_index = part.find(content_type_marker)
                            if content_type_index != -1:
                                # The XML data starts after the marker and the following CRLF
                                xml_start_index = part.find(b"\r\n", content_type_index)
                                if xml_start_index != -1:
                                    # Skip the CRLF itself
                                    xml_data_bytes = part[xml_start_index + 2 :]
                                    xml_data_str = xml_data_bytes.strip(
                                        b" \r\n\x00"
                                    ).decode("utf-8", errors="ignore")
                                    if xml_data_str:
                                        _LOGGER.debug(
                                            "Parsed XML data from event: %s", xml_data_str
                                        )
                                        if self._update_callback:
                                            asyncio.create_task(
                                                self._update_callback(xml_data_str)
                                            )
                except Exception as e:
                    _LOGGER.error("Error parsing binary event stream: %s", e)


            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                _LOGGER.warning(
                    "Websocket connection closed or errored, breaking listener loop"
                )
                break