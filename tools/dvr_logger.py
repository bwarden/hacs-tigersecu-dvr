import asyncio
import logging
import argparse
import sys
import os
import getpass

import aiohttp

# Make the script runnable from anywhere by adding the library path.
# This avoids loading the component's __init__.py and its Home Assistant dependencies.
library_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "tigersecu_dvr")
)
sys.path.insert(0, library_path)

from pytigersecu import TigersecuDVRAPI

_LOGGER = logging.getLogger(__name__)


async def raw_xml_logger(xml_string: str):
    """Callback to log raw XML strings to stdout."""
    print(f"--- XML Payload ---\n{xml_string}\n-------------------")


async def raw_binary_logger(data: bytes):
    """Callback to log raw binary data to stdout."""
    print("--- Raw binary message ---")
    offset = 0
    chunk_size = 16
    while offset < len(data):
        chunk = data[offset : offset + chunk_size]

        # Format the address part
        addr = f"{offset:08x}"

        # Format the hex part with a double space in the middle
        hex_bytes = [f"{b:02x}" for b in chunk]
        hex_part1 = " ".join(hex_bytes[:8])
        hex_part2 = " ".join(hex_bytes[8:])
        # Pad to align columns if the chunk is smaller than 16 bytes
        hex_part_full = f"{hex_part1:<23s}  {hex_part2:<23s}"

        # Format the ASCII part
        ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)

        print(f"{addr}  {hex_part_full}  |{ascii_part}|")
        offset += chunk_size
    print("--------------------------")


async def raw_binary_dumper(data: bytes):
    """Callback to dump raw binary data directly to stdout."""
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


async def main():
    """Main function to run the DVR logger."""
    parser = argparse.ArgumentParser(
        description="Log incoming messages from a Tigersecu DVR."
    )
    parser.add_argument(
        "--host", required=True, help="Hostname or IP address of the DVR"
    )
    parser.add_argument(
        "--username", required=True, help="Username for DVR authentication"
    )
    parser.add_argument(
        "--password",
        help="Password for DVR authentication. If not provided, you will be prompted.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--log-binary",
        action="store_true",
        help="Log the raw binary message stream as hex",
    )
    parser.add_argument(
        "--raw-dump",
        action="store_true",
        help="Dump the raw, unprocessed binary stream to stdout. All other logs go to stderr.",
    )

    args = parser.parse_args()

    # If password was not provided, prompt for it securely.
    if not args.password:
        try:
            args.password = getpass.getpass("Enter DVR Password: ")
        except (EOFError, KeyboardInterrupt):
            print("\nPassword entry cancelled.")
            return

    # Determine logging stream. For raw dump, logs go to stderr.
    log_stream = sys.stderr if args.raw_dump else sys.stdout

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        stream=log_stream,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    _LOGGER.info("Starting DVR logger for %s", args.host)

    xml_callback = raw_xml_logger
    binary_callback = None

    if args.raw_dump:
        _LOGGER.info("Raw dump mode enabled. Binary data will be sent to stdout.")
        xml_callback = None  # Disable XML logging in raw dump mode
        binary_callback = raw_binary_dumper
    elif args.log_binary:
        binary_callback = raw_binary_logger

    session = aiohttp.ClientSession()
    api = TigersecuDVRAPI(
        host=args.host,
        username=args.username,
        password=args.password,
        session=session,
        update_callback=None,
        raw_xml_callback=xml_callback,
        raw_binary_callback=binary_callback,
    )

    try:
        await api.async_connect()
        _LOGGER.info("Connected to DVR. Logging messages...")
        # Keep the script running indefinitely
        await asyncio.Event().wait()
    except ConnectionError as e:
        _LOGGER.error("Failed to connect to DVR: %s", e)
    except asyncio.CancelledError:
        _LOGGER.info("Logger stopped.")
    finally:
        await api.async_disconnect()
        await session.close()
        _LOGGER.info("Disconnected from DVR.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _LOGGER.info("Logger interrupted by user.")
