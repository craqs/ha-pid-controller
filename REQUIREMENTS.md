I want you to create a PID controller running on thermostats expoed in Home Assistant. My current setup is:
  - a few of thermostats connected via mqtt to Home Assistant and exposed as normal thermostats - (Bosch Radiator Thermostat II) - https://www.zigbee2mqtt.io/devices/BTH-RA.html
  - schedy - home assistant addon that schedules and sets temperatures on thermostat entities. I use it because it supports multiple layers of thermostat settings applied on top of each other (which allows me to achieve stateful approach to control
  thermostats) - https://hass-apps.readthedocs.io/en/stable/apps/schedy/

# Rationale
I'm not satisfied with PID running in my Bosch thermostats, it closes valve too often causing "cold feeling" in room (despite the fact that temperature is set ok). I'd prefer PID to first drop to some small value of valve opening (like 20-30% - should be adjustable), and then drop to 0% if current temperature is far higher than desired one.

# Algorithm
Schedy controls thermostats by just setting desired temperature (which works fine), but I'm not satisfied of PID algorithm used in thermostats. They tend to close valve too quickly just to open it after some time again when temperature drops. I'd
prefer the approach where valve is slightly open for most of the time to avoid dropping temperature too much. My wife complains when valve is completely closed, because she feels cold. If valve could use some sort of "floor" value to keep the valve
slighly open (e.g. 20% instead of 0%) until the difference between desired and current temperature is not so big, then that would solve the issue.

# Integration with HA
I think the custom PID controller could be easily integrated with my current environment if this new addon/integration would expose "virtual" thermostats that could be still controlled by schedy. Virtual thermostat would be used then as an interface
for custom PID controller.

# Integration with thermostats
My thermostats support setting valve directly ("PI heating demand" with values from 0% to 100%), so PID controller could just directly set this value on real thermostats. The only catch I'm aware of is that you need to probably set this value every
15-20 minutes to make sure that original PID algorithm won't step into the process.

If you have any questions, you need to clarify something, just don't hesitate and ask me
