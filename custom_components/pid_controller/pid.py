"""PID controller algorithm with floor value logic."""

from __future__ import annotations

import time


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
    ) -> None:
        """Initialize PID controller."""
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.floor_value = floor_value
        self.off_threshold = off_threshold
        self.integral_max = integral_max

        self._integral: float = 0.0
        self._last_error: float | None = None
        self._last_time: float | None = None

        # Last computed components for debugging
        self.last_p: float = 0.0
        self.last_i: float = 0.0
        self.last_d: float = 0.0
        self.last_floor_active: bool = False

    def reset(self) -> None:
        """Reset PID state."""
        self._integral = 0.0
        self._last_error = None
        self._last_time = None

    def update_params(
        self,
        kp: float,
        ki: float,
        kd: float,
        floor_value: int,
        off_threshold: float,
        integral_max: float,
    ) -> None:
        """Update PID parameters at runtime."""
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.floor_value = floor_value
        self.off_threshold = off_threshold
        self.integral_max = integral_max

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

        if not is_heating or target_temp is None:
            self.reset()
            return 0

        # If significantly above target, close valve completely
        if current_temp >= target_temp + self.off_threshold:
            self.reset()
            return 0

        now = time.monotonic()
        error = target_temp - current_temp  # positive = needs heating

        # Proportional
        self.last_p = self.kp * error

        # Integral and Derivative
        if self._last_time is not None and self._last_error is not None:
            dt = now - self._last_time
            if dt > 0:
                self._integral += self.ki * error * dt
                self._integral = max(
                    -self.integral_max, min(self.integral_max, self._integral)
                )
                self.last_d = self.kd * (error - self._last_error) / dt
            else:
                self.last_d = 0.0
        else:
            self.last_d = 0.0

        self.last_i = self._integral
        self._last_error = error
        self._last_time = now

        raw_output = self.last_p + self.last_i + self.last_d
        raw_output = max(0.0, min(100.0, raw_output))

        # Floor logic: keep valve at minimum opening when heating is needed
        if raw_output < self.floor_value and current_temp < target_temp:
            self.last_floor_active = True
            return self.floor_value

        return int(raw_output)
