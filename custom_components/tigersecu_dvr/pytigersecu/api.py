"""API for communicating with a Tigersecu DVR."""

import asyncio
import base64
import logging
import ssl
from typing import Awaitable, Callable
from email.parser import BytesParser
from xml.etree import ElementTree as ET

import aiohttp

_LOGGER = logging.getLogger(__name__)
MESSAGE_TIMEOUT = 60.0


class AuthenticationError(Exception):
    """Exception raised for authentication failures."""


class TigersecuDVRAPI:
    """A class to communicate with the Tigersecu DVR."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        update_callback: Callable[[dict], Awaitable[None]] = None,
        raw_xml_callback: Callable[[str], Awaitable[None]] = None,
        raw_binary_callback: Callable[[bytes], Awaitable[None]] = None,
    ):
        """Initialize the API."""
        self.host = host
        self.username = username
        self.password = password
        self._session = session
        self._update_callback = update_callback
        self._raw_xml_callback = raw_xml_callback
        self._raw_binary_callback = raw_binary_callback
        self._ws = None
        self._listen_task = None
        self._manager_task = None
        self._authenticated = False
        self._buffer = b""
        self._desync_count = 0
        self._emsg_header = None  # To store the 64-byte emsg header
        self._boundary = None
        self.channels = []
        self.discovered_sensors = set()
        self.connected = asyncio.Event()
        self._trigger_handlers = {
            "Motion": self._handle_motion_event,
            "VLOSS": self._handle_vloss_event,
            "Disk": self._handle_disk_event,
            "Record": self._handle_record_event,
            "Login": self._handle_login_event,
            "Network": self._handle_network_event,
            "SMART": self._handle_smart_event,
            "Scheme": self._handle_scheme_event,
            "Sensor": self._handle_sensor_event,
            "VideoInput": self._handle_video_input_event,
            "DateTime": self._handle_datetime_event,
        }

        # Handled Events:
        # <Trigger Event="DateTime" Value="1764907307" TZ="..."/>
        # <Trigger Event="Disk" ID="0" Num="1" Model="WDC WD10PURX-64E" Status="Record" Flag="OVWR" Capacity="1000203091968" Available="75497472" Error="0"/>
        # <Trigger Event="Login" User="admin" From="127.0.0.1"/>
        # <Trigger Event="Motion" Value="525" Status="17407"/>
        # <Trigger Event="Network" Link="True" IP="172.16.4.13" MAC="DE:AD:BE:EF:AA:55" DHCP_Gateway="172.16.4.1" DHCP_Netmask="255.255.255.0" GIP="1.1.1.1" SPD="100"/>
        # <Trigger Event="Record" ID="0" Type="None"/>
        # <Trigger Event="Scheme" ID="2"/>
        # <Trigger Event="Sensor" Value="5" Status="0"/>
        # <Trigger Event="SMART" ID="0" Num="1" Serial="NONE">
        #   <Attribute ID="1" Value="200" Worst="200" Thresh="51" RAW="070000000000"/>
        #   ...
        # </Trigger>
        # <Trigger Event="VideoInput" CH="0" Chip="RN6264" Format="NTSC"/>
        # <Trigger Event="VLOSS" Value="23552" Status="24576"/>

        # Seen but Unhandled Events:
        # <Trigger Event="ConfigChange" Value="service.xml"/>
        #   - Indicates a configuration file on the DVR has changed.
        #
        # <Trigger Event="ErrorAuthorization" Value="0"/>
        #   - Seen on failed login. The connection is dropped, so no specific handling is needed.
        #
        # <Trigger Event="Logout" User="admin" From="127.0.0.1"/>
        #   - Indicates a user logged out from the web UI.
        #
        # <Trigger Event="SendRemote" />
        #   - Purpose unknown.
        #
        # <Trigger Event="SrvFd" Fd="44"/>
        #   - Purpose unknown, seems to be an internal file descriptor.

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
            # Just send the auth string. If it's invalid, the DVR will close the connection.
            await ws.send_str(f"Basic {auth_str}")

            # Try to read a message to confirm connection is accepted.
            try:
                msg = await ws.receive(timeout=5)
                if msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    raise AuthenticationError("Connection rejected by DVR.")
            except asyncio.TimeoutError:
                if ws.closed:
                    raise AuthenticationError("Connection rejected by DVR (timeout).")

        finally:
            if ws and not ws.closed:
                await ws.close()

    async def _connect_internal(self):
        _LOGGER.debug("Connecting to websocket at wss://%s/streaming", self.host)
        self._authenticated = False
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
        self._auth_message = auth_message
        await self._ws.send_str(auth_message)

        # Reset buffer for the new connection
        self._buffer = b""

        # Validate auth by reading the first message
        try:
            msg = await self._ws.receive(timeout=5)
            if msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                if not self._authenticated:
                    raise AuthenticationError("Authentication failed")

            if msg.type == aiohttp.WSMsgType.BINARY:
                self._process_binary_message(msg.data)
        except asyncio.TimeoutError:
            if self._ws.closed and not self._authenticated:
                raise AuthenticationError("Authentication failed")
            # If open, we just timed out waiting for the first message. Proceed to listen.

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

            except AuthenticationError:
                _LOGGER.error("Authentication failed. Aborting connection attempts.")
                break
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

        while not self._ws.closed:
            try:
                msg = await self._ws.receive(timeout=MESSAGE_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "No message received in %s seconds. Buffer content: %s",
                    MESSAGE_TIMEOUT,
                    self._buffer[:32].hex(),
                )
                raise ConnectionError("Websocket timeout")

            # The DVR sends a non-standard multipart stream as binary data.
            if msg.type == aiohttp.WSMsgType.BINARY:
                self._process_binary_message(msg.data)

            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                if not self._authenticated:
                    raise AuthenticationError(
                        "Connection closed without valid message (Authentication Failed)"
                    )
                raise ConnectionError(
                    f"Websocket connection closed or errored: {msg.type}"
                )

    def _process_binary_message(self, data: bytes):
        """Process a binary message from the websocket."""
        # The DVR sends a stream of messages, each prefixed with a 4-byte identifier.
        if self._raw_binary_callback:
            asyncio.create_task(self._raw_binary_callback(data))

        self._buffer += data

        while len(self._buffer) >= 4:
            prefix = self._buffer[:4].decode("ascii", errors="ignore")

            if prefix in ("erqu", "erco"):
                _LOGGER.error(
                    "Received '%s' message, connection is invalid. Reconnecting. Buffer: %s",
                    prefix,
                    self._buffer[:32].hex(),
                )
                # This will cause the _listen loop to exit and the manager to reconnect.
                raise ConnectionError(f"Received '{prefix}' from DVR")

            if prefix == "wsev":
                _LOGGER.debug("Received 'wsev' acknowledgment.")
                self._authenticated = True
                # This is just an acknowledgment. Consume the 4 bytes and continue processing.
                self._buffer = self._buffer[4:]
                continue

            if prefix == "emsg":
                self._authenticated = True
                # An 'emsg' is followed by 64 bytes of binary data, then the text payload.
                if len(self._buffer) < 68:  # 4 bytes for 'emsg' + 64 bytes for header
                    _LOGGER.debug(
                        "Incomplete 'emsg' received, waiting for more data. Buffer: %s",
                        self._buffer[:32].hex(),
                    )
                    return  # Wait for the rest of the message

                # An 'emsg' block contains HTTP-like headers and a payload.
                # Find the end of the headers (double CRLF).
                headers_end_pos = self._buffer.find(b"\r\n\r\n", 68)
                if headers_end_pos == -1:
                    _LOGGER.debug(
                        "Incomplete 'emsg' headers received, waiting for more data."
                    )
                    return

                header_bytes = self._buffer[68:headers_end_pos]
                payload_start_pos = headers_end_pos + 4

                # The DVR sometimes includes an HTTP status line before the headers.
                # If present, strip it out before parsing.
                if header_bytes.lstrip().startswith(b"HTTP/"):
                    http_end_pos = header_bytes.find(b"\n")
                    if http_end_pos != -1:
                        _LOGGER.debug(
                            "HTTP status from emsg headers: %s",
                            header_bytes[:http_end_pos].strip().decode(errors="ignore"),
                        )
                        header_bytes = header_bytes[http_end_pos + 1 :]

                # The first emsg defines the boundary for the multipart stream.
                # We check for this case first.
                header_str = header_bytes.decode("utf-8", errors="ignore")
                headers = BytesParser().parsebytes(header_bytes)
                content_type = headers.get("content-type")

                if not self._boundary and "boundary=" in header_str:
                    if content_type:
                        # The boundary value in the header might be quoted
                        new_boundary = (
                            content_type.split("boundary=")[1].strip().strip('"')
                        )
                        self._boundary = new_boundary.encode()
                        _LOGGER.debug("Updated multipart boundary: %s", self._boundary)

                if not self._boundary:
                    _LOGGER.error("No boundary defined, cannot process emsg payload.")
                    # Discard the message to avoid getting stuck
                    self._buffer = self._buffer[payload_start_pos:]
                    continue

                # The payload is terminated by the boundary.
                boundary_pos = self._buffer.find(self._boundary, payload_start_pos)
                if boundary_pos == -1:
                    _LOGGER.debug("Incomplete 'emsg' payload, waiting for more data.")
                    return

                payload = self._buffer[payload_start_pos:boundary_pos]

                if content_type and "text/plain" in content_type:
                    self._process_multipart_payload(payload)

                # Consume the processed message from the buffer, including the boundary.
                # The boundary might have a trailing '--' and is followed by CRLF.
                end_of_message = self._buffer.find(b"\r\n", boundary_pos)
                if end_of_message == -1:
                    # Should not happen if we found the boundary, but as a fallback...
                    end_of_message = boundary_pos + len(self._boundary)

                self._buffer = self._buffer[end_of_message + 2 :]
                continue

            # If we get here, we have an unknown prefix.
            _LOGGER.warning(
                "Unknown message prefix '%s', discarding one byte and retrying. Buffer: %s",
                prefix,
                self._buffer[:32].hex(),
            )
            self._buffer = self._buffer[1:]

    def _parse_boundary_from_emsg(self, emsg_data: str):
        """Extract the multipart boundary from the initial emsg payload."""
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
            _LOGGER.error(
                "Boundary not found in initial 'emsg' message. Data: %s", emsg_data
            )

    def _process_multipart_payload(self, payload: bytes):
        """Process a multipart MIME payload containing XML triggers."""
        if not self._boundary:
            _LOGGER.warning("Boundary not set, cannot process multipart payload.")
            return
        try:
            # The payload is the XML content itself.
            xml_data_str = payload.strip(b" \r\n\x00").decode("utf-8", errors="ignore")
            if xml_data_str:
                self._process_xml_triggers(xml_data_str)
        except Exception as e:
            _LOGGER.error(
                "Error parsing multipart payload: %s. Payload: %s", e, payload.hex()
            )

    def _process_xml_triggers(self, xml_string: str):
        """Parse XML triggers and dispatch them via the callback."""
        if self._raw_xml_callback:
            # Log the raw XML string for debugging purposes
            asyncio.create_task(self._raw_xml_callback(xml_string))

        if not xml_string or not self._update_callback:
            return

        # The stream sends multiple <Trigger.../> elements concatenated together,
        # which is not valid XML. We wrap it to create a valid document.
        try:
            root = ET.fromstring(f"<root>{xml_string}</root>")
        except ET.ParseError as e:
            _LOGGER.error("Error parsing XML trigger data: %s. XML: %s", e, xml_string)
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
            _LOGGER.debug("No handler for event type '%s'; ignoring.", event_type)

    def _emit(self, data: dict):
        """Emit data via the callback."""
        if self._update_callback:
            asyncio.create_task(self._update_callback(data))

    def _handle_motion_event(self, trigger: ET.Element):
        """Handle a motion detection event and emit structured data."""
        # <Trigger Event="Motion" Value="525" Status="17407"/>
        # Value is a bitmask of channels with motion.
        # I don't know what Status represents.
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
        # <Trigger Event="VLOSS" Value="23552" Status="24576"/>
        # Value is a bitmask of channels with video loss.
        # I don't know what Status represents.
        try:
            vloss_mask = int(trigger.get("Value", "0"))
            for channel_id in self.channels:
                is_vloss = bool(vloss_mask & (1 << channel_id))
                self._emit({"event": "vloss", "channel": channel_id, "state": is_vloss})
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid VLOSS event: %s", trigger.attrib)

    def _handle_sensor_event(self, trigger: ET.Element):
        """Handle a sensor event and emit structured data."""
        # <Trigger Event="Sensor" Value="5" Status="0"/>
        # Value is a bitmask of sensors that are active.
        # I don't know what Status represents.
        try:
            sensor_mask = int(trigger.get("Value", "0"))
            # Unlike Motion or VLOSS, the sensor bitmask is not tied to channels.
            # Each bit represents a distinct sensor. We assume
            # that if sensor 'N' is asserted, sensors 0 through N all exist.
            if sensor_mask > 0:
                # Find the highest asserted sensor ID.
                max_asserted_id = sensor_mask.bit_length() - 1
                # Add all sensors up to that ID to our discovered set.
                for i in range(max_asserted_id + 1):
                    self.discovered_sensors.add(i)

            # Iterate over a copy of the set, as it could be modified elsewhere.
            for sensor_id in self.discovered_sensors.copy():
                is_sensor_active = bool(sensor_mask & (1 << sensor_id))
                self._emit(
                    {
                        "event": "sensor",
                        "sensor_id": sensor_id,
                        "state": is_sensor_active,
                    }
                )
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid Sensor event: %s", trigger.attrib)

    def _handle_disk_event(self, trigger: ET.Element):
        """Handle a disk status event."""
        # <Trigger Event="Disk" ID="0" Num="1" Model="WDC WD10PURX-64E" Status="Record" Flag="OVWR" Capacity="1000203091968" Available="75497472" Error="0"/>
        # ID and Num likely differentiate a secondary disk.
        # Status is a string like "Record" or "Idle"; just indicates whether the DVR is actually writing to this disk.
        # OVWR means overwrite mode is enabled; freeing up space by deleting old footage.
        self._emit({"event": "disk", "data": trigger.attrib})

    def _handle_login_event(self, trigger: ET.Element):
        """Handle a user login event."""
        # <Trigger Event="Login" User="admin" From="127.0.0.1"/>
        # Indicates someone else has logged in.
        # From isn't usually the actual source IP.
        self._emit({"event": "login", "data": trigger.attrib})

    def _handle_network_event(self, trigger: ET.Element):
        """Handle a network status event."""
        # <Trigger Event="Network" Link="True" IP="172.16.4.13" MAC="DE:AD:BE:EF:AA:55" DHCP_Gateway="172.16.4.1" DHCP_Netmask="255.255.255.0" GIP="1.1.1.1" SPD="100"/>
        # Issued when you connect; probably gets reissued on DHCP renewals.
        # GIP is your detected external IP address, via service on the DVR itself.
        # SPD is the link speed in Mbps.
        # Static network configuration might show up differently.
        self._emit({"event": "network", "data": trigger.attrib})

    def _handle_smart_event(self, trigger: ET.Element):
        """Handle a disk SMART status event."""
        # <Trigger Event="SMART" ID="0" Num="1" Serial="NONE">
        # <Attribute ID="1" Value="200" Worst="200" Thresh="51" RAW="070000000000"/>
        # <Attribute ID="3" Value="125" Worst="123" Thresh="21" RAW="751200000000"/>
        # ...and so on...
        # ID and Num probably match the attributes in the Disk event.
        # Each Attribute child contains SMART attribute data.
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

    def _handle_scheme_event(self, trigger: ET.Element):
        """Handle a disk scheme event."""
        # <Trigger Event="Scheme" ID="2"/>
        # Indicates a change in the disk scheme, e.g., overwrite mode changed.
        # Probably redundant with the Flag attribute in the Disk event, but this event usually
        # is sent along with the Record events.
        try:
            scheme_id = int(trigger.get("ID"))
            self._emit({"event": "scheme", "id": scheme_id})
        except (ValueError, KeyError, TypeError):
            _LOGGER.warning("Received invalid Scheme event: %s", trigger.attrib)

    def _handle_record_event(self, trigger: ET.Element):
        """Handle a record status event."""
        # <Trigger Event="Record" ID="0" Type="None"/>
        # <Trigger Event="Record" ID="1" Type="None"/>
        # ...and so on for each channel -- all sent regularly every few seconds.
        # Type is a string like None, Event (motion), or Conti (continuous). May be others.
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
        # <Trigger Event="VideoInput" CH="0" Chip="RN6264" Format="NTSC"/>
        # <Trigger Event="VideoInput" CH="1" Chip="RN6264" Format="NTSC"/>
        # <Trigger Event="VideoInput" CH="2" Chip="RN6264" Format="NTSC"/>
        # <Trigger Event="VideoInput" CH="3" Chip="RN6264" Format="NTSC"/>
        # ...and so on for each channel.
        # Usually sent just at the beginning of the connection. This is how we discover channels.
        try:
            channel_id = int(trigger.get("CH"))
            if channel_id not in self.channels:
                self.channels.append(channel_id)
            self._emit({"event": "video_input", "data": trigger.attrib})
        except (ValueError, KeyError):
            _LOGGER.warning("Received invalid VideoInput event: %s", trigger.attrib)

    def _handle_datetime_event(self, trigger: ET.Element):
        """Handle a DateTime event."""
        # <Trigger Event="DateTime" Value="1764907307" TZ="..."/>
        try:
            timestamp = int(trigger.get("Value"))
            self._emit({"event": "datetime", "timestamp": timestamp})
        except (ValueError, KeyError, TypeError):
            _LOGGER.warning("Received invalid DateTime event: %s", trigger.attrib)
