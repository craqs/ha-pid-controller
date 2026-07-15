"""Config flow for PID Controller integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_BOOST_THRESHOLD,
    CONF_BOOST_VALUE,
    CONF_FLOOR_VALUE,
    CONF_HEATING_DEMAND_ENTITY,
    CONF_INTEGRAL_MAX,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_OFF_THRESHOLD,
    CONF_PID_SAMPLE_INTERVAL,
    CONF_REAL_THERMOSTAT_ENTITY,
    CONF_STALE_TIMEOUT,
    CONF_SYNC_TARGET_TEMP,
    CONF_TEMPERATURE_ENTITY,
    CONF_UPDATE_INTERVAL,
    DEFAULT_BOOST_THRESHOLD,
    DEFAULT_BOOST_VALUE,
    DEFAULT_FLOOR_VALUE,
    DEFAULT_INTEGRAL_MAX,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_OFF_THRESHOLD,
    DEFAULT_PID_SAMPLE_INTERVAL,
    DEFAULT_STALE_TIMEOUT,
    DEFAULT_SYNC_TARGET_TEMP,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_TEMPERATURE_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "climate"])
        ),
        vol.Required(CONF_REAL_THERMOSTAT_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="climate")
        ),
    }
)


def _find_heating_demand_entity(
    hass, real_thermostat_entity_id: str
) -> str | None:
    """Auto-detect the PI heating demand number entity from the same device."""
    ent_reg = er.async_get(hass)

    # Find the device that owns the real thermostat
    thermostat_entry = ent_reg.async_get(real_thermostat_entity_id)
    if thermostat_entry is None or thermostat_entry.device_id is None:
        return None

    device_id = thermostat_entry.device_id

    # Find a number entity on the same device with "heating_demand" or
    # "pi_heating_demand" in the entity_id
    for entity in er.async_entries_for_device(ent_reg, device_id):
        if entity.domain == "number" and (
            "pi_heating_demand" in entity.entity_id
            or "heating_demand" in entity.entity_id
        ):
            return entity.entity_id

    return None


class PIDControllerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PID Controller."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._user_input: dict[str, Any] = {}

    def _is_duplicate_demand(self, heating_demand: str) -> bool:
        """Check if another entry already controls this demand entity."""
        reconfigure_id = (
            self._get_reconfigure_entry().entry_id
            if self.source == SOURCE_RECONFIGURE
            else None
        )
        return any(
            entry.data.get(CONF_HEATING_DEMAND_ENTITY) == heating_demand
            and entry.entry_id != reconfigure_id
            for entry in self._async_current_entries()
        )

    def _async_finish(self, data: dict[str, Any]) -> dict:
        """Create the entry, or update it when reconfiguring."""
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                title=data[CONF_NAME],
                data=data,
            )
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    async def _async_handle_thermostat_form(
        self, user_input: dict[str, Any] | None, step_id: str
    ) -> dict:
        """Shared logic for the user and reconfigure steps."""
        errors: dict[str, str] = {}

        if user_input is not None:
            heating_demand = _find_heating_demand_entity(
                self.hass, user_input[CONF_REAL_THERMOSTAT_ENTITY]
            )
            if heating_demand is None:
                # Fall back to picking the demand entity manually.
                self._user_input = dict(user_input)
                return await self.async_step_manual_demand()
            if self._is_duplicate_demand(heating_demand):
                errors[CONF_REAL_THERMOSTAT_ENTITY] = "already_configured"
            else:
                return self._async_finish(
                    {**user_input, CONF_HEATING_DEMAND_ENTITY: heating_demand}
                )

        schema = USER_SCHEMA
        if self.source == SOURCE_RECONFIGURE:
            schema = self.add_suggested_values_to_schema(
                schema, self._get_reconfigure_entry().data
            )

        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Handle the initial step."""
        return await self._async_handle_thermostat_form(user_input, "user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Handle reconfiguration of an existing entry."""
        return await self._async_handle_thermostat_form(user_input, "reconfigure")

    async def async_step_manual_demand(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Pick the heating demand entity manually when auto-detect fails."""
        errors: dict[str, str] = {}

        if user_input is not None:
            heating_demand = user_input[CONF_HEATING_DEMAND_ENTITY]
            if self._is_duplicate_demand(heating_demand):
                errors[CONF_HEATING_DEMAND_ENTITY] = "already_configured"
            else:
                return self._async_finish(
                    {**self._user_input, CONF_HEATING_DEMAND_ENTITY: heating_demand}
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HEATING_DEMAND_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="number")
                ),
            }
        )

        return self.async_show_form(
            step_id="manual_demand",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow."""
        return PIDControllerOptionsFlow()


class PIDControllerOptionsFlow(OptionsFlow):
    """Handle options flow for PID Controller."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Handle options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_KP,
                    default=options.get(CONF_KP, DEFAULT_KP),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=100, step=0.1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_KI,
                    default=options.get(CONF_KI, DEFAULT_KI),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=1, step=0.001, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_KD,
                    default=options.get(CONF_KD, DEFAULT_KD),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=100, step=0.1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_FLOOR_VALUE,
                    default=options.get(CONF_FLOOR_VALUE, DEFAULT_FLOOR_VALUE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=100, step=1, mode=NumberSelectorMode.SLIDER
                    )
                ),
                vol.Optional(
                    CONF_OFF_THRESHOLD,
                    default=options.get(CONF_OFF_THRESHOLD, DEFAULT_OFF_THRESHOLD),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=5, step=0.1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5, max=60, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_PID_SAMPLE_INTERVAL,
                    default=options.get(
                        CONF_PID_SAMPLE_INTERVAL, DEFAULT_PID_SAMPLE_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10, max=300, step=10, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_MIN_TEMP,
                    default=options.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=15, step=0.5, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_MAX_TEMP,
                    default=options.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=15, max=40, step=0.5, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_INTEGRAL_MAX,
                    default=options.get(CONF_INTEGRAL_MAX, DEFAULT_INTEGRAL_MAX),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10, max=500, step=10, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_BOOST_THRESHOLD,
                    default=options.get(
                        CONF_BOOST_THRESHOLD, DEFAULT_BOOST_THRESHOLD
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=5, step=0.1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_BOOST_VALUE,
                    default=options.get(CONF_BOOST_VALUE, DEFAULT_BOOST_VALUE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=50, max=100, step=5, mode=NumberSelectorMode.SLIDER
                    )
                ),
                vol.Optional(
                    CONF_STALE_TIMEOUT,
                    default=options.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=360, step=5, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_SYNC_TARGET_TEMP,
                    default=options.get(
                        CONF_SYNC_TARGET_TEMP, DEFAULT_SYNC_TARGET_TEMP
                    ),
                ): BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
