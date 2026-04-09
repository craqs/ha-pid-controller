# PID Controller for Radiator Thermostats

A Home Assistant custom integration that provides virtual thermostats with a custom PID algorithm designed to keep radiator valves slightly open, preventing the "cold feeling" caused by aggressive valve cycling.

## Problem

Many radiator thermostats (e.g., Bosch BTH-RA) have built-in PID controllers that close the valve too aggressively. Even when the average temperature is correct, frequent full-close/full-open cycles create uncomfortable temperature swings.

## Solution

This integration creates virtual climate entities that:
- Accept target temperature from schedulers like [Schedy](https://hass-apps.readthedocs.io/en/stable/apps/schedy/)
- Run a custom PID algorithm with a **floor value** — a configurable minimum valve opening (e.g., 25%)
- Directly control the radiator valve via the thermostat's PI heating demand entity
- Periodically refresh the valve position to prevent the built-in PID from taking over

### Floor Value Logic

- When heating is needed, the valve never drops below the floor value (default 25%)
- The valve only closes to 0% when the room temperature exceeds the target by a configurable threshold (default 1°C)
- This keeps a gentle flow of warm water through the radiator, preventing the "cold feeling"

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "PID Controller for Radiator Thermostats"
3. Install and restart Home Assistant

### Manual

Copy the `custom_components/pid_controller` directory to your Home Assistant's `custom_components` directory and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "PID Controller"
3. Configure:
   - **Name**: Friendly name for the virtual thermostat
   - **Temperature source entity**: Sensor or climate entity providing current room temperature
   - **Real thermostat entity**: Your Zigbee thermostat — the PI heating demand entity is auto-detected from the same device

### PID Tuning

After setup, click **Configure** on the integration entry to adjust all parameters. See the [PID Tuning Guide](#pid-tuning-guide) below for detailed explanations.

## How It Works

```
Schedy (or manual control)
  │
  └─ set_temperature(22°C) on virtual thermostat
       │
       ├─ Every 60s: PID recalculates valve position
       │     reads current temp from temperature source
       │     computes valve % with floor logic
       │     writes to PI heating demand entity
       │
       └─ Every 15min: refreshes valve position
             prevents built-in PID takeover
```

## Requirements

- Thermostats must expose a PI heating demand entity (number entity, 0-100%) via Zigbee2MQTT or similar
- Tested with Bosch Radiator Thermostat II (BTH-RA)

## PID Tuning Guide

This section explains every tunable parameter, what it does, and how to adjust it for your setup.

### Understanding the PID Algorithm

A PID controller continuously calculates an **error** — the difference between where you want the temperature to be (target) and where it actually is (current):

```
error = target_temperature - current_temperature
```

A positive error means the room is too cold. The controller then combines three terms to decide how far to open the valve:

```
valve_output = P + I + D
```

Each term serves a different purpose:

- **P (Proportional)**: Reacts to the *current* error. Big error → open valve wide. Small error → open valve a little.
- **I (Integral)**: Reacts to *accumulated* error over time. If the room has been slightly too cold for a long time, this term slowly increases the valve opening to compensate.
- **D (Derivative)**: Reacts to *how fast* the error is changing. If the temperature is rising quickly toward the target, this term reduces the valve opening to prevent overshooting.

On top of the standard PID, this integration adds **floor value logic** and an **off threshold** — explained below — which are the key features that prevent the "cold feeling" problem.

### Parameter Reference

| Parameter | Default | Range | Unit |
|---|---|---|---|
| Kp (Proportional gain) | 15.0 | 0–100 | %/°C |
| Ki (Integral gain) | 0.005 | 0–1 | %/(°C·s) |
| Kd (Derivative gain) | 2.0 | 0–100 | %·s/°C |
| Floor value | 25 | 0–100 | % |
| Off threshold | 1.0 | 0–5 | °C |
| Valve refresh interval | 15 | 5–60 | minutes |
| PID calculation interval | 60 | 10–300 | seconds |
| Min temperature | 5.0 | 0–15 | °C |
| Max temperature | 30.0 | 15–40 | °C |
| Integral anti-windup limit | 100.0 | 10–500 | — |

### Kp — Proportional Gain

**What it does**: Multiplies the current error to produce the proportional term: `P = Kp × error`.

**Example**: With Kp=15 and the room 1°C below target, P = 15 × 1 = 15% valve opening. If the room is 2°C below target, P = 30%.

**How to tune**:
- **Too low** (e.g., 5): The valve opens too little. The room heats very slowly and may never reach the target temperature. The integral term will eventually compensate, but response is sluggish.
- **Too high** (e.g., 50): The valve reacts too aggressively to small temperature differences. You'll see the valve jumping between wide open and the floor value. Temperature may oscillate around the setpoint.
- **Start here**: Leave at 15. If the room takes too long to warm up after a setpoint change, increase by 5. If you see the valve constantly swinging between high and low values, decrease by 5.

### Ki — Integral Gain

**What it does**: Accumulates error over time: `I += Ki × error × dt`. This eliminates *steady-state error* — the situation where the proportional term alone can't get the temperature exactly to the target.

**Example**: If the room is consistently 0.3°C below target, the integral term slowly builds up over minutes/hours, gradually increasing the valve opening until the temperature reaches the exact target.

**How to tune**:
- **Too low** (e.g., 0.001): The system is very slow to correct small persistent offsets. The room might settle 0.2–0.5°C below target.
- **Too high** (e.g., 0.05): The integral accumulates too quickly, causing the temperature to overshoot the target, then undershoot, then overshoot again (oscillation). This is the most common PID tuning mistake.
- **Start here**: Leave at 0.005. If the room consistently settles slightly below target (check over 1–2 hours), double it to 0.01. If you see slow temperature oscillations (period of 30+ minutes), halve it.
- **Note**: The integral is reset to zero whenever the temperature exceeds `target + off_threshold` or when HVAC mode is set to OFF. This prevents the integral from accumulating a huge value during long heating periods.

### Kd — Derivative Gain

**What it does**: Reacts to how fast the error is changing: `D = Kd × (error_change / time_change)`. Acts as a brake — when temperature is rising quickly, it reduces valve output to prevent overshooting.

**Example**: If the temperature jumped 0.5°C in the last 60 seconds toward the target, the derivative term produces a negative contribution, slightly closing the valve to prevent overshoot.

**How to tune**:
- **Too low** (e.g., 0): No braking effect. Temperature may overshoot the target, especially in well-insulated rooms where heat builds up.
- **Too high** (e.g., 20): The controller becomes oversensitive to any temperature fluctuation. Sensor noise (normal ±0.1°C variations) causes erratic valve behavior.
- **Start here**: Leave at 2. Most radiator heating systems are slow enough that derivative control is less critical than in faster systems. If you see overshooting (temperature goes 0.5°C+ above target before settling), increase to 5. If the valve position seems noisy/jittery, decrease to 1 or 0.
- **Tip**: Radiator heating has very long time constants (it takes minutes for valve changes to affect room temperature). The derivative term is the least important of the three for this application. When in doubt, reduce it.

### Floor Value — Minimum Valve Opening

**What it does**: When the PID calculates a valve position that is greater than 0% but less than the floor value, the floor value is used instead. Also, when the current temperature is below the target but the PID output is 0 (e.g., due to a large derivative term), the floor value is applied.

**This is the key feature of this integration.** Standard PIDs close the valve to 0% as the temperature approaches the target. This stops hot water flow through the radiator, which cools down and creates the "cold feeling" even though the air temperature sensor reads correctly.

**Example**: With floor_value=25 and the room 0.2°C below target, the PID might calculate 3% valve opening. Instead of 3%, the valve stays at 25%, keeping a gentle flow of warm water through the radiator.

**The valve goes to 0% only when** the room temperature exceeds `target + off_threshold` (see below).

**How to tune**:
- **Too low** (e.g., 10%): You might still feel cold near the radiator because the water flow is too low to keep the radiator noticeably warm.
- **Too high** (e.g., 50%): The room may overshoot the target temperature because the minimum heat output is substantial. You'll rely more on the off threshold to eventually close the valve.
- **Start here**: 25% is a good starting point for typical European radiator systems. If your wife still feels cold near the radiator, increase to 30–35%. If the room frequently overshoots the target, decrease to 20%.
- **Tip**: You can check if the floor is active by looking at the `floor_active` attribute on the virtual thermostat entity in HA Developer Tools → States.

### Off Threshold — When to Fully Close the Valve

**What it does**: The valve is only allowed to close to 0% when: `current_temperature >= target_temperature + off_threshold`. Until this condition is met, the valve stays at least at the floor value.

**This works hand-in-hand with the floor value.** Together they create a "comfort band":
- Below target: valve at PID-calculated value (but at least floor_value)
- Between target and target + off_threshold: valve at floor_value (gentle heating continues)
- Above target + off_threshold: valve at 0% (heating stops)

**Example**: Target is 22°C, off_threshold is 1.0°C.
- Room at 21.5°C → PID active, valve at calculated value (minimum 25%)
- Room at 22.3°C → PID says low/zero, but floor value kicks in: valve at 25%
- Room at 23.0°C → valve closes to 0%, heating stops completely

**How to tune**:
- **Too low** (e.g., 0.2°C): The valve will close to 0% almost as soon as the target is reached. You're back to the same problem as the built-in PID — frequent on/off cycling.
- **Too high** (e.g., 3°C): The valve stays open long after the room is warm enough. The room consistently overshoots by several degrees before the valve closes. Wasted energy.
- **Start here**: 1.0°C. If the room still cycles uncomfortably (valve frequently goes 0% → floor → 0%), increase to 1.5°C. If the room gets too warm before the valve closes, decrease to 0.5°C.
- **Tip**: In rooms with poor insulation (temperature drops quickly when heating stops), use a higher threshold (1.5–2.0°C) to keep the valve open longer.

### Valve Refresh Interval

**What it does**: Every N minutes, the integration re-sends the current valve position to the thermostat, even if the value hasn't changed. This is necessary because the Bosch BTH-RA (and similar thermostats) will revert to their built-in PID algorithm if they don't receive an external valve command within a timeout period.

**How to tune**:
- The BTH-RA typically requires a command every ~20 minutes. The default of 15 minutes provides a safety margin.
- **If you notice the thermostat occasionally "taking over"** (valve position suddenly changes without the PID doing it), decrease to 10 minutes.
- **If you want to reduce Zigbee traffic**, increase to 18–20 minutes, but don't exceed 20 for BTH-RA.
- This is independent of the PID calculation interval — it's purely about keeping the thermostat in "external control" mode.

### PID Calculation Interval

**What it does**: How often (in seconds) the PID algorithm recalculates the valve position and sends it to the thermostat.

**How to tune**:
- **60 seconds (default)**: Good balance. Room temperature changes slowly, so more frequent calculations don't improve comfort. Each calculation triggers a Zigbee command, so lower intervals mean more radio traffic.
- **Decrease to 30s**: If you want faster response to temperature changes (e.g., someone opens a window). More Zigbee traffic.
- **Increase to 120–300s**: If you have many thermostats and want to reduce Zigbee network load. Response is slower but for a stable room this is fine.
- **Tip**: The PID also recalculates immediately when Schedy changes the target temperature, regardless of this interval.

### Min/Max Temperature

**What it does**: Limits the setpoint range exposed by the virtual thermostat in the HA UI and to Schedy.

- **Min temperature** (default 5°C): The lowest target temperature that can be set. Typically used for frost protection.
- **Max temperature** (default 30°C): The highest target temperature that can be set.

These don't affect the PID algorithm itself — they only constrain the `set_temperature` service call.

### Integral Anti-Windup Limit

**What it does**: Clamps the integral term to the range [-limit, +limit]. Without this, the integral can accumulate to extremely large values during extended periods where the heating system can't reach the target (e.g., very cold outside, undersized radiator).

**Example**: Without anti-windup, if the room is 3°C below target for 2 hours, the integral might accumulate to 500+. When the temperature finally approaches the target, the integral "memory" keeps the valve wide open, causing massive overshoot. The anti-windup limit prevents this.

**How to tune**:
- **100 (default)**: The integral term can contribute up to 100% valve opening on its own. For most setups this is fine — the clamp rarely activates because the integral gain (Ki) is small.
- **Decrease to 50**: If you see large temperature overshoots after extended heating-up periods. This limits how much "history" the integral can accumulate.
- **Increase to 200**: If the system struggles to reach the target and you've already increased Ki. A higher limit allows the integral to compensate for larger persistent errors.
- **Note**: The integral is also reset when the temperature crosses the off threshold, which naturally prevents windup in normal operation.

### Tuning Workflow — Step by Step

If you're starting from scratch, follow this sequence:

1. **Start with defaults.** Set target temperature and observe for 1–2 hours.

2. **Check the debug attributes** in Developer Tools → States:
   - `valve_position`: Is it changing or stuck?
   - `pid_p`, `pid_i`, `pid_d`: Which term is dominant?
   - `floor_active`: Is the floor value being used frequently?

3. **Tune floor_value and off_threshold first.** These are the comfort parameters unique to this integration. Get these right before touching Kp/Ki/Kd:
   - If someone still feels cold → increase floor_value (try 30–35%)
   - If the room overshoots → decrease off_threshold or floor_value
   - If the valve cycles between 0% and floor_value frequently → increase off_threshold

4. **Then tune Kp.** Change the target temperature by 2°C and observe:
   - Valve should respond quickly but not slam to 100%
   - If too sluggish, increase Kp by 5
   - If too aggressive, decrease Kp by 5

5. **Then tune Ki.** Set the target and wait 1–2 hours:
   - If temperature settles 0.3°C+ below target, double Ki
   - If temperature slowly oscillates (30+ min cycles), halve Ki

6. **Leave Kd last.** Usually the default is fine. Only increase if you see consistent overshooting that Kp/Ki adjustments didn't fix.

### Example Configurations

**Comfortable living room** (prioritize steady warmth, no cold feeling):
```
Kp: 15, Ki: 0.005, Kd: 2
Floor value: 30%, Off threshold: 1.5°C
```

**Well-insulated bedroom** (temperature stays stable, less heating needed):
```
Kp: 10, Ki: 0.003, Kd: 1
Floor value: 20%, Off threshold: 0.8°C
```

**Drafty room / large windows** (temperature drops fast, needs aggressive heating):
```
Kp: 25, Ki: 0.008, Kd: 3
Floor value: 35%, Off threshold: 2.0°C
```

**Energy-saving mode** (accept wider temperature swings for lower consumption):
```
Kp: 10, Ki: 0.003, Kd: 1
Floor value: 15%, Off threshold: 0.5°C
```

## Debugging

The virtual thermostat exposes extra state attributes:
- `valve_position`: Current valve opening percentage
- `pid_p`, `pid_i`, `pid_d`: PID component values
- `floor_active`: Whether the floor value is currently being applied
- `heating_demand_entity`: The auto-detected entity being controlled
