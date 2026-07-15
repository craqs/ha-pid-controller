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

- When heating is needed and the room is at or below the target, the valve never drops below the floor value (default 25%)
- Between the target and `target + off_threshold`, the floor **decays proportionally** — e.g., at 0.3°C above target with off_threshold=1.0, the effective floor is `25% × (1 - 0.3/1.0) = 17%`
- The valve only closes to 0% when the room temperature exceeds the target by the off threshold (default 1°C)
- This keeps a gentle flow of warm water through the radiator, preventing the "cold feeling"

### Boost Mode

When the room is significantly below the target temperature (by default 1.5°C or more), the valve is forced to 100% to warm up the room as quickly as possible. Once the temperature gets closer to the target, normal PID control takes over. Boost can be disabled by setting the threshold to 0.

Boost exits with a small hysteresis (0.2°C below the entry threshold) so sensor noise at the boundary doesn't slam the valve back and forth, and the PID's time bookkeeping keeps running during boost so the handoff back to PID control is smooth (no accumulated integral jump).

### Safety & Robustness

- **Sensor stale failsafe**: If the temperature source stops reporting (dead battery, Zigbee dropout), the integration pauses valve writes so the thermostat's built-in controller can take over as a fallback — instead of heating forever on a frozen reading.
- **Takeover detection**: The integration watches the heating demand entity; if the built-in PID overwrites the valve position, it re-asserts its value immediately instead of waiting for the next periodic refresh.
- **Minimal Zigbee traffic**: Valve positions are only written when they change; the periodic refresh alone keeps the thermostat in external-control mode. This saves TRV battery.

### Target Temperature Sync

Optionally syncs the target temperature to the real thermostat (enabled by default). This keeps the thermostat's display correct and makes the built-in PID a safer fallback if the integration stops sending commands.

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=craqs&repository=ha-pid-controller&category=integration)

Or manually:
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
   - **Real thermostat entity**: Your Zigbee thermostat — the PI heating demand entity is auto-detected from the same device (if auto-detection fails, you can pick the number entity manually)

To change these entities later, use **Reconfigure** on the integration entry — no need to delete and re-add.

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
       │     writes to PI heating demand entity (only when changed)
       │
       ├─ Every 15min: refreshes valve position
       │     prevents built-in PID takeover
       │
       └─ On heating demand entity change: takeover detection
             re-asserts our valve position if the built-in
             PID overwrote it (rate-limited to 1/min)
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
- **D (Derivative)**: Reacts to *how fast the measured temperature is changing*. If the temperature is rising quickly toward the target, this term reduces the valve opening to prevent overshooting. It acts on the temperature (not the error), so target changes from Schedy don't cause derivative kicks, and it is low-pass filtered (~5 min time constant) so sensor noise doesn't make the valve jitter.

On top of the standard PID, this integration adds **floor value logic** and an **off threshold** — explained below — which are the key features that prevent the "cold feeling" problem.

### Parameter Reference

| Parameter | Default | Range | Unit |
|---|---|---|---|
| Kp (Proportional gain) | 15.0 | 0–100 | %/°C |
| Ki (Integral gain) | 0.005 | 0–1 | %/(°C·s) |
| Kd (Derivative gain) | 2.0 | 0–100 | % per °C/h |
| Floor value | 25 | 0–100 | % |
| Off threshold | 1.0 | 0–5 | °C |
| Valve refresh interval | 15 | 5–60 | minutes |
| PID calculation interval | 60 | 10–300 | seconds |
| Min temperature | 5.0 | 0–15 | °C |
| Max temperature | 30.0 | 15–40 | °C |
| Integral anti-windup limit | 100.0 | 10–500 | — |
| Boost threshold | 1.5 | 0–10 | °C |
| Boost valve opening | 100 | 0–100 | % |
| Sensor stale timeout | 60 | 0–360 | minutes |
| Sync target temperature | On | On/Off | — |

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
- **Note**: The integral is reset to zero whenever the temperature exceeds `target + off_threshold` or when HVAC mode is set to OFF. It also doesn't accumulate while the output is already saturated (0% or 100%) in the direction of the error — so long warmups don't wind it up — and it's protected against negative windup while the floor value is holding the valve open above target.

### Kd — Derivative Gain

**What it does**: Brakes on the temperature slope: `D = -Kd × (temperature change in °C per hour)`. When the room is warming quickly, this produces a negative contribution that closes the valve early to prevent overshoot.

Two implementation details matter here:
- **Derivative on measurement**: D reacts to the measured temperature, not the error. A setpoint jump from Schedy therefore causes no derivative kick.
- **Low-pass filtered** (~5 minute time constant): a single 0.1°C sensor step doesn't spike the valve; only sustained temperature movement produces braking.

**Example**: With Kd=2 and the room warming at 1.5°C/hour (typical radiator warmup), D ≈ -3% valve. With Kd=10, the same warmup brakes by -15%.

**How to tune**:
- **Too low** (e.g., 0): No braking effect. Temperature may overshoot the target, especially in well-insulated rooms where heat builds up.
- **Too high** (e.g., 50): The controller closes the valve hard during normal warmup, making heating sluggish.
- **Start here**: Leave at 2 for gentle braking. If you see overshooting (temperature goes 0.5°C+ above target before settling), increase to 5–10. If warmup near the target feels too slow, decrease.
- **Tip**: Radiator heating has very long time constants. The derivative term is the least important of the three for this application. When in doubt, reduce it.

### Floor Value — Minimum Valve Opening

**What it does**: When the PID calculates a valve position that is greater than 0% but less than the floor value, the floor value is used instead. Also, when the current temperature is below the target but the PID output is 0 (e.g., due to a large derivative term), the floor value is applied.

**This is the key feature of this integration.** Standard PIDs close the valve to 0% as the temperature approaches the target. This stops hot water flow through the radiator, which cools down and creates the "cold feeling" even though the air temperature sensor reads correctly.

**Example**: With floor_value=25 and the room 0.2°C below target, the PID might calculate 3% valve opening. Instead of 3%, the valve stays at 25%, keeping a gentle flow of warm water through the radiator.

**Proportional decay above target**: When the temperature is between the target and `target + off_threshold`, the floor decays linearly rather than dropping to 0% abruptly. The formula is:

```
effective_floor = floor_value × (1 - (current - target) / off_threshold)
```

For example, with floor_value=25, off_threshold=1.0, and the room 0.3°C above target:
`25 × (1 - 0.3/1.0) = 17%`. This smooth decay prevents the valve from jumping between floor and 0%.

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
- Between target and target + off_threshold: floor decays proportionally from floor_value to 0%
- Above target + off_threshold: valve at 0% (heating stops, PID resets)

**Example**: Target is 22°C, off_threshold is 1.0°C, floor_value is 25%.
- Room at 21.5°C → PID active, valve at calculated value (minimum 25%)
- Room at 22.3°C → effective floor = `25% × (1 - 0.3/1.0) = 17%`
- Room at 22.7°C → effective floor = `25% × (1 - 0.7/1.0) = 7%`
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
- Takeover is also detected event-driven: if the heating demand entity reports a value the integration didn't command, it re-asserts its position immediately (rate-limited to once a minute). The periodic refresh is the belt-and-suspenders backup.
- **If you want to reduce Zigbee traffic**, increase to 18–20 minutes, but don't exceed 20 for BTH-RA.
- This is independent of the PID calculation interval — it's purely about keeping the thermostat in "external control" mode.

### PID Calculation Interval

**What it does**: How often (in seconds) the PID algorithm recalculates the valve position and sends it to the thermostat.

**How to tune**:
- **60 seconds (default)**: Good balance. Room temperature changes slowly, so more frequent calculations don't improve comfort.
- A Zigbee command is only sent when the computed valve position actually **changes**, so a short interval doesn't flood the network while the room is stable.
- **Decrease to 30s**: If you want faster response to temperature changes.
- **Increase to 120–300s**: If you have many thermostats and want to minimize computation. Response is slower but for a stable room this is fine.
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
- **Saturation anti-windup (conditional integration)**: The integral doesn't accumulate while the output is already saturated (at 100% or 0%) in the direction of the error. Without this, a long warmup would wind the integral up to the limit and force an overshoot once the room reaches the target.
- **Floor anti-windup**: When the floor value is overriding the PID output above target, the integral is prevented from accumulating negative. Without this, the integral would build up a large negative value while the floor keeps the valve open, causing very slow recovery when heating is needed again.
- **Time-step clamp**: A single PID step never integrates more than 15 minutes of error, so gaps between calculations (HA restarts, stalls, long boost phases) can't inject a large integral jump.

### Boost Threshold — Fast Warmup Trigger

**What it does**: When the room temperature is this many degrees below the target, the valve is forced to the boost value (default 100%) for rapid warmup. Normal PID control resumes once the temperature gets closer to the target.

**Example**: With boost_threshold=1.5 and target=23°C, if the room is at 21°C (2°C below target), the valve goes straight to 100%. When the room warms to 21.7°C (1.3°C below — the threshold minus a 0.2°C exit hysteresis), boost deactivates and PID takes over. The hysteresis prevents the valve from chattering between 100% and the PID output when the temperature hovers right at the threshold.

**How to tune**:
- **Too low** (e.g., 0.5°C): Boost activates too often, even for small setpoint changes. The valve slams to 100% when it's not really needed.
- **Too high** (e.g., 5°C): Boost rarely activates. The room takes a long time to warm up after being cold (e.g., after opening windows or overnight setback).
- **Set to 0**: Disables boost entirely. The PID handles all warmup gradually.
- **Start here**: 1.5°C. If warmup from cold feels too slow, decrease to 1.0°C. If the valve goes to 100% too aggressively on small setpoint changes, increase to 2.0°C.

### Boost Valve Opening

**What it does**: The valve opening percentage used during boost mode.

**How to tune**:
- **100% (default)**: Maximum heat output during warmup. Best for getting the room to temperature quickly.
- **Decrease to 70–80%**: If 100% causes the radiator to make noise (some radiators click or hiss at full flow) or if you want a slightly gentler warmup.
- **Tip**: Check the `boost_active` attribute on the virtual thermostat to see when boost is engaged.

### Sensor Stale Timeout — Failsafe

**What it does**: If the temperature source doesn't deliver a new reading for this many minutes (dead battery, Zigbee dropout, sensor removed), the integration stops writing valve positions. Without fresh commands, the real thermostat's built-in controller takes over after its own timeout (~20 minutes on the BTH-RA) and regulates using its internal sensor and the synced target temperature — a safe fallback instead of heating forever on a frozen reading.

A warning is logged when the failsafe engages and an info message when the sensor recovers; the `sensor_stale` attribute on the virtual thermostat shows the current state. Control resumes automatically on the first fresh reading.

**How to tune**:
- **60 minutes (default)**: Safely above the reporting heartbeat of typical Zigbee temperature sensors (Xiaomi/Aqara report at least every ~50 minutes).
- **Decrease to 20–30 min**: If your sensor reports frequently and you want a faster failsafe.
- **Set to 0**: Disables the failsafe (previous behavior — not recommended).
- **Tip**: This works best with **Sync target temperature** enabled, so the built-in fallback regulates toward the right setpoint.

### Sync Target Temperature

**What it does**: When enabled, the target temperature set on the virtual thermostat is also sent to the real thermostat via the `climate.set_temperature` service. This is purely informational for the real thermostat — the custom PID still controls the valve directly via the heating demand entity.

**Why it matters**:
- **Display**: The real thermostat's screen shows the correct target temperature instead of a stale value.
- **Fallback safety**: If the integration stops sending valve commands (e.g., HA crashes), the real thermostat's built-in PID has a reasonable target to fall back on.
- **Default**: Enabled. There's rarely a reason to disable this unless you have a specific reason to keep the real thermostat's target independent.

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
Boost threshold: 1.5°C, Boost value: 100%
```

**Well-insulated bedroom** (temperature stays stable, less heating needed):
```
Kp: 10, Ki: 0.003, Kd: 1
Floor value: 20%, Off threshold: 0.8°C
Boost threshold: 1.0°C, Boost value: 80%
```

**Drafty room / large windows** (temperature drops fast, needs aggressive heating):
```
Kp: 25, Ki: 0.008, Kd: 3
Floor value: 35%, Off threshold: 2.0°C
Boost threshold: 2.0°C, Boost value: 100%
```

**Energy-saving mode** (accept wider temperature swings for lower consumption):
```
Kp: 10, Ki: 0.003, Kd: 1
Floor value: 15%, Off threshold: 0.5°C
Boost threshold: 0 (disabled)
```

## Debugging

The virtual thermostat exposes extra state attributes in Developer Tools → States:

**PID output:**
- `valve_position`: Current valve opening percentage (0–100)
- `pid_p`, `pid_i`, `pid_d`: Individual PID component values
- `floor_active`: Whether the floor value is currently overriding the PID output
- `boost_active`: Whether boost mode is currently engaged
- `sensor_stale`: Whether the temperature source stopped reporting (failsafe engaged, valve writes paused)

**Configuration (read-only mirrors of current settings):**
- `kp`, `ki`, `kd`: Current PID gains
- `floor_value`, `off_threshold`: Floor and off threshold settings
- `integral_max`: Anti-windup limit
- `boost_threshold`, `boost_value`: Boost mode settings
- `update_interval_min`: Valve refresh interval in minutes
- `pid_sample_interval_sec`: PID calculation interval in seconds
- `sensor_stale_timeout_min`: Stale sensor failsafe timeout in minutes
- `sync_target_temperature`: Whether target temp sync is enabled
- `heating_demand_entity`: The auto-detected entity being controlled
