"""Constants for the PID Controller integration."""

DOMAIN = "pid_controller"

# Config keys
CONF_TEMPERATURE_ENTITY = "temperature_entity"
CONF_REAL_THERMOSTAT_ENTITY = "real_thermostat_entity"
CONF_HEATING_DEMAND_ENTITY = "heating_demand_entity"

# PID defaults
DEFAULT_KP = 15.0
DEFAULT_KI = 0.005
DEFAULT_KD = 2.0
DEFAULT_FLOOR_VALUE = 25
DEFAULT_OFF_THRESHOLD = 1.0
DEFAULT_UPDATE_INTERVAL = 15  # minutes - refresh valve to prevent built-in PID takeover
DEFAULT_PID_SAMPLE_INTERVAL = 60  # seconds - PID recalculation interval
DEFAULT_INTEGRAL_MAX = 100.0
DEFAULT_MIN_TEMP = 5.0
DEFAULT_MAX_TEMP = 30.0

# Option keys
CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"
CONF_FLOOR_VALUE = "floor_value"
CONF_OFF_THRESHOLD = "off_threshold"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_PID_SAMPLE_INTERVAL = "pid_sample_interval"
CONF_INTEGRAL_MAX = "integral_max"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_SYNC_TARGET_TEMP = "sync_target_temperature"
DEFAULT_SYNC_TARGET_TEMP = True
CONF_BOOST_THRESHOLD = "boost_threshold"
DEFAULT_BOOST_THRESHOLD = 1.5  # °C below target to activate boost
CONF_BOOST_VALUE = "boost_value"
DEFAULT_BOOST_VALUE = 100  # valve % during boost
CONF_STALE_TIMEOUT = "sensor_stale_timeout"
DEFAULT_STALE_TIMEOUT = 60  # minutes without a temperature update before failsafe; 0 disables
