"""Config flow for PID Controller integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
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
    CONF_TEMPERATURE_ENTITY,
    CONF_UPDATE_INTERVAL,
    DEFAULT_FLOOR_VALUE,
    DEFAULT_INTEGRAL_MAX,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_OFF_THRESHOLD,
    DEFAULT_PID_SAMPLE_INTERVAL,
    DEFAULT_SYNC_TARGET_TEMP,
    DEFAULT_UPDATE_INTERVAL,
    CONF_SYNC_TARGET_TEMP,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Auto-detect heating demand entity
            heating_demand = _find_heating_demand_entity(
                self.hass, user_input[CONF_REAL_THERMOSTAT_ENTITY]
            )

            if heating_demand is None:
                errors[CONF_REAL_THERMOSTAT_ENTITY] = "heating_demand_not_found"
            else:
                # Check for duplicate heating demand entity usage
                for entry in self._async_current_entries():
                    if entry.data.get(CONF_HEATING_DEMAND_ENTITY) == heating_demand:
                        errors[CONF_REAL_THERMOSTAT_ENTITY] = "already_configured"
                        break

            if not errors:
                user_input[CONF_HEATING_DEMAND_ENTITY] = heating_demand
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=user_input,
                )

        schema = vol.Schema(
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

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow."""
        return PIDControllerOptionsFlow(config_entry)


class PIDControllerOptionsFlow(OptionsFlow):
    """Handle options flow for PID Controller."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Handle options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options

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
                    CONF_SYNC_TARGET_TEMP,
                    default=options.get(
                        CONF_SYNC_TARGET_TEMP, DEFAULT_SYNC_TARGET_TEMP
                    ),
                ): BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
