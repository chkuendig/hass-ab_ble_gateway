"""The April Brother BLE Gateway integration."""
from __future__ import annotations
from homeassistant.components.bluetooth import BaseHaRemoteScanner
from .util import iter_gateway_devices, parse_ap_ble_devices_data, parse_raw_data
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.components import mqtt
from homeassistant.components.mqtt.models import ReceiveMessage
from homeassistant.setup import async_when_setup
from .const import DOMAIN
from homeassistant.components.bluetooth import (
    HaBluetoothConnector,
    async_get_advertisement_callback,
    async_register_scanner,
    MONOTONIC_TIME,
)
from homeassistant.const import (
    ATTR_COMMAND,
    ATTR_ENTITY_ID,
    CONF_CLIENT_SECRET,
    CONF_HOST,
    CONF_NAME,
    EVENT_HOMEASSISTANT_STOP,
)

import re
TWO_CHAR = re.compile("..")


# TODO List the platforms that you want to support.
# For your initial PR, limit it to 1 platform.
PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR]

_LOGGER = logging.getLogger(__name__)


class AbBleScanner(BaseHaRemoteScanner):
    """Receive advertisements from the gateway's MQTT stream."""

    @callback
    def async_on_mqtt_message(self, msg: ReceiveMessage) -> None:
        """Feed every complete device entry of a gateway message to HA."""
        for device in iter_gateway_devices(msg.payload):
            try:
                adv = parse_raw_data(parse_ap_ble_devices_data(device))
            except (IndexError, ValueError, UnicodeDecodeError) as err:
                _LOGGER.debug("Skipping malformed advertisement %s: %s", device, err)
                continue
            if adv is None:
                continue
            self._async_on_advertisement(
                address=adv['address'].upper(),
                rssi=adv['rssi'],
                local_name=adv['local_name'],
                service_uuids=adv['service_uuids'],
                service_data=adv['service_data'],
                manufacturer_data=adv['manufacturer_data'],
                tx_power=None,
                details=dict(),
                # the msg.payload does have a field "time" but its time passed since boot and I don't know how to figure out the boot timestamp so we just use the current time here
                advertisement_monotonic_time=MONOTONIC_TIME())


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up April Brother BLE Gateway from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    source_id = str(entry.unique_id)
    connectable = False

    # Stock V4 firmware has no GATT transport. Register the receiver for
    # availability/source tracking, without advertising connection capability.
    scanner = AbBleScanner(source_id, entry.title, None, connectable)
    entry.async_on_unload(scanner.async_setup())
    entry.async_on_unload(
        async_register_scanner(
            hass,
            scanner,
            source_domain=DOMAIN,
            source_config_entry_id=entry.entry_id,
        )
    )

    config = entry.as_dict()
    entry.async_on_unload(
        await mqtt.async_subscribe(
            hass, config['data']['mqtt_topic'],
            scanner.async_on_mqtt_message, encoding=None,
        )
    )
    hass.data[DOMAIN][entry.entry_id] = scanner

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This integration has no entity platforms. ConfigEntry runs the registered
    # MQTT, scanner and timer cleanup callbacks when it is unloaded.
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
