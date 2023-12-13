import bleak
import asyncio
import math
import argparse
import RPi.GPIO as gpio
import atexit
import abc
import inspect

class IOLayer(abc.ABC):
    def __init__(self, name):
        self.name = name
    def uninit(self):
        pass
    def set_outputs(self, outputs):
        pass
    def get_inputs(self):
        pass

class RPIGPIOLayer(IOLayer):
    def __init__(self, input_pins, output_pins):
        super().__init__('GPIO')
        self.input_pins = input_pins
        self.output_pins = output_pins
        gpio.setmode(gpio.BCM)
        for input_pin in self.input_pins:
            gpio.setup(input_pin, gpio.IN)
        for output_pin in self.output_pins:
            gpio.setup(output_pin, gpio.OUT)

    def uninit(self):
        return gpio.cleanup()

    def set_outputs(self, outputs):
        gpio.output(self.output_pins[:len(outputs)], list(map(bool, outputs))) 

    def get_inputs(self):
        return gpio.input(self.input_pins)
    
class BlenkyLayer(IOLayer):
    async def __init__(self, address):
        super().__init__("BLE")
        self.device = bleak.BleakClient(address)
        self.ble_input_future = None
        await self.connect()

        def ble_input_handler(characteristic, data):
            if self.ble_input_future is None:
                return
            inputs = self.decode_inputs(data)
            print(f'new inputs: {inputs}')
            self.ble_input_future.set_result(inputs)

        await self.device.start_notify('00002a56-0000-1000-8000-00805f9b34fb', ble_input_handler)


    async def uninit(self):
        await self.device.disconnect()

    async def connect(self):
        await self.device.connect()

    def _encode_outputs(self, outputs):
        data = [0xff] * math.ceil(len(outputs) / 4)
        for i in range(len(outputs)):
            output = outputs[i]
            byte_index = int(i / 4)
            bit_index = int(i * 2) % 8
            data[byte_index] &= ~((~output & 0b11) << bit_index)
        return bytes(data)
    
    def _decode_inputs(self, input_data):
        inputs = []
        for byte in input_data:
            for i in range(0, 8, 2):
                inputs.append((byte >> i) & 0b11)
        while len(inputs) > 0 and inputs[-1] == 0b11:
            inputs.pop(-1)
        return tuple(inputs)
    
    async def set_outputs(self, outputs):
        await self.device.write_gatt_char('00002a57-0000-1000-8000-00805f9b34fb', self._encode_outputs(outputs))

    async def get_inputs(self):
        data = await self.device.read_gatt_char('00002a56-0000-1000-8000-00805f9b34fb')
        return self._decode_inputs(data)

class Tester():
    async def call_func(func, *args, **kwargs):
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return func(*args, **kwargs)
    
    async def __init__(self, *layers) -> None:
        if(len(layers) != 2):
            raise NotImplementedError('Only 2 layers supported')
        self.layers = layers
        for layer in layers:
            await self.call_func(layer.init)

    async def run(self):

        print('connected')

        await self.test_multiple_inputs((0,))
        await self.test_multiple_inputs((1,))
        await self.test_multiple_inputs((0,))
        await self.test_multiple_inputs((1,))

        await self.test_input(0, 0)
        await self.test_input(0, 1)
        await self.test_input(0, 0)
        await self.test_input(0, 1)

        await self.test_outputs((0,))
        await self.test_outputs((1,))
        await self.test_outputs((0,))
        await self.test_outputs((1,))

    async def test_outputs(self, outputs):
        outputs = tuple(outputs)
        await self.ble_send_outputs(outputs)
        gpio_inputs = self.gpio_get_inputs()
        print(f'expected outputs {outputs}, reported: {gpio_inputs}')
        if gpio_inputs != outputs:
            raise RuntimeError('GPIO outputs not properly set in uC')

    async def test_multiple_inputs(self, inputs):
        inputs = tuple(inputs)
        self.gpio_set_outputs(inputs)
        # await asyncio.sleep(1)
        reported = await self.ble_read_inputs()
        print(f'expected: {inputs}, reported: {reported}')
        if inputs != reported:
            raise RuntimeError('GPIO inputs not properly read in uC')

    async def test_input(self, index, expected_state):
        print(f'testing input #{index}: {expected_state}')
        self.ble_input_future = asyncio.Future()
        self.gpio_set_output(index, expected_state)
        try:
            report = await asyncio.wait_for(self.ble_input_future, 5)
        except asyncio.TimeoutError:
            raise RuntimeError('uC did not detect/send changed input in time')
        finally:
            self.ble_input_future = None

        expected_report = tuple([expected_state] + [0b11] * index)
        if report != expected_report:
            print(f'expected: {expected_report}, actual: {report}')
            raise RuntimeError('uC output not matching expected')
    
    def decode_inputs(self, input_data):
        inputs = []
        for byte in input_data:
            for i in range(0, 8, 2):
                inputs.append((byte >> i) & 0b11)
        while len(inputs) > 0 and inputs[-1] == 0b11:
            inputs.pop(-1)
        return tuple(inputs)

async def main():
    parser = argparse.ArgumentParser('BLEnky testing kit')
    parser.add_argument('--address', '-a', type=str, required=True, help='BLE MAC address / Address UUID to connect to')
    parser.add_argument('--input-pin', '-i', type=int, required=True, help='One or multiple input pins on the PI connected to the uC', action='append')
    parser.add_argument('--output-pin', '-o', type=int, required=True, help='One or multiple output pins on the PI connected to the uC', action='append')
    args = parser.parse_args()

    gpioLayer = await RPIGPIOLayer(args.input_pin, args.output_pin)
    bleLayer = await BlenkyLayer(args.address)

    await Tester(args.address, args.input_pin, args.output_pin).run()

if __name__ == '__main__':
    asyncio.run(main())