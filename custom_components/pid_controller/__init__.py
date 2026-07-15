"""PID Controller for Radiator Thermostats."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BOOST_THRESHOLD,
    CONF_BOOST_VALUE,
    CONF_FLOOR_VALUE,
    CONF_INTEGRAL_MAX,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_OFF_THRESHOLD,
    DEFAULT_BOOST_THRESHOLD,
    DEFAULT_BOOST_VALUE,
    DEFAULT_FLOOR_VALUE,
    DEFAULT_INTEGRAL_MAX,
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    DEFAULT_OFF_THRESHOLD,
    DOMAIN,
)
from .pid import PIDController

PLATFORMS = ["climate"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PID Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    pid = PIDController(
        kp=entry.options.get(CONF_KP, DEFAULT_KP),
        ki=entry.options.get(CONF_KI, DEFAULT_KI),
        kd=entry.options.get(CONF_KD, DEFAULT_KD),
        floor_value=entry.options.get(CONF_FLOOR_VALUE, DEFAULT_FLOOR_VALUE),
        off_threshold=entry.options.get(CONF_OFF_THRESHOLD, DEFAULT_OFF_THRESHOLD),
        integral_max=entry.options.get(CONF_INTEGRAL_MAX, DEFAULT_INTEGRAL_MAX),
        boost_threshold=entry.options.get(CONF_BOOST_THRESHOLD, DEFAULT_BOOST_THRESHOLD),
        boost_value=entry.options.get(CONF_BOOST_VALUE, DEFAULT_BOOST_VALUE),
    )

    hass.data[DOMAIN][entry.entry_id] = {"pid": pid}

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so all options (including intervals) take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
