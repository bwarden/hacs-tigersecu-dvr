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
    print(f"--- RAW XML TRIGGER ---\n{xml_string}\n-----------------------")


async def raw_binary_logger(data: bytes):
    """Callback to log raw binary data to stdout."""
    print(f"--- RAW BINARY DATA ---\n{data.hex()}\n-----------------------")


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

    args = parser.parse_args()

    # If password was not provided, prompt for it securely.
    if not args.password:
        try:
            args.password = getpass.getpass("Enter DVR Password: ")
        except (EOFError, KeyboardInterrupt):
            print("\nPassword entry cancelled.")
            return

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        stream=sys.stdout,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    _LOGGER.info("Starting DVR logger for %s", args.host)

    binary_callback = raw_binary_logger if args.log_binary else None

    session = aiohttp.ClientSession()
    api = TigersecuDVRAPI(
        host=args.host,
        username=args.username,
        password=args.password,
        session=session,
        # We don't need the structured data callback for this tool
        update_callback=None,
        raw_xml_callback=raw_xml_logger,
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
