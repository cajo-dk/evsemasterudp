"""Config flow for the EVSE Master UDP integration"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .evse_client import get_evse_client

_LOGGER = logging.getLogger(__name__)

DOMAIN = "evsemasterudp"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("serial"): str,
        vol.Required("password"): str,
        vol.Optional("host"): str,
        vol.Optional("port", default=28376): int,
        # Default friendly base name (avoids huge serial in entity names)
        vol.Optional("name", default="EVSEMaster"): str,
    }
)

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configuration flow manager for EVSE EmProto"""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user configuration step"""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                # Validate configuration
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidHost:
                errors["base"] = "invalid_host"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                # Create the configuration entry
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


async def _wait_for_discovery_refresh(
    serial: str, client, expected_host: str, expected_port: int, timeout: float = 5.0
) -> None:
    """Give the communicator a chance to learn the EVSE reply port from broadcasts."""
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        evse = client.get_evse(serial)
        if evse and (
            evse.get("ip") != expected_host or evse.get("port") != expected_port
        ):
            break
        await asyncio.sleep(0.1)

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate user input data"""
    serial = data["serial"]
    password = data["password"]
    host = data.get("host")
    port = data.get("port", 28376)

    if host:
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError as err:
            raise InvalidHost from err

    # Get the EVSE client
    client = get_evse_client()

    # Start the client temporarily for testing
    was_running = client.running
    if not was_running:
        try:
            await client.start()
        except Exception as err:
            _LOGGER.error(f"Unable to start EVSE client: {err}")
            raise CannotConnect

    try:
        import asyncio

        if host:
            client.ensure_evse(serial, host, port)
            await _wait_for_discovery_refresh(serial, client, host, port)
            _LOGGER.info(f"Using configured EVSE endpoint {serial} @ {host}:{port}")
        else:
            # Wait longer to discover EVSEs (like test_full.py)
            await asyncio.sleep(5)

        # Check if EVSE is found - Retry on failure
        evse = client.get_evse(serial)
        if not evse:
            if host:
                _LOGGER.error(f"Configured EVSE {serial} @ {host}:{port} was not registered")
                raise CannotConnect

            # Retry after 2 additional seconds
            _LOGGER.warning(f"EVSE {serial} not found, retrying...")
            await asyncio.sleep(2)
            evse = client.get_evse(serial)

        if not evse:
            _LOGGER.error(f"EVSE {serial} not found after 7 seconds")
            raise CannotConnect

        _LOGGER.info(f"EVSE {serial} found, attempting connection...")

        # Test connection with retry
        success = await client.login(serial, password)
        if not success:
            # Only one retry to avoid blocking the EVSE
            _LOGGER.warning(f"First auth attempt failed for {serial}, retrying...")
            await asyncio.sleep(2)
            success = await client.login(serial, password)

        if not success:
            raise InvalidAuth

        _LOGGER.info(f"Successfully connected to EVSE {serial}")

        return {
            "title": f"EVSE {serial}",
            "serial": serial,
        }

    finally:
        # Stop the client if it was not started before
        if not was_running:
            await client.stop()

class CannotConnect(HomeAssistantError):
    """Error indicating that connection could not be established"""

class InvalidAuth(HomeAssistantError):
    """Error indicating invalid authentication"""


class InvalidHost(HomeAssistantError):
    """Error indicating invalid host input"""
