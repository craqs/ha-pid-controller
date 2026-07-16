"""PID controller algorithm with floor value logic."""

from __future__ import annotations

import time

# Cap the time step used for integration/derivative. Protects against huge
# integral jumps after gaps between compute() calls (HA stalls, suspend).
MAX_DT = 900.0

# Boost exits this many degrees below the entry threshold, so sensor noise
# at the boundary doesn't slam the valve between boost_value and PID output.
BOOST_HYSTERESIS = 0.2

# Time constant (seconds) of the low-pass filter on the derivative term.
# Raw sensor steps (0.1 °C per sample) would otherwise dominate any
# usefully-sized kd.
D_FILTER_TAU = 300.0


def _pct(value: float) -> int:
    """Round a non-negative valve percentage to int, half-up."""
    return int(value + 0.5)


class PIDController:
    """PID controller with configurable floor value for radiator thermostats."""

    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        floor_value: int,
        off_threshold: float,
        integral_max: float,
        boost_threshold: float,
        boost_value: int,
    ) -> None:
        """Initialize PID controller."""
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.floor_value = floor_value
        self.off_threshold = off_threshold
        self.integral_max = integral_max
        self.boost_threshold = boost_threshold
        self.boost_value = boost_value

        self._integral: float = 0.0
        self._last_temp: float | None = None
        self._last_time: float | None = None
        self._d_filtered: float = 0.0
        self._boost_active: bool = False

        # Last computed components for debugging
        self.last_p: float = 0.0
        self.last_i: float = 0.0
        self.last_d: float = 0.0
        self.last_floor_active: bool = False
        self.last_boost_active: bool = False

    def reset(self) -> None:
        """Reset PID state."""
        self._integral = 0.0
        self._last_temp = None
        self._last_time = None
        self._d_filtered = 0.0
        self._boost_active = False

    def compute(
        self,
        current_temp: float,
        target_temp: float | None,
        is_heating: bool,
    ) -> int:
        """Compute valve position (0-100).

        Args:
            current_temp: Current room temperature.
            target_temp: Desired temperature, or None if not set.
            is_heating: Whether HVAC mode is HEAT (not OFF).

        Returns:
            Valve position as integer 0-100.
        """
        self.last_floor_active = False
        self.last_boost_active = False

        if not is_heating or target_temp is None:
            self.reset()
            self.last_p = 0.0
            self.last_i = 0.0
            self.last_d = 0.0
            return 0

        # If significantly above target, close valve completely
        if current_temp >= target_temp + self.off_threshold:
            self.reset()
            self.last_p = 0.0
            self.last_i = 0.0
            self.last_d = 0.0
            return 0

        now = time.monotonic()
        error = target_temp - current_temp  # positive = needs heating

        dt: float | None = None
        if self._last_time is not None:
            dt = now - self._last_time
            if dt <= 0:
                dt = None
            elif dt > MAX_DT:
                dt = MAX_DT

        # Derivative on measurement (not on error): immune to setpoint jumps.
        # kd is in %·h/°C, i.e. D = -kd × temperature slope in °C/hour.
        if dt is not None and self._last_temp is not None:
            slope_c_per_h = (current_temp - self._last_temp) / dt * 3600.0
            alpha = dt / (D_FILTER_TAU + dt)
            self._d_filtered += alpha * (-self.kd * slope_c_per_h - self._d_filtered)
        self.last_d = self._d_filtered

        self.last_p = self.kp * error

        # Boost: force valve open when far below target for fast warmup
        if self.boost_threshold <= 0:
            self._boost_active = False
        elif self._boost_active:
            self._boost_active = error > self.boost_threshold - BOOST_HYSTERESIS
        else:
            self._boost_active = error >= self.boost_threshold

        if self._boost_active:
            # Keep time/temperature bookkeeping running during boost so the
            # first PID tick after handoff sees a normal dt, not the whole
            # boost duration accumulated into the integral in one step.
            self.last_boost_active = True
            self.last_i = self._integral
            self._last_temp = current_temp
            self._last_time = now
            return _pct(self.boost_value)

        # Conditional integration: don't accumulate while the output is
        # already saturated in the direction of the error, otherwise the
        # integral winds up during long warmups and forces an overshoot.
        if dt is not None:
            provisional = self.last_p + self._integral + self.last_d
            saturated = (provisional >= 100.0 and error > 0) or (
                provisional <= 0.0 and error < 0
            )
            if not saturated:
                self._integral += self.ki * error * dt
                self._integral = max(
                    -self.integral_max, min(self.integral_max, self._integral)
                )

        self.last_i = self._integral
        self._last_temp = current_temp
        self._last_time = now

        raw_output = self.last_p + self.last_i + self.last_d
        raw_output = max(0.0, min(100.0, raw_output))

        # Floor logic: keep valve at minimum opening, decaying linearly
        # above target until off_threshold is reached. (We only get here
        # when current_temp < target_temp + off_threshold.)
        if current_temp <= target_temp:
            effective_floor = float(self.floor_value)
        else:
            overshoot_ratio = (current_temp - target_temp) / self.off_threshold
            effective_floor = self.floor_value * (1.0 - overshoot_ratio)

        if raw_output < effective_floor:
            # Anti-windup: when floor overrides the PID and temp is
            # above target, prevent negative integral windup.
            # Allow positive integral to decay toward zero (legitimate
            # unwinding) but clamp at zero to prevent accumulating a
            # large negative value.
            if error < 0 and self._integral < 0:
                self._integral = 0.0
                self.last_i = self._integral
            self.last_floor_active = True
            return _pct(effective_floor)

        return _pct(raw_output)
