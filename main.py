import bleak
import asyncio
import math
import argparse
import RPi.GPIO as gpio
import atexit
import abc
import inspect
import logging
import time
import sys
from gpioasm import gpioasm

logging.basicConfig(level=logging.INFO)
# logging.StreamHandler.terminator = ''


def print_color(color='reset'):
    if color is None:
        print("\x1b[0m")
        return
    print({
        'grey': "\x1b[38;20m",
        'yellow': "\x1b[33;20m",
        'red': "\x1b[31;20m",
        'green': "\x1b[32;20m",
        'bold_red': "\x1b[31;1m",
        'reset': "\x1b[0m",
    }[color], end='')

def print_color_str(str, color='green'):
    print_color(color)
    print(str)
    print_color('reset')

class IOLayer(abc.ABC):
    def __init__(self, name):
        self.name = name
    def init(self):
        pass
    def uninit(self):
        pass
    def set_outputs(self, outputs):
        pass
    def get_inputs(self):
        pass
    def set_output(self, index, output):
        pass
    def get_input(self, index):
        pass
    def before_get_input(self, index):
        pass

class RPIGPIOLayer(IOLayer):
    def __init__(self, input_pins, output_pins):
        super().__init__('GPIO')
        self.input_pins = input_pins
        self.output_pins = output_pins

    async def init(self):
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
        return tuple(map(gpio.input, self.input_pins))
    
    def set_output(self, index, output):
        gpio.output(self.output_pins[index], output)

    def get_input(self, index):
        return gpio.input(self.input_pins[index])
    
class BlenkyLayer(IOLayer):
    def __init__(self, address):
        super().__init__('BLE')
        self.device = bleak.BleakClient(address, timeout=30)
        self.ble_input_future = None
        self.logger = logging.getLogger('BLE')
        self.logger.addHandler(logging.StreamHandler())
        self.logger.handlers[0].terminator = '\n'

    async def init(self):
        await self.connect()

        def ble_input_handler(characteristic, data):
            inputs = self._decode_inputs(data)
            if self.ble_input_future is None:
                return
            if self.ble_input_future.done():
                return
            self.ble_input_future.set_result(inputs)

        await self.device.start_notify('00002a56-0000-1000-8000-00805f9b34fb', ble_input_handler)


    async def uninit(self):
        await self.device.disconnect()

    async def connect(self):
        self.logger.info('connecting...')
        await self.device.connect()
        print_color_str('OK')

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

    async def before_get_input(self, index):
        self.ble_input_future = asyncio.Future()

    async def get_inputs(self):
        data = await self.device.read_gatt_char('00002a56-0000-1000-8000-00805f9b34fb')

        read = self._decode_inputs(data)
        return read
    
    async def set_output(self, index, output):
        outputs = [0b11] * index + [output]
        await self.device.write_gatt_char('00002a57-0000-1000-8000-00805f9b34fb', self._encode_outputs(outputs))

    async def get_input(self, index):
        try:
            reported = await asyncio.wait_for(self.ble_input_future, 5)
            try:
                return reported[index]
            except IndexError:
                return None
        except asyncio.exceptions.TimeoutError:
            self.logger.error('Error waiting for input notification')
            return None

class Tester():
    async def call_func(self, func, *args, **kwargs):
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return func(*args, **kwargs)
    
    def __init__(self, *layers) -> None:
        if(len(layers) != 2):
            raise NotImplementedError('Only 2 layers supported')
        self.layers = layers
        self.logger = logging.getLogger('Tests')

    async def init(self):
        for layer in self.layers:
            await self.call_func(layer.init)

    class TestFailedError(RuntimeError):
        pass

    async def run(self):
        signals_list = (
            (0,0,0,0),
            (1,0,0,0),
            (0,1,0,0),
            (0,0,1,0),
            (0,0,0,1),
            (1,1,0,0),
            (0,0,1,1),
            (1,0,1,0),
            (0,1,0,1),
            (1,1,1,1),
            (0,0,0,0),
        )
        test_count = [0,0]
        try:
            for layers in (self.layers, self.layers[::-1]):
                for signals in signals_list:
                    try:
                        signals_formated = tuple('●' if s else '○' for s in signals)
                        self.logger.info(f'testing signals {signals_formated}, {layers[0].name} -> {layers[1].name}...')
                        await self.test_signals(layers, signals)
                        print_color_str('OK')
                        test_count[0] += 1
                    except self.TestFailedError as e:
                        print()
                        print_color('red')
                        self.logger.error(str(e))
                        print_color()
                        print()
                        test_count[1] += 1
            # for layer in self.layers:
            #     await self.call_func(layer.set_outputs, (0, 0, 0, 0))

            for layers in (self.layers, self.layers[::-1]):
                for signal in (1, 0):
                    for index in range(4):
                        try:
                            self.logger.info(f'testing signal index {index}: {signal}, {layers[0].name} -> {layers[1].name}...')
                            await self.test_signal(layers, index, signal)
                            print_color_str('OK')
                            test_count[0] += 1
                        except self.TestFailedError as e:
                            print()
                            print_color('red')
                            self.logger.error(str(e))
                            print_color()
                            print()
                            test_count[1] += 1
        finally:
            pass
            # for layer in self.layers:
            #     await self.call_func(layer.uninit)
        if test_count[1] == 0:
            print_color('green')
        else:
            print_color('red')
        self.logger.info(f'Tests successful: %d, failed: %d', *test_count)
        if test_count[1] > 0:
            sys.exit(1)
        print_color()
        print()

    async def test_signals(self, layers, signals: tuple[int]):
        await self.call_func(layers[0].set_outputs, signals)
        reported = await self.call_func(layers[1].get_inputs)
        if signals != reported:
            raise self.TestFailedError(f'signal {layers[0].name}{signals} did not match {layers[1].name}{reported}')

    async def test_signal(self, layers, index, signal):
        await self.call_func(layers[1].before_get_input, index)
        await self.call_func(layers[0].set_output, index, signal)
        reported = await self.call_func(layers[1].get_input, index)
        if signal != reported:
            raise self.TestFailedError(f'signal {layers[0].name}: {signal} did not match {layers[1].name}: {reported}')

async def main():
    parser = argparse.ArgumentParser('BLEnky testing kit')
    parser.add_argument('--address', '-a', type=str, required=True, help='BLE MAC address / Address UUID to connect to')
    parser.add_argument('--input-pin', '-i', type=int, required=True, help='One or multiple input pins on the PI connected to the uC', nargs='+')
    parser.add_argument('--output-pin', '-o', type=int, required=True, help='One or multiple output pins on the PI connected to the uC', nargs='+')
    args = parser.parse_args()

    atexit.register(gpio.cleanup)

    gpioLayer = RPIGPIOLayer(args.input_pin, args.output_pin)
    bleLayer = BlenkyLayer(args.address)

    tester = Tester(gpioLayer, bleLayer)
    await tester.init()

    # stop gpioASM engine by sending empty code
    await bleLayer.device.write_gatt_char('b1190001-2a74-d5a2-784f-c1cdb3862ab0', (0x00, 0x80, 0x00))

    await tester.run()

    async def test_inputs_delayed(target_states, timeout_min_ms, timeout_max_ms):
        timeout_min = timeout_min_ms / 1000
        timeout_max = timeout_max_ms / 1000
        time_start = time.time()
        time_delta = 0

        while gpioLayer.get_inputs() != target_states:
            time_delta = time.time() - time_start
            if time_delta > timeout_max:
                raise TimeoutError(f'runtime slept for too long ({int(time_delta * 1000)} > {timeout_max_ms}) inputs: {gpioLayer.get_inputs()}')
            time.sleep(0.0005)
        if time_delta < timeout_min:
            raise TimeoutError(f'runtime slept for too short ({int(time_delta * 1000)} < {timeout_min_ms}) inputs: {gpioLayer.get_inputs()}')
        return int(time_delta * 1000)
    
    await bleLayer.set_outputs((0, 0, 0, 0))
    gpioLayer.set_outputs((0, 0, 0, 0))

    logger = logging.getLogger('gpioASM')

    async def upload_gpioasm_file(filename):
        logger.info('compiling gpioASM code (https://github.com/dakhnod/BLEnky-testing-kit/blob/main/test.gpioasm)...')
        payload = gpioasm.Compiler().file_compile(filename)
        print_color_str('OK')
        
        logger.info('uploading gpioASM code...')

        index = 0
        while len(payload) > 0:
            status_byte = 0x00
            if(len(payload) > 19):
                status_byte = 0b10000000
            await bleLayer.device.write_gatt_char('b1190001-2a74-d5a2-784f-c1cdb3862ab0', [(status_byte | index)] + payload[:19])
            index += 1
            payload = payload[19:]
        print_color_str('OK')

    await upload_gpioasm_file('test.gpioasm')

    logger.info('synchronizing...')
    time_taken = await test_inputs_delayed((1, 1, 1, 1), 0, 1000)
    print_color_str('OK')

    logger.info('running tests...')
    print()

    # the following steps are refrelected in the file test.gpioasm

    targets = [
        ((1, 0, 0, 0), 100),
        ((0, 1, 0, 0), 100),
        ((0, 0, 1, 0), 100),
        ((0, 0, 0, 1), 100)
    ]

    for timeout in (100, 200):
        for _ in range(10):
            targets.append(((1, 0, 1, 0), timeout))
            targets.append(((0, 1, 0, 1), timeout))

    for (states, timeout) in targets:
        signals_formated = tuple('●' if s else '○' for s in states)
        logger.info(f'awaiting {signals_formated}...')
        time_taken = await test_inputs_delayed(states, timeout - 10, timeout + 3)
        print_color_str(f'OK, took {time_taken}ms')

    for states in ((1, 1, 1, 1), (0, 0, 0, 0)):
        logger.info('waiting for outputs to stay constant...')
        try:
            await test_inputs_delayed(states, 0, 5000)
            raise RuntimeError('gpioASM engine did not wait at sleep_match_all command...')
        except TimeoutError:
            print_color_str('OK')
        logger.info('triggering sleep_match_all command by setting outputs...')
        gpioLayer.set_outputs(states)
        time_taken = await test_inputs_delayed(states, 0, 5)
        print_color_str(f'OK, took {time_taken}ms to react')
    print_color('green')
    logger.info('gpioASM script fully run and traced.')
    print_color()
    await upload_gpioasm_file('led_demo.gpioasm')
    print()

if __name__ == '__main__':
    asyncio.run(main())
