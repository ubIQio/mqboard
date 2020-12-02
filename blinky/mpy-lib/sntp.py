# Simple NTP daemon for MicroPython using asyncio.
# Copyright (c) 2020 by Thorsten von Eicken
# Based on https://github.com/wieck/micropython-ntpclient by Jan Wieck
# See LICENSE file

try:
    import uasyncio as asyncio
    from sys import print_exception
except ImportError:
    import asyncio
import sys, socket, struct, time, logging, machine

TZ=1
TZ_US = TZ*60*60*1000000

def time_us():      # returns system time in us since 2000/1/1 
    return int(time.time()  * 1000000)


log = logging.getLogger(__name__)


# Offsets into the NTP packet
OFF_ORIG = 24
OFF_RX = 32
OFF_TX = 40

# Poll and adjust intervals
MIN_POLL = 64  # never poll faster than every 32 seconds
MAX_POLL = 1024  # default maximum poll interval

# (date(2000, 1, 1) - date(1900, 1, 1)).days * 24*60*60
NTP_DELTA = 3155673600

# ntp2mp converts from NTP seconds+fraction with an Epoch of 1900/1/1
# to MP microseconds with an Epoch of 2000/1/1
def ntp2mp(secs, frac):
    usec = (frac * 1000000) >> 32
    # print(secs, frac, "->", secs - NTP_DELTA, (secs - NTP_DELTA) * 1000000, usec)
    return ((secs - NTP_DELTA) * 1000000) + usec


# mp2ntp converts from MP microseconds to NTP seconds and frac
def mp2ntp(usecs):
    (secs, usecs) = divmod(usecs, 1000000)
    return (secs + NTP_DELTA, (usecs << 32) // 1000000)


# ntpclient -
#   Class implementing the uasyncio based NTP client
class SNTP:
    def __init__(self, host="pool.ntp.org", poll=MAX_POLL, max_step=1):
        self._host = host
        self._sock = None
        self._addr = None
        self._send = None
        self._recv = None
        self._close = None
        self._req_poll = poll
        self._min_poll = MIN_POLL
        self._max_step = int(max_step * 1000000)
        self._poll_task = None

    def start(self):
        self._poll_task = asyncio.get_event_loop().create_task(self._poller())

    async def stop(self):
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except:
                pass
            self._close()
            self._poll_task = None

    async def _poll(self):
        # We try to stay with the same server as long as possible. Only
        # lookup the address on startup or after errors.
        if self._sock is None:
            self._addr = socket.getaddrinfo(self._host, 123)[0][-1]
            log.debug("server %s->%s", self._host, self._addr)
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.connect(self._addr)
            stream = asyncio.StreamReader(self._sock)

            async def write_drain(pkt):
                stream.write(pkt)
                await stream.drain()

            self._send = write_drain
            self._recv = lambda length: stream.read(length)
            self._close = lambda: self._sock.close()

        # Send the NTP v3 request to the server
        wbuf = bytearray(48)
        wbuf[0] = 0b00011011
        send_us = time_us()-TZ_US
        send_ntp = mp2ntp(send_us)
        struct.pack_into("!II", wbuf, OFF_TX, send_ntp[0], send_ntp[1])  # set tx timestamp
        await self._send(wbuf)

        # Get server reply
        while True:
            # Raises asyncio.TimeoutError on time-out
            rbuf = await asyncio.wait_for(self._recv(48), timeout=1)
            recv_us = time_us()-TZ_US            # Verify it's truly a response to our request
            orig_ntp = struct.unpack_from("!II", rbuf, OFF_ORIG)  # get originate timestamp
            if orig_ntp == send_ntp:
                break

            # Calculate clock step to apply per RFC4330:
            #     To calculate the roundtrip delay d and system clock offset t relative
            #    to the server, the client sets the Transmit Timestamp field in the
            #    request to the time of day according to the client clock in NTP
            #    timestamp format.  
            #                         1                   2                   3
            #    0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
            #   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            #   |                           Seconds                             |
            #   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            #   |                  Seconds Fraction (0-padded)                  |
            #   +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
            #   
            #   For this purpose, the clock need not be synchronized.  
            #   The server copies this field to the Originate
            #   Timestamp in the reply and sets the Receive Timestamp and Transmit
            #   Timestamp fields to the time of day according to the server clock in
            #   NTP timestamp format.

            #    When the server reply is received, the client determines a
            #    Destination Timestamp variable as the time of arrival according to
            #    its clock in NTP timestamp format.  The following table summarizes
            #    the four timestamps.

            #       Timestamp Name          ID   When Generated
            #       ------------------------------------------------------------
            #       Originate Timestamp     T1   time request sent by client
            #       Receive Timestamp       T2   time request received by server
            #       Transmit Timestamp      T3   time reply sent by server
            #       Destination Timestamp   T4   time reply received by client

            #    The roundtrip delay d and system clock offset t are defined as:

            #       d = (T4 - T1) - (T3 - T2)     t = ((T2 - T1) + (T3 - T4)) / 2.

            #    Note that in general both delay and offset are signed quantities and
            #    can be less than zero; however, a delay less than zero is possible
            #    only in symmetric modes, which SNTP clients are forbidden to use.

        rx_us = ntp2mp(*struct.unpack_from("!II", rbuf, OFF_RX))  # get server recv timestamp
        tx_us = ntp2mp(*struct.unpack_from("!II", rbuf, OFF_TX))  # get server transmit timestamp
        delay = (recv_us - send_us) - (tx_us - rx_us)           #roundtrip delay in us
        step = ((rx_us - send_us) + (tx_us - recv_us)) // 2   #system clock offset

        tup = struct.unpack_from("!IIIIII", rbuf, OFF_ORIG)
        r = mp2ntp(recv_us)
        # log.warning( "Orig TS=[%d]\n Receive TS=[%d]\n Transmit TS=[%d]\n Destination TS=[%d]\n -> rtt=%fms\n system clock offset=%ds\n",
        #    tup[0],  tup[2],  tup[4], r[0], delay / 1000, step/1000000)

        return (delay, step)

    async def _poller(self):
        self._status = 0
        while True:
            # print("\nperforming NTP query")
            try:
                self.status = (self._status << 1) & 0xFFFF
                (delay_us, step_us) = await self._poll()
                if step_us > self._max_step or -step_us > self._max_step: # by default, if more than a second out 
                    log.warning("NTP offset %ds from local time %s", step_us/1000000, time.localtime())
                    (tgt_s, tgt_us) = divmod(time_us() + step_us, 1000000)
                    tm=time.localtime(tgt_s)
                    machine.RTC().init((tm[0], tm[1], tm[2], tm[6] + TZ, tm[3], tm[4], tm[5], tgt_us))

                else:
                    if abs(step_us) < 500000:
                        log.warning("NTP time within 500ms -> no adjustment")
                    else:
                        log.warning("adjusting by %dms (RTT delay=%dus)", step_us/1000, delay_us)
                        #TODO no adj.time atm, just set it
                        (tgt_s, tgt_us) = divmod(time_us() + step_us, 1000000)
                        tm=time.localtime(tgt_s)
                        machine.RTC().init((tm[0], tm[1], tm[2], tm[6] + TZ, tm[3], tm[4], tm[5], tgt_us))

                self.status |= 1
                await asyncio.sleep(61)

            except asyncio.TimeoutError:
                log.warning("%s timed out", self._host)
                if (self._status & 0x7) == 0:
                    # Three failures in a row, force fresh DNS look-up
                    self.sock = None
                    await asyncio.sleep(11)
            except OSError as e:
                # Most likely DNS lookup failure
                log.warning("%s: %s", self._host, e)
                self.sock = None
                await asyncio.sleep(11)
            except Exception as e:
                log.error("%s", e)
                print_exception(e)
                await asyncio.sleep(121)


def start(mqtt, config):
    # from utime import tzset

    config.pop("zone", "UTC+0")

    async def on_init(config):
        ss = SNTP(**config)
        ss.start()
    mqtt.on_init(on_init(config))


# if __name__ == "__main__":
#
#     logging.basicConfig(level=logging.DEBUG)
#
#     async def runner():
#         ss = SNTP(host="192.168.0.1")
#         ss.start()
#         while True:
#             await asyncio.sleep(300)
#
#     asyncio.run(runner())
