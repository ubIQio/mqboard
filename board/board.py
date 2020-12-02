# Board configuration so other modules don't need to be too board-specific
# Copyright © 2020 by Thorsten von Eicken.
import machine

# Pull-in the specifics of the board from `board_config`. That should be the only file that is
# customized for each board. It also contains passwords and keys and should thus not be checked
# into public version control.
# There is a `board_config_tmpl.py` file around to use as template.
from board_config import *

# ===== LED stuff and battery voltage stuff

act_led = False  # network activity LED, typ. blue, turn on with act_led(True)
fail_led = False  # failure/error, type red, turn on with fail_led(True)
bat_volt_pin = None  # voltage divider pin to measure battery
bat_fct = 2  # voltage divider factor


def define_led():
    global act_led, fail_led, bat_volt_pin, bat_fct
    if kind == "iDAQ":
        bat_volt_pin = machine.ADC(machine.Pin(32))
        bat_volt_pin.atten(machine.ADC.ATTN_11DB)
        import neopixel
        np = neopixel.NeoPixel(machine.Pin(2), 1) # Just one neopixel, on IO02
        color = [64, 0, 0]
        np[0] = color
        np.write()

        def set_red(v):
            color[0] = 64 if v else 0
            np[0] = color
            np.write()

        def set_blue(v):
            color[2] = 64 if v else 0
            np[0] = color
            np.write()

        def set_green(v):
            color[1] = 64 if v else 0
            np[0] = color
            np.write()

        fail_led = set_red
        act_led = set_blue
        mqtt_led = set_green

    elif kind == "huzzah32":
        # Adafruit Huzzah32 feather
        lpin = machine.Pin(13, machine.Pin.OUT, None, value=0)
        led = lambda v: lpin(v)
        fail_led = led

    elif kind == "lolin-d32":
        # Wemos Lolin D-32
        lpin = machine.Pin(5, machine.Pin.OUT, None, value=1)
        led = lambda v: lpin(not v)
        bat_volt_pin = machine.ADC(machine.Pin(35))
        bat_volt_pin.atten(machine.ADC.ATTN_11DB)
        act_led, fail_led = (led, led)

define_led()
del define_led  # GC the function


def get_battery_voltage():
    """
    Returns the current battery voltage. If no battery is connected, returns 3.7V
    This is an approximation only, but useful to detect if the battery is getting low.
    """
    if bat_volt_pin == None:
        return 0
    measuredvbat = bat_volt_pin.read() / 4095
    measuredvbat *= 3.6 * bat_fct  # 3.6V at full scale
    return measuredvbat


# ===== Wifi stuff
# connect_wifi is a handy little function to manually connect wifi
def connect_wifi():
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    print("Connecting to", wifi_ssid, "...")
    wlan.connect(wifi_ssid, wifi_pass)
    while not wlan.isconnected():
        pass
    print("Connected!")
