"""
Provides a standalone test framework for the TigersecuDVR class.

This script allows for testing the connection and message stream from a Tigersecu
DVR without running inside the full Home Assistant environment. It is useful for
development, debugging, and verifying device communication.

Usage:
1. Set the required environment variables:
   - DVR_HOST: The IP address or hostname of your DVR.
   - DVR_USERNAME: The username for your DVR.
   - DVR_PASSWORD: The password for your DVR.

2. Run the script from your terminal:
   python3 standalone_test.py

The script will connect to the DVR, authenticate, and print any received
XML data to the console. Press Ctrl+C to disconnect and exit gracefully.
"""
import asyncio
import logging
import os
import signal

import aiohttp

# Import the standalone API class
from api import TigersecuDVRAPI


async def on_data_received(event_data: dict):
    """Callback function to simply print received structured event data."""
    logging.info("--- EVENT DATA RECEIVED ---: %s", event_data)

async def main():
    """Main function to run the standalone test."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    host = os.getenv("DVR_HOST")
    username = os.getenv("DVR_USERNAME")
    password = os.getenv("DVR_PASSWORD")

    if not all([host, username, password]):
        logging.error(
            "Missing required environment variables. "
            "Please set DVR_HOST, DVR_USERNAME, and DVR_PASSWORD."
        )
        return

    # The API class needs an aiohttp.ClientSession
    async with aiohttp.ClientSession() as session:
        api = TigersecuDVRAPI(
            host, username, password, session, update_callback=on_data_received
        )
        await run_test(api)

async def run_test(api: TigersecuDVRAPI):
    """Connect to the API and wait for shutdown signal."""
    shutdown_event = asyncio.Event()

    def _signal_handler(*_):
        """Signal handler to trigger graceful shutdown."""
        logging.info("Shutdown signal received, disconnecting...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, _signal_handler)
    loop.add_signal_handler(signal.SIGTERM, _signal_handler)

    try:
        await api.async_connect()
        logging.info("Connection successful. Listening for messages... (Press Ctrl+C to exit)")
        await shutdown_event.wait()
    finally:
        await api.async_disconnect()
        logging.info("Disconnected from DVR.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Exiting.")