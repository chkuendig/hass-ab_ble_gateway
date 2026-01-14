"""Config flow for  devices."""
import errno
from functools import partial
import logging
import socket

from homeassistant.components import mqtt
from homeassistant.components.mqtt.const import CONF_BROKER
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN
from homeassistant.components.mqtt.models import (  
    DATA_MQTT,
    DATA_MQTT_AVAILABLE
)

from urllib.parse import urlparse

import voluptuous as vol
import requests

from homeassistant import config_entries, data_entry_flow
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.data_entry_flow import FlowResult
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_TIMEOUT,
    CONF_TYPE,
    CONF_DESCRIPTION,
    ATTR_CONFIGURATION_URL,
    CONF_PORT,
    CONF_FRIENDLY_NAME,
    CONF_HOSTS,
    CONF_UNIQUE_ID,
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.components import mqtt

from homeassistant.helpers import config_validation as cv
from datetime import timedelta

from homeassistant.helpers.debounce import Debouncer

# from .helpers import format_mac

_LOGGER = logging.getLogger(__name__)


class AbBleFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a AbBle config flow."""

    VERSION = 1

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        if not discovery_info.properties["hw"].startswith("4."):
            _LOGGER.error("Only AB BLE Gateway revisions 4.x are supported ")
            return self.async_abort(reason="zeroconf_server_error")
        formatted_mac = ':'.join(discovery_info.properties["mac"][i:i + 2] for i in range(0, len(discovery_info.properties["mac"]), 2))
        self.config = {
            CONF_HOST: discovery_info.host,
            CONF_HOSTS: discovery_info.addresses,
            CONF_PORT: str(discovery_info.port),
            CONF_NAME: discovery_info.hostname,
            CONF_FRIENDLY_NAME: discovery_info.hostname[
                0 : discovery_info.hostname.find(".")
            ].upper(),
            CONF_MAC: formatted_mac,
            CONF_UNIQUE_ID: formatted_mac,
        }

        self._async_abort_entries_match({CONF_UNIQUE_ID: self.config[CONF_UNIQUE_ID]})

        await self.async_set_unique_id(self.config[CONF_UNIQUE_ID])
        self._abort_if_unique_id_configured(updates=self.config)

        self.context["title_placeholders"] = {
            CONF_HOST: self.config[CONF_HOST],
            CONF_NAME: self.config[CONF_FRIENDLY_NAME],
            CONF_DESCRIPTION: self.config[CONF_UNIQUE_ID],
        }
        return await self.async_step_confirm()

    def get_info(self, host, port):
        """Fetch info from gateway"""
        return requests.get("http://{}:{}/info".format(host, port), timeout=5).json()

    def get_config(self, host, port, username=None, password=None):
        """Fetch config from gateway"""
        auth = None
        if username or password:
            _LOGGER.debug("setting username + password")
            auth = requests.auth.HTTPBasicAuth(username, password)
        return requests.get(
            "http://{}:{}/config".format(host, port), timeout=5, auth=auth
        ).json()

    async def async_step_confirm(self, user_input=None):
        """Handle user-confirmation of discovered node."""

        """
        Config Flow
        1. Manual: Enter IP and optional username/password
        2. if auth is good, continue -- this is where discovery jumps in
        3. fetch config and compare
        4. get info and set other fields
        """

        errors = {}
        if (user_input):
            # check connection and bail
            self.config["mqtt_id_prefix"] = user_input["mqtt_id_prefix"]
            self.config["mqtt_topic"] = user_input["mqtt_topic"]
            self.config["mqtt_user"] = user_input["mqtt_user"] if "mqtt_user" in user_input else None
            self.config["mqtt_mqtt_password"] = user_input["mqtt_mqtt_password"] if "mqtt_mqtt_password" in user_input else None
            return await self._async_get_entry()
        host = (
            user_input[CONF_HOST]
            if user_input
            else (self.config[CONF_HOST] if CONF_HOST in self.config else None)
        )
        port = (
            user_input[CONF_PORT]
            if user_input
            else (self.config[CONF_PORT] if CONF_PORT in self.config else None)
        )

        username = (
            user_input[CONF_USERNAME]
            if user_input
            else (self.config[CONF_USERNAME] if CONF_USERNAME in self.config else None)
        )
        password = (
            user_input[CONF_PASSWORD]
            if user_input
            else (self.config[CONF_PASSWORD] if CONF_PASSWORD in self.config else None)
        )

        if host is not None and port is not None:
            details = await self.hass.async_add_executor_job(self.get_info, host, port)
            # some is set by discovery, otherwise we set it manually
            if CONF_HOST not in self.config:
                self.config[CONF_HOST] = host
                self.config[CONF_HOSTS] = [host]
                self.config[CONF_PORT] = port
            if CONF_NAME not in self.config:
                name = "xbg-" + details['mac'].replace(':', "")[-6].lower()
                self.config[CONF_NAME] = name
                self.config[CONF_FRIENDLY_NAME] = name

            if CONF_MAC not in self.config:
                self.config[CONF_MAC] = details['mac']
            if CONF_UNIQUE_ID not in self.config:
                self.config[CONF_UNIQUE_ID] = details['mac']

            # Check if auth is required
            # http://192.168.178.223/info
            # {"firmwareVer":"1.5.12","hardwareVer":"4.0","mac":"C4:5B:BE:8E:51:8C","sn":9326988,"validate":1,"auth":1}

            # if auth works, get other settings, otherwise ask for auth first
            if details["auth"] == 0 or (username and password):
                gateway_config = await self.hass.async_add_executor_job(
                    self.get_config, host, port, username, password
                )
            else:
                errors["base"] = "failed_connecting"
                _LOGGER.error("Can't fetch config, no auth set")

        # http://192.168.178.223/config
        # {"conn-type":3,"host":"mqtt.bconimg.com","port":1883,"mqtt-topic":"gw/test555","cfg-topic":"device-config","one-cfg-topic":"device-config-","one-pub-topic":"pub-config-","http-url":"","req-int":1,"min-rssi":-127,"adv-filter":0,"dup-filter":0,"scan-act":0,"mqtt-id-prefix":"XBG_","mqtt-username":"","mqtt-password":"","mqtt-config":0,"mqtt-retain":0,"mqtt-qos":0,"basic-auth":1,"req-format":0,"ntp-enabled":0,"ntp1":"ntp1.aliyun.com","ntp2":"ntp2.aliyun.com","mqtts":0,"https":0,"wss":0,"sch-type":0,"metadata":"","tz":"","sch-begin":"","sch-end":"","filter-mfg":0,"filter-uuid":""}
        # make sure conn-type is 3, mqtt settings match and show an error otherwise
        mqtt_data = self.hass.data[DATA_MQTT]
        if mqtt_data.client is None or not mqtt.util.mqtt_config_entry_enabled(self.hass):
            errors["base"] = "mqtt_not_enabled"
        mqtt_config = mqtt_data.client.conf
        if (gateway_config['conn-type'] != 3):  # 3 is the conn-type for MQTT (1 is websocket, 2 is HTTP)
            errors["base"] = "gateway_mqtt_not_configured"

        elif (gateway_config['host'] != mqtt_config.get(CONF_BROKER) or
                gateway_config['port'] != mqtt_config.get(CONF_PORT)):
            errors["base"] = "mqtt_broker_mismatch"

        elif (gateway_config['mqtt-username'] != (mqtt_config.get(CONF_USERNAME) or "") or
                gateway_config['mqtt-password'] != (mqtt_config.get(CONF_PASSWORD) or "")):
            errors["base"] = "mqtt_auth_mismatch"

        data_schema = {
            vol.Required("mqtt_id_prefix", description={"suggested_value": gateway_config['mqtt-id-prefix']}): str,
            vol.Required("mqtt_topic", description={"suggested_value": gateway_config['mqtt-topic']}): str,
        }

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                CONF_HOST: self.config[CONF_HOST],
                CONF_NAME: self.config[CONF_NAME],
                CONF_DESCRIPTION: self.config[CONF_UNIQUE_ID],
            },
            data_schema=vol.Schema(data_schema), errors=errors
        )

    async def async_step_user(self, user_input=None):
        """Handle a flow initiated by the user."""
        errors = {}

        self.config = {}
        if user_input is not None:
            host = (
                user_input[CONF_HOST]
                if user_input
                else (self.config[CONF_HOST] if CONF_HOST in self.config else None)
            )
            port = (
                user_input[CONF_PORT]
                if user_input
                else (self.config[CONF_PORT] if CONF_PORT in self.config else None)
            )

            username = (
                user_input[CONF_USERNAME]
                if CONF_USERNAME in user_input
                else (self.config[CONF_USERNAME] if CONF_USERNAME in self.config else None)
            )
            password = (
                user_input[CONF_PASSWORD]
                if CONF_PASSWORD in user_input
                else (self.config[CONF_PASSWORD] if CONF_PASSWORD in self.config else None)
            )
            if CONF_HOST not in self.config:
                self.config[CONF_HOST] = host
                self.config[CONF_HOSTS] = [host]
                self.config[CONF_PORT] = int(port)

            if password and CONF_PASSWORD not in self.config:
                self.config[CONF_PASSWORD] = password
            if username and CONF_USERNAME not in self.config:
                self.config[CONF_USERNAME] = username
            return await self.async_step_confirm()

        data_schema = {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default="80"): str,

            vol.Optional(CONF_USERNAME): str,
            vol.Optional(CONF_PASSWORD): str,
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(data_schema),
            errors=errors,
        )

    async def _async_get_entry(self):
        """Return config entry or update existing config entry."""
        print("self.async_create_entry")
        return self.async_create_entry(
            title=self.config[CONF_FRIENDLY_NAME],
            data=self.config,
        )
