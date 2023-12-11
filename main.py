import bleak
import asyncio
import math
import argparse
import RPi.GPIO as gpio
import atexit

class Tester():
    def __init__(self, address, input_pins, output_pins) -> None:
        self.device = bleak.BleakClient(address)
        self.ble_input_future = None
        self.input_pins = input_pins
        self.output_pins = output_pins

    def gpio_setup(self):
        gpio.setmode(gpio.BCM)
        for input_pin in self.input_pins:
            gpio.setup(input_pin, gpio.INPUT)
        for output_pin in self.output_pins:
            gpio.setup(output_pin, gpio.OUTPUT)

        atexit.register(gpio.cleanup)

    async def run(self):
        self.gpio_setup()

        await self.device.connect()
        
        def ble_input_handler(characteristic, data):
            if self.ble_input_future is None:
                return
            self.ble_input_future.set_result(self.decode_inputs(data))

        await self.device.start_notify('00002a56-0000-1000-8000-00805f9b34fb', ble_input_handler)

        print('connected')

        while True:
            await self.ble_send_outputs([1])
            await asyncio.sleep(0.2)
            await self.ble_send_outputs([0])
            await asyncio.sleep(1)

    async def test_outputs(self, *outputs):
        await self.ble_send_outputs(outputs)
        gpio_inputs = self.gpio_get_inputs()
        if gpio_inputs != outputs:
            raise RuntimeError('GPIO outputs not properly set in uC')

    async def test_multiple_inputs(self, inputs):
        if inputs != await self.ble_read_inputs():
            raise RuntimeError('GPIO inputs not properly read in uC')

    async def test_input(self, index, expected_state):
        self.ble_input_future = asyncio.Future()
        self.gpio_set_output(index, expected_state)
        try:
            report = await asyncio.wait_for(self.ble_input_future, 5)
        except asyncio.TimeoutError:
            raise RuntimeError('uC did not detect/send changed input in time')
        finally:
            self.ble_input_future = None

        expected_report = [expected_state] + [0b11] * index
        if report != expected_report:
            raise RuntimeError('uC output not matching expected')

    def gpio_set_output(self, index, output):
        gpio.output(self.output_pins[index], output)

    def gpio_get_inputs(self):
        return tuple(map(gpio.input, self.input_pins))

    def encode_outputs(self, outputs):
        data = [0xff] * math.ceil(len(outputs) / 4)
        for i in range(len(outputs)):
            output = outputs[i]
            byte_index = int(i / 4)
            bit_index = int(i * 2) % 8
            data[byte_index] &= ~((~output & 0b11) << bit_index)
        return bytes(data)
    
    def decode_inputs(self, input_data):
        inputs = []
        for byte in input_data:
            for i in range(0, 8, 2):
                inputs.append((byte >> i) & 0b11)
        while len(inputs) > 0 and inputs[-1] == 0b11:
            inputs.pop(-1)
        return tuple(inputs)
    
    async def ble_send_outputs(self, outputs):
        await self.device.write_gatt_char('00002a57-0000-1000-8000-00805f9b34fb', self.encode_outputs(outputs))

    async def ble_read_inputs(self):
        data = await self.device.read_gatt_char('00002a56-0000-1000-8000-00805f9b34fb')
        return self.decode_inputs(data)

async def main():
    parser = argparse.ArgumentParser('BLEnky testing kit')
    parser.add_argument('--address', '-a', type=str, required=True, help='BLE MAC address / Address UUID to connect to')
    parser.add_argument('--input-pin', '-i', type=int, required=True, help='One or multiple input pins on the PI connected to the uC', action='append')
    parser.add_argument('--output-pin', '-o', type=int, required=True, help='One or multiple output pins on the PI connected to the uC', action='append')
    args = parser.parse_args()

    await Tester(args.address, args.input_pin, args.output_pin).run()

if __name__ == '__main__':
    asyncio.run(main())