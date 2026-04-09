"""Virtual climate entity for PID Controller."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    CONF_HEATING_DEMAND_ENTITY,
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
    CONF_FLOOR_VALUE,
    CONF_INTEGRAL_MAX,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_OFF_THRESHOLD,
    DEFAULT_PID_SAMPLE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_FLOOR_VALUE,
    DEFAULT_INTEGRAL_MAX,
    DOMAIN,
)
from .pid import PIDController

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PID Controller climate entity from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    entity = PIDVirtualThermostat(hass, entry, data["pid"])
    data["entity"] = entity
    async_add_entities([entity])


class PIDVirtualThermostat(ClimateEntity):
    """Virtual thermostat that uses a custom PID to control a real radiator valve."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_target_temperature_step = 0.5
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        pid: PIDController,
    ) -> None:
        """Initialize the virtual thermostat."""
        self.hass = hass
        self._entry = entry
        self._pid = pid
        self._lock = asyncio.Lock()

        self._attr_unique_id = f"pid_controller_{entry.entry_id}"
        self._attr_name = entry.data.get("name", "PID Controller")
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature = None
        self._attr_min_temp = entry.options.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
        self._attr_max_temp = entry.options.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)

        self._temperature_entity = entry.data[CONF_TEMPERATURE_ENTITY]
        self._heating_demand_entity = entry.data.get(CONF_HEATING_DEMAND_ENTITY)
        self._real_thermostat_entity = entry.data[CONF_REAL_THERMOSTAT_ENTITY]

        self._valve_position: int = 0
        self._unsub_listeners: list = []

    async def async_added_to_hass(self) -> None:
        """Set up listeners when entity is added."""
        # Listen to temperature source changes
        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass,
                [self._temperature_entity],
                self._async_temperature_changed,
            )
        )

        # Listen to real thermostat for availability
        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass,
                [self._real_thermostat_entity],
                self._async_thermostat_state_changed,
            )
        )

        # PID sample interval
        sample_interval = self._entry.options.get(
            CONF_PID_SAMPLE_INTERVAL, DEFAULT_PID_SAMPLE_INTERVAL
        )
        from datetime import timedelta

        self._unsub_listeners.append(
            async_track_time_interval(
                self.hass,
                self._async_pid_tick,
                timedelta(seconds=sample_interval),
            )
        )

        # Valve refresh interval (to override built-in PID)
        update_interval = self._entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        self._unsub_listeners.append(
            async_track_time_interval(
                self.hass,
                self._async_refresh_valve,
                timedelta(minutes=update_interval),
            )
        )

        # Listen for options updates
        self._entry.async_on_unload(
            self._entry.add_update_listener(self._async_options_updated)
        )

        # Read initial temperature
        temp_state = self.hass.states.get(self._temperature_entity)
        if temp_state and temp_state.state not in ("unavailable", "unknown"):
            try:
                self._attr_current_temperature = float(temp_state.state)
            except (ValueError, TypeError):
                pass

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listeners."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

    @callback
    def _async_temperature_changed(self, event: Event) -> None:
        """Handle temperature sensor state change."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return
        try:
            self._attr_current_temperature = float(new_state.state)
            self.async_write_ha_state()
        except (ValueError, TypeError):
            pass

    @callback
    def _async_thermostat_state_changed(self, event: Event) -> None:
        """Handle real thermostat state change for availability tracking."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        self._attr_available = new_state.state not in ("unavailable",)
        self.async_write_ha_state()

    async def _async_pid_tick(self, _now=None) -> None:
        """Periodic PID recalculation."""
        await self._async_recalculate_and_send()

    async def _async_refresh_valve(self, _now=None) -> None:
        """Re-send current valve position to prevent built-in PID takeover."""
        async with self._lock:
            await self._async_send_valve_position(self._valve_position)

    @staticmethod
    async def _async_options_updated(
        hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Handle options update."""
        data = hass.data[DOMAIN].get(entry.entry_id)
        if data and "pid" in data:
            pid: PIDController = data["pid"]
            pid.update_params(
                kp=entry.options.get(CONF_KP, DEFAULT_KP),
                ki=entry.options.get(CONF_KI, DEFAULT_KI),
                kd=entry.options.get(CONF_KD, DEFAULT_KD),
                floor_value=entry.options.get(CONF_FLOOR_VALUE, DEFAULT_FLOOR_VALUE),
                off_threshold=entry.options.get(
                    CONF_OFF_THRESHOLD, DEFAULT_OFF_THRESHOLD
                ),
                integral_max=entry.options.get(
                    CONF_INTEGRAL_MAX, DEFAULT_INTEGRAL_MAX
                ),
            )
        if data and "entity" in data:
            entity: PIDVirtualThermostat = data["entity"]
            entity._attr_min_temp = entry.options.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
            entity._attr_max_temp = entry.options.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
            entity.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature (called by Schedy)."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        self._attr_target_temperature = temp
        self.async_write_ha_state()
        await self._async_recalculate_and_send()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()
        if hvac_mode == HVACMode.OFF:
            async with self._lock:
                self._valve_position = 0
                self._pid.reset()
                await self._async_send_valve_position(0)
        else:
            await self._async_recalculate_and_send()

    async def async_turn_on(self) -> None:
        """Turn on heating."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn off heating."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _async_recalculate_and_send(self) -> None:
        """Run PID calculation and send valve position."""
        async with self._lock:
            current = self._attr_current_temperature
            if current is None:
                return

            valve = self._pid.compute(
                current_temp=current,
                target_temp=self._attr_target_temperature,
                is_heating=self._attr_hvac_mode == HVACMode.HEAT,
            )

            self._valve_position = valve
            self._update_hvac_action()
            self.async_write_ha_state()
            await self._async_send_valve_position(valve)

    def _update_hvac_action(self) -> None:
        """Update hvac_action based on current state."""
        if self._attr_hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif self._valve_position > 0:
            self._attr_hvac_action = HVACAction.HEATING
        else:
            self._attr_hvac_action = HVACAction.IDLE

    async def _async_send_valve_position(self, valve: int) -> None:
        """Send valve position to the real thermostat's PI heating demand."""
        entity_id = self._heating_demand_entity
        if not entity_id:
            _LOGGER.warning("No heating demand entity configured")
            return

        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.warning(
                "Heating demand entity %s is not available, skipping valve write",
                entity_id,
            )
            return

        try:
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": valve},
                blocking=True,
            )
        except Exception:
            _LOGGER.exception(
                "Failed to set valve position on %s", entity_id
            )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for debugging."""
        return {
            "valve_position": self._valve_position,
            "pid_p": round(self._pid.last_p, 2),
            "pid_i": round(self._pid.last_i, 2),
            "pid_d": round(self._pid.last_d, 2),
            "floor_active": self._pid.last_floor_active,
            "heating_demand_entity": self._heating_demand_entity,
        }
