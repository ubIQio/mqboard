import logging

log = logging.getLogger(__name__)
from uasyncio import Loop as loop, sleep_ms
from board import act_led
import machine
from machine import Pin
from machine import ADC

#Define Analogue Pins
# V_BATT Sensing
Vbatt_pin = machine.ADC(Pin(32))          # create ADC object on ADC pin
Vbatt_pin.atten(ADC.ATTN_11DB)      # set 11dB input attenuation (voltage range roughly 0.0v - 3.6v)
Vbatt_pin.width(ADC.WIDTH_12BIT)  # set 9 bit return values (returned range 0-511)
Vbatt_adjust = 0.96

# VIN Sensing
Vin_pin = machine.ADC(Pin(33))          # create ADC object on ADC pin
Vin_pin.atten(ADC.ATTN_11DB)    # set 11dB input attenuation (voltage range roughly 0.0v - 3.6v)
Vin_pin.width(ADC.WIDTH_12BIT)  # set 9 bit return values (returned range 0-511)
Vin_adjust = 1.016990291262136

# V_SYS Sensing
Vsys_pin = machine.ADC(Pin(34))          # create ADC object on ADC pin
Vsys_pin.atten(ADC.ATTN_11DB)    # set 11dB input attenuation (voltage range roughly 0.0v - 3.6v)
Vsys_pin.width(ADC.WIDTH_12BIT)  # set 9 bit return values (returned range 0-511)
Vsys_adjust = 1.016990291262136

# LiPo Charging Status Function

def chargingStatus():
    chargeStatus = "Error"
    if Pin(27, Pin.IN, Pin.PULL_DOWN).value() == 1:
        chargeStatus = "Fully Charged"
    else:
        if Pin(27, Pin.IN, Pin.PULL_UP).value() == 0:
            chargeStatus = "Charging"
        else:
            if (((Vbatt_pin.read() * (3.6/4095))*2) * Vbatt_adjust) >= 4.0:
                chargeStatus = "No Battery"
            else:
                chargeStatus = "Using Battery"
    return chargeStatus

class Blinker:
    def __init__(self, mqclient, topic, period, b_topic):
        self.mqclient = mqclient
        self.topic = topic
        self.period = period
        self.b_topic= b_topic

    async def blinker(self,mqclient):
        while True:
            act_led(1)
            await sleep_ms(self.period // 2)
            act_led(0)
            await sleep_ms(self.period // 2)
            # log.info("Charging Status = %s", chargingStatus())
            # log.info("V_BATT: %.2fv", ((Vbatt_pin.read() * (3.6/4095))*2) * Vbatt_adjust)
            # compose json message with battery data
            msg = '{"Charging Status":"%s","V_BATT":%.2f}' % (
                chargingStatus(),
                ((Vbatt_pin.read() * (3.6/4095))*2) * Vbatt_adjust
            )
            log.debug(msg)
            await mqclient.publish(self.b_topic, msg, qos=0)

    def period(self, millisecs):
        self.period = millisecs

    def on_msg(self, topic, msg, retained, qos, dup):
        topic = str(topic, "utf-8")
        log.info("on_msg: %s (len=%d ret=%d qos=%d dup=%d)", topic, len(msg), retained, qos, dup)
        if topic == self.topic:
            try:
                p = int(msg)
                if p < 50 or p > 10000:
                    raise ValueError("period must be in 50..10000")
                self.period = p
            except Exception as e:
                log.exc(e, "Invalid incoming message")

    async def hook_it_up(self, mqtt):
        log.info("hook_it_up called")
        mqtt.on_msg(self.on_msg)
        await mqtt.client.subscribe(self.topic, qos=1)
        log.info("Subscribed to %s", self.topic)



# start is called by the module launcher loop in main.py; it is passed a handle onto the MQTT
# dispatcher and to the "blinky" config dict in board_config.py
def start(mqtt, config):
    period = config.get("period", 1000)  # get period from config with a default of 1000ms
    log.info("start called, period=%d", period)
    bl = Blinker(mqtt.client, config["topic"], period, config["b_topic"])
    loop.create_task(bl.blinker(mqtt.client))
    mqtt.on_init(bl.hook_it_up(mqtt))



