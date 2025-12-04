"""API for communicating with a Tigersecu DVR."""

import asyncio
import base64
import logging
import ssl
from typing import Awaitable, Callable
from xml.etree import ElementTree as ET

import aiohttp
from aiohttp.hdrs import METH_GET
from multidict import CIMultiDict

_LOGGER = logging.getLogger(__name__)
MESSAGE_TIMEOUT = 60.0


class TigersecuDVRAPI:
    """A class to communicate with the Tigersecu DVR."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        update_callback: Callable[[dict], Awaitable[None]] = None,
    ):
        """Initialize the API."""
        self.host = host
        self.username = username
        self.password = password
        self._session = session
        self._update_callback = update_callback
        self._ws = None
        self._listen_task = None
        self._manager_task = None
        self._buffer = b""
        self._boundary = None
        self.channels = []
        self.connected = asyncio.Event()
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

    async def async_connect(self):
        """Establish and authenticate a persistent websocket connection."""
        # Start the connection manager task
        self._manager_task = asyncio.create_task(self._connection_manager())

    async def async_validate_connection(self):
        """Validate connection details without starting the full listener."""
        url = f"wss://{self.host}/streaming"
        _LOGGER.debug("Validating connection to {%s}", url)
        auth_str = base64.b64encode(f"{self.username}:{self.password}".encode()).decode(
            "ascii"
        )
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        ws = None
        try:
            ws = await self._session.ws_connect(
                url, protocols=("message",), ssl=ssl_context, timeout=10
            )
            await ws.send_str(f"Basic {auth_str}")
            # Wait for the two confirmation messages: "wsev" and "emsg".
            wsev_response = await ws.receive(timeout=10)
            wsev_data = wsev_response.data
            if wsev_response.type == aiohttp.WSMsgType.BINARY:
                wsev_data = wsev_data.decode("utf-8", errors="ignore")

            if (
                wsev_response.type
                not in (
                    aiohttp.WSMsgType.TEXT,
                    aiohttp.WSMsgType.BINARY,
                )
                or "wsev" not in wsev_data
            ):
                raise ConnectionError(
                    "Authentication failed during validation: 'wsev' confirmation not found"
                )

            # The second message is "emsg", we just need to receive it.
            emsg_response = await ws.receive(timeout=10)
            emsg_data = emsg_response.data
            if emsg_response.type == aiohttp.WSMsgType.BINARY:
                emsg_data = emsg_data.decode("utf-8", errors="ignore")

            if "emsg" not in emsg_data:
                raise ConnectionError(
                    "Authentication failed during validation: 'emsg' confirmation not found"
                )

            # The emsg contains the boundary for the multipart stream.
            boundary_marker = "boundary="
            boundary_start_index = emsg_data.find(boundary_marker)
            if boundary_start_index != -1:
                boundary_start = boundary_start_index + len(boundary_marker)
                # The boundary is terminated by a newline
                boundary_end_index = emsg_data.find("\r\n", boundary_start)
                if boundary_end_index == -1:
                    boundary_end_index = len(emsg_data)
            else:
                raise ConnectionError(
                    "Authentication failed: boundary not found in 'emsg' confirmation"
                )

        finally:
            if ws and not ws.closed:
                await ws.close()

    async def _connect_internal(self):
        _LOGGER.debug("Connecting to websocket at wss://%s/streaming", self.host)
        url = f"wss://{self.host}/streaming"
        auth_str = base64.b64encode(f"{self.username}:{self.password}".encode()).decode(
            "ascii"
        )

        # DVRs often use self-signed certificates
        # Use ssl.SSLContext() instead of ssl.create_default_context() to avoid
        # blocking I/O calls for loading default certs and paths, which are not needed.
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Establish the connection without any authentication headers.
        self._ws = await self._session.ws_connect(
            url, protocols=("message",), ssl=ssl_context, timeout=10
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

        if (
            wsev_response.type
            not in (
                aiohttp.WSMsgType.TEXT,
                aiohttp.WSMsgType.BINARY,
            )
            or "wsev" not in wsev_data
        ):
            _LOGGER.error(
                "Authentication failed: 'wsev' confirmation not found in message. Got: %s",
                wsev_response.data,
            )
            raise ConnectionError("Authentication failed on 'wsev' confirmation")

        # Second message is "emsg" and contains the boundary
        emsg_response = await self._ws.receive(timeout=10)
        emsg_data = emsg_response.data
        if emsg_response.type == aiohttp.WSMsgType.BINARY:
            emsg_data = emsg_data.decode("utf-8", errors="ignore")

        if "emsg" not in emsg_data:
            raise ConnectionError(
                "Authentication failed: 'emsg' confirmation not found"
            )

        boundary_marker = "boundary="
        boundary_start_index = emsg_data.find(boundary_marker)
        if boundary_start_index != -1:
            boundary_start = boundary_start_index + len(boundary_marker)
            # The boundary is terminated by a newline
            boundary_end_index = emsg_data.find("\r\n", boundary_start)
            if boundary_end_index == -1:
                boundary_end_index = len(emsg_data)
            self._boundary = emsg_data[boundary_start:boundary_end_index].encode()
            _LOGGER.debug("Found multipart boundary: %s", self._boundary)
        else:
            raise ConnectionError("Boundary not found in 'emsg' confirmation message")

        self.connected.set()
        _LOGGER.debug("Authentication successful in _connect_internal.")

    async def async_disconnect(self):
        """Disconnect from the DVR and clean up."""
        _LOGGER.debug("Disconnecting from DVR")
        if self._manager_task and not self._manager_task.done():
            self._manager_task.cancel()
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            # Wait for the listener task to finish cancelling
            try:
                await self._listen_task
            except asyncio.CancelledError:
                _LOGGER.debug("Listener task successfully cancelled during disconnect.")
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def _connection_manager(self):
        """Manages the connection and reconnection logic."""
        backoff_time = 1
        while True:
            try:
                await self._connect_internal()
                self.connected.set()
                _LOGGER.info("Successfully connected to DVR.")
                backoff_time = 1  # Reset backoff on successful connection
                await self._listen()  # This will run until the connection is lost

            except (aiohttp.ClientError, ConnectionError, asyncio.TimeoutError) as err:
                _LOGGER.warning(
                    "Connection to DVR lost: %s. Reconnecting in %d seconds.",
                    err,
                    backoff_time,
                )
            except asyncio.CancelledError:
                _LOGGER.debug("Connection manager cancelled.")
                break

            # Clean up before retrying
            if self._ws and not self._ws.closed:
                await self._ws.close()
            self.connected.clear()

            await asyncio.sleep(backoff_time)
            backoff_time = min(backoff_time * 2, 60)

    async def _listen(self):
        """Listen for incoming messages from the websocket."""
        _LOGGER.debug("Websocket listener started")
        self._buffer = b""

        while not self._ws.closed:
            msg = await self._ws.receive(timeout=MESSAGE_TIMEOUT)

            # The DVR sends a non-standard multipart stream as binary data.
            if msg.type == aiohttp.WSMsgType.BINARY:
                self._process_binary_message(msg.data)

            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                raise ConnectionError(
                    f"Websocket connection closed or errored: {msg.type}"
                )

    def _process_binary_message(self, data: bytes):
        """Process a binary message from the websocket."""
        self._buffer += data
        try:
            if not self._boundary:
                _LOGGER.warning("Boundary not set, cannot process binary stream.")
                return

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
                            xml_data_str = xml_data_bytes.strip(b" \r\n\x00").decode(
                                "utf-8", errors="ignore"
                            )
                            if xml_data_str:
                                self._process_xml_triggers(xml_data_str)
        except Exception as e:
            _LOGGER.error("Error parsing binary event stream: %s", e)

    def _process_xml_triggers(self, xml_string: str):
        """Parse XML triggers and dispatch them via the callback."""
        if not xml_string or not self._update_callback:
            return

        # The stream sends multiple <Trigger.../> elements concatenated together,
        # which is not valid XML. We wrap it to create a valid document.
        try:
            root = ET.fromstring(f"<root>{xml_string}</root>")
        except ET.ParseError as e:
            _LOGGER.error("Error parsing XML trigger data: %s", e)
            return

        # A burst of VideoInput events indicates the initial channel discovery.
        # We can use this to signal that all channels have been found.
        video_inputs_in_message = [
            t for t in root if t.tag == "Trigger" and t.get("Event") == "VideoInput"
        ]

        if len(video_inputs_in_message) > 1:
            discovered_channels = [int(t.get("CH")) for t in video_inputs_in_message]
            self._emit(
                {"event": "channels_discovered", "channels": discovered_channels}
            )

        for trigger in root:
            if trigger.tag == "Trigger" and trigger.attrib:
                # For SMART events, we need the child elements, so pass the full element.
                self._dispatch_trigger(trigger)

    def _dispatch_trigger(self, trigger: ET.Element):
        """Dispatch a trigger element to the appropriate handler in the API."""
        event_type = trigger.get("Event")
        if not event_type:
            _LOGGER.debug(
                "Received a trigger with no Event attribute: %s", ET.tostring(trigger)
            )
            return

        handler = self._trigger_handlers.get(event_type)
        if handler:
            handler(trigger)
        else:
            _LOGGER.debug("No handler for event type '%s'", event_type)

    def _emit(self, data: dict):
        """Emit data via the callback."""
        if self._update_callback:
            asyncio.create_task(self._update_callback(data))

    def _handle_motion_event(self, trigger: ET.Element):
        """Handle a motion detection event and emit structured data."""
        try:
            motion_mask = int(trigger.get("Value", "0"))
            for channel_id in self.channels:
                is_motion = bool(motion_mask & (1 << channel_id))
                self._emit(
                    {"event": "motion", "channel": channel_id, "state": is_motion}
                )
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid Motion event: %s", trigger.attrib)

    def _handle_vloss_event(self, trigger: ET.Element):
        """Handle a video loss event and emit structured data."""
        try:
            vloss_mask = int(trigger.get("Value", "0"))
            for channel_id in self.channels:
                is_vloss = bool(vloss_mask & (1 << channel_id))
                self._emit({"event": "vloss", "channel": channel_id, "state": is_vloss})
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid VLOSS event: %s", trigger.attrib)

    def _handle_disk_event(self, trigger: ET.Element):
        """Handle a disk status event."""
        self._emit({"event": "disk", "data": trigger.attrib})

    def _handle_login_event(self, trigger: ET.Element):
        """Handle a user login event."""
        self._emit({"event": "login", "data": trigger.attrib})

    def _handle_network_event(self, trigger: ET.Element):
        """Handle a network status event."""
        self._emit({"event": "network", "data": trigger.attrib})

    def _handle_smart_event(self, trigger: ET.Element):
        """Handle a disk SMART status event."""
        try:
            disk_id = int(trigger.get("ID"))
            attributes = {}
            for attr in trigger:
                if attr.tag == "Attribute":
                    attr_id = int(attr.get("ID"))
                    attributes[attr_id] = attr.attrib
            self._emit(
                {
                    "event": "smart",
                    "disk_id": disk_id,
                    "attributes": attributes,
                }
            )
        except (ValueError, KeyError, TypeError):
            _LOGGER.warning("Received invalid SMART event: %s", trigger.attrib)

    def _handle_record_event(self, trigger: ET.Element):
        """Handle a record status event."""
        try:
            channel_id = int(trigger.get("ID"))
            record_type = trigger.get("Type")
            self._emit(
                {
                    "event": "record",
                    "channel": channel_id,
                    "type": record_type,
                }
            )
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid Record event: %s", trigger.attrib)

    def _handle_video_input_event(self, trigger: ET.Element):
        """Handle a video input discovery event."""
        try:
            channel_id = int(trigger.get("CH"))
            if channel_id not in self.channels:
                self.channels.append(channel_id)
            self._emit({"event": "video_input", "data": trigger.attrib})
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid VideoInput event: %s", trigger.attrib)
