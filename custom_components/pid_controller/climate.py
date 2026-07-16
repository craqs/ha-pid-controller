"""Virtual climate entity for PID Controller."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_HEATING_DEMAND_ENTITY,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_PID_SAMPLE_INTERVAL,
    CONF_REAL_THERMOSTAT_ENTITY,
    CONF_STALE_TIMEOUT,
    CONF_SYNC_TARGET_TEMP,
    CONF_TEMPERATURE_ENTITY,
    CONF_UPDATE_INTERVAL,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_PID_SAMPLE_INTERVAL,
    DEFAULT_STALE_TIMEOUT,
    DEFAULT_SYNC_TARGET_TEMP,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .pid import PIDController

_LOGGER = logging.getLogger(__name__)

# Minimum seconds between event-driven re-sends when the heating demand
# entity reports a value we didn't command (built-in PID takeover). Bounds
# Zigbee traffic if a device persistently quantizes our writes.
MIN_ENFORCE_INTERVAL = 60.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PID Controller climate entity from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    entity = PIDVirtualThermostat(hass, entry, data["pid"])
    async_add_entities([entity])


DEFAULT_TARGET_TEMP = 20.0


class PIDVirtualThermostat(ClimateEntity, RestoreEntity):
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
        # Last value confirmed written to the demand entity; None forces the
        # next PID tick to write even if the computed value didn't change.
        self._last_sent_valve: int | None = None
        self._last_temp_update: float | None = None
        self._sensor_stale_logged = False
        self._last_enforce: float = 0.0
        self._unsub_listeners: list = []

    async def async_added_to_hass(self) -> None:
        """Set up listeners when entity is added."""
        # Restore previous state
        last_state = await self.async_get_last_state()
        if last_state is not None:
            if last_state.state in (HVACMode.OFF, HVACMode.HEAT):
                self._attr_hvac_mode = HVACMode(last_state.state)
            if last_state.attributes.get(ATTR_TEMPERATURE) is not None:
                try:
                    self._attr_target_temperature = float(
                        last_state.attributes[ATTR_TEMPERATURE]
                    )
                except (ValueError, TypeError):
                    pass

        # Ensure target temperature is never None
        if self._attr_target_temperature is None:
            self._attr_target_temperature = DEFAULT_TARGET_TEMP

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

        # Listen to the heating demand entity: detect built-in PID takeover
        # and recover from unavailability without waiting for the refresh.
        if self._heating_demand_entity:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self.hass,
                    [self._heating_demand_entity],
                    self._async_demand_changed,
                )
            )

        # PID sample interval
        sample_interval = self._entry.options.get(
            CONF_PID_SAMPLE_INTERVAL, DEFAULT_PID_SAMPLE_INTERVAL
        )
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

        # Initial availability from the real thermostat
        thermostat_state = self.hass.states.get(self._real_thermostat_entity)
        self._attr_available = (
            thermostat_state is not None
            and thermostat_state.state != "unavailable"
        )

        # Read initial temperature
        temp = self._read_temperature(
            self.hass.states.get(self._temperature_entity)
        )
        if temp is not None:
            self._attr_current_temperature = temp
            self._last_temp_update = time.monotonic()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listeners."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

    @staticmethod
    def _read_temperature(state: State | None) -> float | None:
        """Extract a temperature from a sensor or climate source state."""
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        if state.domain == "climate":
            value = state.attributes.get("current_temperature")
        else:
            value = state.state
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _sensor_is_stale(self) -> bool:
        """Return True if the temperature source stopped reporting."""
        timeout_min = self._entry.options.get(
            CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT
        )
        if not timeout_min:
            return False
        if (
            self._attr_current_temperature is None
            or self._last_temp_update is None
        ):
            return True
        return time.monotonic() - self._last_temp_update > timeout_min * 60

    def _mark_sensor_stale(self) -> None:
        """Log once and expose the stale condition; valve writes are paused."""
        if not self._sensor_stale_logged:
            self._sensor_stale_logged = True
            _LOGGER.warning(
                "Temperature source %s stopped reporting; pausing valve "
                "control on %s so the thermostat's built-in controller can "
                "take over as fallback",
                self._temperature_entity,
                self._heating_demand_entity,
            )
            self.async_write_ha_state()

    @callback
    def _async_temperature_changed(self, event: Event) -> None:
        """Handle temperature sensor state change."""
        temp = self._read_temperature(event.data.get("new_state"))
        if temp is None:
            return
        self._attr_current_temperature = temp
        self._last_temp_update = time.monotonic()
        if self._sensor_stale_logged:
            self._sensor_stale_logged = False
            _LOGGER.info(
                "Temperature source %s is reporting again; resuming valve "
                "control",
                self._temperature_entity,
            )
        self.async_write_ha_state()

    @callback
    def _async_thermostat_state_changed(self, event: Event) -> None:
        """Handle real thermostat state change for availability tracking."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        self._attr_available = new_state.state not in ("unavailable",)
        self.async_write_ha_state()

    @callback
    def _async_demand_changed(self, event: Event) -> None:
        """Watch the heating demand entity we control.

        If it reports a value we didn't command while we're actively heating,
        the built-in controller took over (or a write was lost) - re-assert
        our position immediately instead of waiting for the periodic refresh.
        """
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            # Force a write once it recovers (next event or next PID tick).
            self._last_sent_valve = None
            return
        if self._attr_hvac_mode != HVACMode.HEAT:
            return
        if self._sensor_is_stale():
            # Deliberately ceding control to the built-in fallback.
            return
        try:
            reported = int(float(new_state.state) + 0.5)
        except (ValueError, TypeError):
            return
        if reported == self._valve_position:
            self._last_sent_valve = self._valve_position
            return
        now = time.monotonic()
        if now - self._last_enforce < MIN_ENFORCE_INTERVAL:
            return
        self._last_enforce = now
        _LOGGER.debug(
            "%s reports %s%% but we commanded %s%%; re-asserting",
            self._heating_demand_entity,
            reported,
            self._valve_position,
        )
        self.hass.async_create_task(self._async_enforce_valve())

    async def _async_enforce_valve(self) -> None:
        """Re-send our valve position after an external change."""
        async with self._lock:
            if self._attr_hvac_mode != HVACMode.HEAT:
                return
            await self._async_send_valve_position(self._valve_position)

    async def _async_pid_tick(self, _now=None) -> None:
        """Periodic PID recalculation."""
        await self._async_recalculate_and_send()

    async def _async_refresh_valve(self, _now=None) -> None:
        """Re-send current valve position to prevent built-in PID takeover."""
        # When off, the real thermostat is also off; don't keep poking it.
        if self._attr_hvac_mode == HVACMode.OFF:
            return
        async with self._lock:
            if self._sensor_is_stale():
                self._mark_sensor_stale()
                return
            await self._async_send_valve_position(self._valve_position)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature (called by Schedy)."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp = max(self._attr_min_temp, min(self._attr_max_temp, float(temp)))
        self._attr_target_temperature = temp
        self.async_write_ha_state()
        # A numeric setpoint implies heating; make sure the real thermostat is
        # back on before syncing (it may have been turned off while idle).
        if self._attr_hvac_mode != HVACMode.OFF:
            await self._async_set_real_hvac_mode(HVACMode.HEAT)
        await self._async_sync_target_temp(temp)
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
            # Mirror the off state onto the real thermostat so its valve doesn't
            # stay parked at the last synced target temperature (e.g. Schedy
            # summer mode leaving valves showing 21.5/23.5 °C).
            await self._async_set_real_hvac_mode(HVACMode.OFF)
        else:
            # Restore the real thermostat to heat before resuming valve control.
            await self._async_set_real_hvac_mode(HVACMode.HEAT)
            await self._async_recalculate_and_send()

    async def async_turn_on(self) -> None:
        """Turn on heating."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn off heating."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _async_sync_target_temp(self, temp: float) -> None:
        """Sync target temperature to the real thermostat if enabled."""
        if not self._entry.options.get(
            CONF_SYNC_TARGET_TEMP, DEFAULT_SYNC_TARGET_TEMP
        ):
            return

        entity_id = self._real_thermostat_entity
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.warning(
                "Real thermostat %s is not available, skipping target temp sync",
                entity_id,
            )
            return

        try:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": entity_id, ATTR_TEMPERATURE: temp},
                blocking=True,
            )
        except Exception:
            _LOGGER.exception(
                "Failed to sync target temperature to %s", entity_id
            )

    async def _async_set_real_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Mirror our on/off state onto the real thermostat if sync is enabled.

        Without this, turning the PID off only zeroes the heating demand but
        leaves the real valve displaying its last target temperature. Gated by
        the same option as target-temperature sync, so users who manage the
        real thermostat independently are unaffected.
        """
        if not self._entry.options.get(
            CONF_SYNC_TARGET_TEMP, DEFAULT_SYNC_TARGET_TEMP
        ):
            return

        entity_id = self._real_thermostat_entity
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.warning(
                "Real thermostat %s is not available, skipping HVAC mode sync",
                entity_id,
            )
            return

        # Already in the desired mode - avoid a redundant write.
        if state.state == hvac_mode:
            return

        try:
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": entity_id, "hvac_mode": hvac_mode},
                blocking=True,
            )
        except Exception:
            _LOGGER.exception("Failed to sync HVAC mode to %s", entity_id)

    async def _async_recalculate_and_send(self) -> None:
        """Run PID calculation and send valve position if it changed."""
        async with self._lock:
            if self._sensor_is_stale():
                self._mark_sensor_stale()
                return
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
            # Only write when the value changed; the periodic refresh keeps
            # the thermostat in external-control mode. Saves Zigbee traffic
            # and TRV battery.
            if valve != self._last_sent_valve:
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
            self._last_sent_valve = None
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
            self._last_sent_valve = valve
        except Exception:
            self._last_sent_valve = None
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
            "sensor_stale": self._sensor_is_stale(),
            "kp": self._pid.kp,
            "ki": self._pid.ki,
            "kd": self._pid.kd,
            "floor_value": self._pid.floor_value,
            "off_threshold": self._pid.off_threshold,
            "integral_max": self._pid.integral_max,
            "update_interval_min": self._entry.options.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            ),
            "pid_sample_interval_sec": self._entry.options.get(
                CONF_PID_SAMPLE_INTERVAL, DEFAULT_PID_SAMPLE_INTERVAL
            ),
            "sensor_stale_timeout_min": self._entry.options.get(
                CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT
            ),
            "sync_target_temperature": self._entry.options.get(
                CONF_SYNC_TARGET_TEMP, DEFAULT_SYNC_TARGET_TEMP
            ),
            "boost_threshold": self._pid.boost_threshold,
            "boost_value": self._pid.boost_value,
            "boost_active": self._pid.last_boost_active,
        }
