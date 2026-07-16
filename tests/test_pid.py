"""Tests for PID controller algorithm."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Import pid.py directly to avoid pulling in homeassistant via __init__.py
_pid_path = Path(__file__).resolve().parent.parent / "custom_components" / "pid_controller" / "pid.py"
_spec = importlib.util.spec_from_file_location("pid", _pid_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["pid"] = _mod
_spec.loader.exec_module(_mod)
PIDController = _mod.PIDController
MAX_DT = _mod.MAX_DT
BOOST_HYSTERESIS = _mod.BOOST_HYSTERESIS


def make_pid(**overrides):
    """Create a PIDController with sensible defaults, overridable."""
    defaults = dict(
        kp=15.0,
        ki=0.005,
        kd=2.0,
        floor_value=25,
        off_threshold=1.0,
        integral_max=100.0,
        boost_threshold=1.5,
        boost_value=100,
    )
    defaults.update(overrides)
    return PIDController(**defaults)


class _Clock:
    """Shared monotonic clock for tick()."""
    value = 0.0

    @classmethod
    def reset(cls):
        cls.value = 0.0


def tick(pid, current, target, is_heating=True, time_offset=None):
    """Advance time and compute. Manages monotonic time automatically."""
    if time_offset is not None:
        _Clock.value = time_offset
    else:
        _Clock.value += 60.0
    with patch("time.monotonic", return_value=_Clock.value):
        return pid.compute(current, target, is_heating)


@pytest.fixture(autouse=True)
def reset_clock():
    """Reset the shared clock before each test."""
    _Clock.reset()


# --- Basic behavior ---


class TestOffConditions:
    def test_returns_zero_when_not_heating(self):
        pid = make_pid()
        result = tick(pid, 20.0, 22.0, is_heating=False)
        assert result == 0

    def test_returns_zero_when_target_is_none(self):
        pid = make_pid()
        result = tick(pid, 20.0, None, is_heating=True)
        assert result == 0

    def test_resets_state_when_not_heating(self):
        pid = make_pid()
        tick(pid, 20.0, 22.0)
        tick(pid, 20.0, 22.0, is_heating=False)
        assert pid.last_p == 0.0
        assert pid.last_i == 0.0
        assert pid.last_d == 0.0

    def test_returns_zero_above_off_threshold(self):
        pid = make_pid(off_threshold=1.0)
        result = tick(pid, 24.0, 23.0)
        assert result == 0

    def test_resets_integral_above_off_threshold(self):
        pid = make_pid(kp=50.0, ki=0.01, kd=0, off_threshold=1.0, boost_threshold=0, floor_value=5)
        # Build up some integral - kp=50 means raw output > floor, so no anti-windup
        tick(pid, 22.5, 23.0, time_offset=0.0)
        tick(pid, 22.5, 23.0)
        assert pid._integral != 0.0
        # Now go above threshold
        tick(pid, 24.5, 23.0)
        assert pid._integral == 0.0

    def test_clears_debug_values_above_off_threshold(self):
        pid = make_pid(kp=50.0, ki=0.01, kd=0, off_threshold=1.0, boost_threshold=0, floor_value=5)
        tick(pid, 22.5, 23.0, time_offset=0.0)
        tick(pid, 22.5, 23.0)
        assert pid.last_p != 0.0
        assert pid.last_i != 0.0
        # Go above threshold - debug values should be cleared
        tick(pid, 24.5, 23.0)
        assert pid.last_p == 0.0
        assert pid.last_i == 0.0
        assert pid.last_d == 0.0


# --- Boost mode ---


class TestBoostMode:
    def test_boost_activates_when_far_below_target(self):
        pid = make_pid(boost_threshold=1.5, boost_value=100)
        result = tick(pid, 20.0, 23.0)
        assert result == 100
        assert pid.last_boost_active is True

    def test_boost_does_not_activate_within_threshold(self):
        pid = make_pid(boost_threshold=1.5, boost_value=100)
        result = tick(pid, 22.0, 23.0)
        assert result != 100
        assert pid.last_boost_active is False

    def test_boost_at_exact_threshold(self):
        pid = make_pid(boost_threshold=1.5, boost_value=100)
        result = tick(pid, 21.5, 23.0)  # exactly 1.5 below
        assert result == 100
        assert pid.last_boost_active is True

    def test_boost_disabled_when_threshold_zero(self):
        pid = make_pid(boost_threshold=0, boost_value=100)
        result = tick(pid, 18.0, 23.0)
        assert pid.last_boost_active is False

    def test_boost_uses_configured_value(self):
        pid = make_pid(boost_threshold=1.5, boost_value=75)
        result = tick(pid, 20.0, 23.0)
        assert result == 75

    def test_boost_exit_has_hysteresis(self):
        """Once active, boost only exits when error drops below
        threshold - hysteresis, so noise at the boundary doesn't chatter."""
        pid = make_pid(kp=15.0, ki=0, kd=0, boost_threshold=1.5, boost_value=100)

        # Enter boost at error 1.6
        result = tick(pid, 21.4, 23.0, time_offset=0.0)
        assert result == 100

        # Error 1.4: below entry threshold but above exit threshold (1.3)
        result = tick(pid, 21.6, 23.0)
        assert result == 100
        assert pid.last_boost_active is True

        # Error 1.25: below exit threshold - boost ends
        result = tick(pid, 21.75, 23.0)
        assert result != 100
        assert pid.last_boost_active is False

        # Error 1.4 again: must NOT re-enter (entry needs >= 1.5)
        result = tick(pid, 21.6, 23.0)
        assert pid.last_boost_active is False

    def test_boost_handoff_does_not_inject_integral(self):
        """Regression: time bookkeeping must keep running during boost.

        Previously _last_time froze at boost entry, so the first PID tick
        after a long boost integrated ki * error * <whole boost duration>
        in one step (~38% valve with defaults), guaranteeing overshoot.
        """
        pid = make_pid(kp=15.0, ki=0.005, kd=0, boost_threshold=1.5, boost_value=100)

        # Steady state at target, then Schedy raises the setpoint
        tick(pid, 20.0, 20.0, time_offset=0.0)
        tick(pid, 20.0, 20.0)

        # Boost for ~85 minutes of slow warmup
        temp = 20.0
        while 23.0 - temp >= 1.5:
            result = tick(pid, temp, 23.0)
            assert result == 100
            temp += 0.018

        # Continue until boost exits (hysteresis band)
        while pid.last_boost_active or 23.0 - temp > 1.3:
            result = tick(pid, temp, 23.0)
            temp += 0.018

        # First post-boost tick may only integrate one sample interval:
        # ki * error * 60 ~= 0.4, not ki * error * 5100 ~= 38
        assert 0.0 < pid._integral < 1.0
        # And the output must be smooth (P + tiny I, lifted to floor)
        assert result <= 30


# --- Floor value logic ---


class TestFloorValue:
    def test_floor_active_below_target(self):
        pid = make_pid(floor_value=25)
        # First tick to initialize time
        tick(pid, 22.5, 23.0, time_offset=0.0)
        # Second tick - P = 15 * 0.5 = 7.5, below floor of 25
        result = tick(pid, 22.5, 23.0)
        assert result == 25
        assert pid.last_floor_active is True

    def test_floor_not_active_when_pid_output_exceeds_floor(self):
        pid = make_pid(kp=50.0, floor_value=25)
        tick(pid, 22.0, 23.0, time_offset=0.0)
        result = tick(pid, 22.0, 23.0)
        # P = 50 * 1.0 = 50, well above floor
        assert result >= 50
        assert pid.last_floor_active is False

    def test_floor_decays_proportionally_above_target(self):
        pid = make_pid(floor_value=25, off_threshold=1.0)
        tick(pid, 23.3, 23.0, time_offset=0.0)
        result = tick(pid, 23.3, 23.0)
        # effective_floor = 25 * (1 - 0.3/1.0) = 17.5, but float representation
        # of 23.3 - 23.0 puts it a hair under -> rounds half-up to 17
        assert result == 17
        assert pid.last_floor_active is True

    def test_floor_decay_at_half_overshoot(self):
        pid = make_pid(floor_value=20, off_threshold=1.0)
        tick(pid, 23.5, 23.0, time_offset=0.0)
        result = tick(pid, 23.5, 23.0)
        # effective_floor = 20 * (1 - 0.5/1.0) = 10
        assert result == 10
        assert pid.last_floor_active is True

    def test_floor_near_off_threshold(self):
        pid = make_pid(floor_value=25, off_threshold=1.0)
        tick(pid, 23.9, 23.0, time_offset=0.0)
        result = tick(pid, 23.9, 23.0)
        # effective_floor = 25 * (1 - 0.9/1.0) = 2.5 -> rounds to 3
        assert result == 3
        assert pid.last_floor_active is True

    def test_floor_zero_disables_floor(self):
        pid = make_pid(floor_value=0, kp=4.0, ki=0, kd=0)
        tick(pid, 22.9, 23.0, time_offset=0.0)
        result = tick(pid, 22.9, 23.0)
        # P = 4 * 0.1 = 0.4 -> rounds to 0, floor is 0 so no override
        assert result == 0
        assert pid.last_floor_active is False


# --- PID computation ---


class TestPIDComputation:
    def test_proportional_term(self):
        pid = make_pid(kp=20.0, ki=0, kd=0)
        tick(pid, 22.0, 23.0, time_offset=0.0)
        tick(pid, 22.0, 23.0)
        assert pid.last_p == pytest.approx(20.0)

    def test_integral_accumulates(self):
        pid = make_pid(kp=0, ki=0.01, kd=0, floor_value=0)
        tick(pid, 22.0, 23.0, time_offset=0.0)
        # error=1.0, ki=0.01, dt=60 -> I += 0.01 * 1.0 * 60 = 0.6
        tick(pid, 22.0, 23.0)
        assert pid.last_i == pytest.approx(0.6)
        # Another tick: I += 0.6 -> total 1.2
        tick(pid, 22.0, 23.0)
        assert pid.last_i == pytest.approx(1.2)

    def test_integral_clamped_by_max(self):
        pid = make_pid(kp=0, ki=1.0, kd=0, integral_max=10.0, floor_value=0, boost_threshold=0)
        tick(pid, 22.0, 23.0, time_offset=0.0)
        # error=1.0, ki=1.0, dt=60 -> I = 60, clamped to 10
        tick(pid, 22.0, 23.0)
        assert pid._integral == pytest.approx(10.0)

    def test_integral_clamps_negative(self):
        # A large positive integral being unwound in one big step must be
        # clamped at -integral_max, not shoot past it.
        pid = make_pid(kp=0, ki=1.0, kd=0, integral_max=10.0, floor_value=0, boost_threshold=0, off_threshold=3.0)
        pid._integral = 15.0
        tick(pid, 23.5, 23.0, time_offset=0.0)
        # error=-0.5: provisional output 15 > 0, so integration proceeds:
        # I += 1.0 * -0.5 * 60 = -30 -> 15 - 30 = -15, clamped to -10
        tick(pid, 23.5, 23.0)
        assert pid._integral == pytest.approx(-10.0)

    def test_derivative_on_measurement(self):
        pid = make_pid(kp=0, ki=0, kd=2.0, floor_value=0)
        tick(pid, 22.0, 23.0, time_offset=0.0)
        # temp rose 0.5C in 60s = 30 C/h -> raw D = -2.0 * 30 = -60
        # low-pass: alpha = 60 / (300 + 60) = 1/6 -> D = -10
        tick(pid, 22.5, 23.0)
        assert pid.last_d == pytest.approx(-10.0)

    def test_derivative_immune_to_setpoint_change(self):
        """Derivative acts on measured temperature, so a target jump
        (Schedy schedule change) must not produce a D kick."""
        pid = make_pid(kp=0, ki=0, kd=2.0, floor_value=0)
        tick(pid, 22.0, 22.5, time_offset=0.0)
        tick(pid, 22.0, 22.5)
        assert pid.last_d == pytest.approx(0.0)
        # Target jumps 1.5C, temperature unchanged -> no derivative kick
        tick(pid, 22.0, 24.0 - 0.01)  # stay below boost threshold
        assert pid.last_d == pytest.approx(0.0)

    def test_derivative_filter_smooths_sensor_steps(self):
        """A single 0.1C sensor step must not produce a full-size D spike."""
        pid = make_pid(kp=0, ki=0, kd=2.0, floor_value=0)
        tick(pid, 22.0, 23.0, time_offset=0.0)
        tick(pid, 22.1, 23.0)
        # Raw would be -2.0 * 6 C/h = -12; filtered = -12/6 = -2
        assert abs(pid.last_d) < 3.0
        # With no further movement it decays toward zero
        tick(pid, 22.1, 23.0)
        d_second = abs(pid.last_d)
        tick(pid, 22.1, 23.0)
        assert abs(pid.last_d) < d_second

    def test_dt_clamped_after_long_gap(self):
        """A long gap between computes (HA stall/suspend) must not integrate
        the whole gap in one step."""
        pid = make_pid(kp=0, ki=0.01, kd=0, floor_value=0, boost_threshold=0)
        tick(pid, 22.0, 23.0, time_offset=0.0)
        tick(pid, 22.0, 23.0, time_offset=10000.0)
        # Unclamped would be 0.01 * 1.0 * 10000 = 100; clamped to MAX_DT=900
        assert pid._integral == pytest.approx(0.01 * 1.0 * MAX_DT)

    def test_no_integration_while_output_saturated(self):
        """Conditional integration: while P alone saturates the output,
        the integral must not wind up (it would force an overshoot later)."""
        pid = make_pid(kp=30.0, ki=0.01, kd=0, boost_threshold=0, floor_value=25)
        tick(pid, 19.0, 23.0, time_offset=0.0)
        for _ in range(30):
            result = tick(pid, 19.0, 23.0)
            assert result == 100
        assert pid._integral == pytest.approx(0.0)

        # Once out of saturation, integration resumes
        tick(pid, 22.5, 23.0)
        assert pid._integral > 0.0

    def test_output_clamped_to_0_100(self):
        pid = make_pid(kp=100.0, ki=0, kd=0, boost_threshold=0)
        tick(pid, 18.0, 23.0, time_offset=0.0)
        result = tick(pid, 18.0, 23.0)
        # P = 100 * 5 = 500, should clamp to 100
        assert result == 100

    def test_output_integer(self):
        pid = make_pid()
        result = tick(pid, 22.0, 23.0, time_offset=0.0)
        assert isinstance(result, int)


# --- Anti-windup for floor override ---


class TestFloorAntiWindup:
    def test_integral_frozen_when_floor_overrides_above_target(self):
        """When floor is active and temp is above target, integral should not
        accumulate more negative."""
        pid = make_pid(kp=15.0, ki=0.005, kd=0, floor_value=25, off_threshold=1.0)

        # First tick to initialize
        tick(pid, 23.3, 23.0, time_offset=0.0)

        # Second tick - integral starts accumulating negative
        tick(pid, 23.3, 23.0)
        i_after_second = pid._integral

        # Third tick - floor is active, integral should be frozen
        tick(pid, 23.3, 23.0)
        i_after_third = pid._integral

        # Integral should not have gone more negative
        assert i_after_third >= i_after_second

    def test_integral_frozen_over_many_ticks(self):
        """Simulate the bedroom scenario: temp above target for extended period."""
        pid = make_pid(kp=20.0, ki=0.005, kd=0, floor_value=25, off_threshold=1.0)

        tick(pid, 23.4, 22.5, time_offset=0.0)
        tick(pid, 23.4, 22.5)
        i_after_first_real = pid._integral

        # Run 60 more ticks (simulating 60 minutes)
        for _ in range(60):
            tick(pid, 23.4, 22.5)

        # Integral should not have gone significantly more negative
        # (it should be frozen at around the value after the first real tick)
        assert pid._integral >= i_after_first_real

    def test_integral_not_frozen_when_floor_not_active(self):
        """When PID output exceeds floor, integral should accumulate normally."""
        pid = make_pid(kp=50.0, ki=0.005, kd=0, floor_value=5, off_threshold=1.0)

        tick(pid, 22.0, 23.0, time_offset=0.0)
        tick(pid, 22.0, 23.0)
        i_first = pid._integral
        tick(pid, 22.0, 23.0)
        i_second = pid._integral

        # Integral should have increased (positive error)
        assert i_second > i_first

    def test_integral_can_unwind_during_floor(self):
        """Even during floor override, integral should be able to move toward zero."""
        pid = make_pid(kp=15.0, ki=0.005, kd=0, floor_value=25, off_threshold=1.0)

        # Build up negative integral first
        pid._integral = -5.0

        # Now with positive error (below target), integral should move positive
        # even if floor is active
        tick(pid, 22.5, 23.0, time_offset=0.0)
        tick(pid, 22.5, 23.0)

        # Integral should have moved toward zero (become less negative)
        assert pid._integral > -5.0

    def test_integral_unwinds_toward_zero_during_floor_above_target(self):
        """When floor is active with positive integral and temp above target,
        integral should decay toward zero — not stay frozen."""
        pid = make_pid(kp=15.0, ki=0.005, kd=0, floor_value=25, off_threshold=1.0)

        # Build up positive integral (simulating prior heating demand)
        tick(pid, 22.5, 23.0, time_offset=0.0)
        for _ in range(50):
            tick(pid, 22.5, 23.0)
        i_positive = pid._integral
        assert i_positive > 0  # should have accumulated positive integral

        # Now temp overshoots above target — error is negative,
        # floor is still active (within off_threshold)
        tick(pid, 23.3, 23.0)
        i_after = pid._integral
        # Integral should have decreased (unwinding toward zero)
        assert i_after < i_positive

    def test_integral_does_not_go_negative_during_floor_above_target(self):
        """Integral must not accumulate negative windup during floor override
        with negative error."""
        pid = make_pid(kp=15.0, ki=0.005, kd=0, floor_value=25, off_threshold=1.0)

        # Start with zero integral, temp above target
        tick(pid, 23.3, 23.0, time_offset=0.0)
        # Multiple ticks with negative error during floor override
        for _ in range(100):
            tick(pid, 23.3, 23.0)

        # Integral must not go negative
        assert pid._integral >= 0

    def test_integral_accumulates_positive_during_floor_below_target(self):
        """When below target and floor is active, integral must keep growing
        so raw PID eventually exceeds the floor — the living room scenario."""
        pid = make_pid(kp=20.0, ki=0.005, kd=0, floor_value=20, off_threshold=1.0, boost_threshold=0)

        # cur=23.3, tgt=24.0 -> error=0.7, P=14, floor=20, floor active
        tick(pid, 23.3, 24.0, time_offset=0.0)
        tick(pid, 23.3, 24.0)
        i_first = pid._integral
        assert i_first > 0  # integral should be positive

        # After more ticks, integral should keep growing
        for _ in range(10):
            tick(pid, 23.3, 24.0)
        i_later = pid._integral
        assert i_later > i_first  # integral must keep accumulating

        # Eventually raw output (P + I) should exceed floor
        # P=14, need I >= 6 to exceed floor of 20
        for _ in range(200):
            tick(pid, 23.3, 24.0)
        assert pid._integral > 6.0  # should have accumulated enough
        assert pid.last_floor_active is False  # PID now exceeds floor


# --- Reset ---


class TestReset:
    def test_reset_clears_state(self):
        pid = make_pid()
        tick(pid, 22.0, 23.0, time_offset=0.0)
        tick(pid, 22.5, 23.0)
        pid.reset()
        assert pid._integral == 0.0
        assert pid._last_temp is None
        assert pid._last_time is None
        assert pid._d_filtered == 0.0
        assert pid._boost_active is False


# --- Integration scenarios ---


class TestScenarios:
    def test_warmup_from_cold(self):
        """Room starts cold, should see boost then floor."""
        pid = make_pid(boost_threshold=1.5, boost_value=100, floor_value=25)

        # Far below target - boost
        result = tick(pid, 19.0, 23.0, time_offset=0.0)
        assert result == 100
        assert pid.last_boost_active is True

        # Getting closer but still in boost range
        result = tick(pid, 21.0, 23.0)
        assert result == 100

        # Within 1.5 of target - PID takes over, floor likely active
        result = tick(pid, 22.0, 23.0)
        assert pid.last_boost_active is False
        assert result >= 25  # at least floor value

    def test_overshoot_and_recovery(self):
        """Room overshoots target, floor decays, then closes at threshold."""
        pid = make_pid(floor_value=25, off_threshold=1.0)

        tick(pid, 22.5, 23.0, time_offset=0.0)

        # At target - floor active
        result = tick(pid, 23.0, 23.0)
        assert result == 25
        assert pid.last_floor_active is True

        # 0.5 above target - floor decays
        result = tick(pid, 23.5, 23.0)
        # effective_floor = 25 * (1 - 0.5/1.0) = 12.5 -> rounds to 13
        assert result == 13

        # At off_threshold - valve closes
        result = tick(pid, 24.0, 23.0)
        assert result == 0

    def test_schedy_off_and_on(self):
        """Simulate Schedy turning off then on - verify clean state transitions."""
        pid = make_pid()

        # Heating active
        tick(pid, 22.0, 23.0, time_offset=0.0)
        result = tick(pid, 22.0, 23.0)
        assert result > 0

        # Schedy turns off
        result = tick(pid, 22.0, 23.0, is_heating=False)
        assert result == 0
        assert pid._integral == 0.0

        # Schedy turns back on
        result = tick(pid, 22.0, 23.0, is_heating=True)
        assert result > 0
