"""The OPA-UA integration."""
import asyncio
from datetime import timedelta
import logging
import voluptuous as vol
from homeassistant.core import callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from async_timeout import timeout
from homeassistant.const import MATCH_ALL
from homeassistant.helpers.typing import ConfigType, HomeAssistantType, ServiceDataType
from homeassistant.core import CoreState, Event, HassJob, ServiceCall, callback

from homeassistant.const import CONF_API_KEY
from homeassistant.core import Config, HomeAssistant, State
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import TrackStates
from homeassistant.helpers.event import async_track_state_change
from opcua import ua, Server
from homeassistant.util.decorator import Registry
from homeassistant.const import (
    CONF_CLIENT_ID,
    CONF_DEVICE,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PAYLOAD,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_USERNAME,
    CONF_VALUE_TEMPLATE,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
ENTITY_ADAPTERS = Registry()
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

# TODO List the platforms that you want to support.
# For your initial PR, limit it to 1 platform.
# PLATFORMS = ["light"]


async def async_setup_entry(hass, config_entry) -> bool:
    return True


async def async_setup(hass, config):
    """Set up the MQTT state feed."""
    conf = config.get(DOMAIN)

    handler = OpcUaHandler(hass, config, config)
    handler.on_connect(hass)

    return True


class OpcUaHandler:
    def __init__(
        self,
        hass: HomeAssistantType,
        config_entry,
        conf,
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.conf = conf
        self._ha_started = asyncio.Event()

        if self.hass.state == CoreState.running:
            self._ha_started.set()
            _LOGGER.info("HomeAssitant already up!")
        else:

            @callback
            def ha_started(_):
                self._ha_started.set()
                _LOGGER.info("HomeAssistant started!")

            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, ha_started)

    def update_opc_tag(self, entity, state, attributes):

        root = self._server.get_root_node()

        try:
            node = root.get_child(["0:Objects", "2:HomeAssistant", "2:" + entity])
            node.set_value(state)
        except:
            _LOGGER.warn("OPC UA error with: " + entity)

        _LOGGER.info(
            f"UPDATE OPC: entity {entity} state: {state}"  # attributes: {attributes}"
        )
        # todo update attributes

    def handle_event(self, event):
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        di = State.as_dict(new_state)

        self.update_opc_tag(
            event.data["entity_id"], new_state.state, new_state.attributes
        )

    def setup_uaserver(self) -> Server:
        server = Server()
        server.set_endpoint("opc.tcp://0.0.0.0:4840/hass/server/")
        self._server = server
        return server

    def init_opc_entities(self, server, entities):
        _LOGGER.info(f"Init OPC UA with {len(entities)} entities")

        uri = "http://haopcua.lehofer.org"
        idx = server.register_namespace(uri)
        loc_name = "HomeAssistant"

        types = server.get_node(ua.ObjectIds.BaseObjectType)

        object_type_to_derive_from = server.get_root_node().get_child(
            ["0:Types", "0:ObjectTypes", "0:BaseObjectType"]
        )
        ha_state_type = types.add_object_type(idx, "HAStateType")
        ha_state_type.add_variable(idx, "state", 1.0)

        # create objects
        objects = server.get_objects_node()

        loc_node = objects.add_folder(idx, loc_name)
        loc_node.add_property(idx, "location_name", loc_name)
        loc_node.add_property(idx, "version", "0.1")

        for entity in entities:
            _LOGGER.debug("Entity: %s state: %s", entity.entity_id, entity.state)
            # create the node
            node = loc_node.add_object(idx, entity.entity_id)  # , ha_state_type.nodeid)

            # set the value for the state child variable
            # state = node.get_child("%d:state" % idx)

            state = node.add_variable(idx, "state", entity.state)
            # state.set_value(entity.state)

            # node.set_attribute(ua.AttributeIds.DisplayName, entity.attributes['friendly_name'])

            for attr in entity.attributes.keys():
                if attr not in ["icon"]:
                    try:
                        node.add_property(idx, attr, entity.attributes[attr])
                    except:
                        _LOGGER.error(f"OPC UA add property error: {attr}")

        server.start()
        return (idx, loc_name)

    def on_connect(self, hass) -> None:
        async def ha_initialized():
            def async_get_entities(self, hass):
                entities = []
                for state in hass.states.async_all():
                    entities.append(state)

                return entities

            await self._ha_started.wait()  # Wait for Home Assistant to start

            entities = async_get_entities(self, hass)
            server = self.setup_uaserver()
            self.init_opc_entities(server, entities)

            hass.bus.async_listen("state_changed", self.handle_event)

        self.hass.loop.create_task(ha_initialized())
