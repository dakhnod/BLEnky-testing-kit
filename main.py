import bleak
import asyncio
import math

class Tester():
    def __init__(self) -> None:
        self.device = bleak.BleakClient('1CF348D8-55AD-3E39-4F8F-679B29C7175E')
        self.input_future = None

    async def run(self):
        await self.device.connect()
        
        def ble_input_handler(characteristic, data):
            if self.input_future is None:
                return
            self.input_future.set_result(self.decode_inputs(data))

        await self.device.start_notify('00002a56-0000-1000-8000-00805f9b34fb', ble_input_handler)

        print('waiting')

        # await self.test_output(0, 1)
        await self.test_outputs(0, )
        print('test succeeded')

        await asyncio.sleep(9999)

        while True:
            await self.ble_send_outputs(1)
            await asyncio.sleep(0.2)
            await self.ble_send_outputs(0)
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
        self.input_future = asyncio.Future()
        outputs = [expected_state] + [0b11] * index
        self.gpio_set_outputs(index, expected_state)
        try:
            async with asyncio.timeout(5):
                inputs = await self.input_future
        except asyncio.TimeoutError:
            raise RuntimeError('uC did not detect/send changed input in time')
        
        self.input_future = None
        if inputs[index] != expected_state:
            raise RuntimeError('uC output not matching expected')

    def gpio_set_output(self, index, output):
        pass

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

    def gpio_get_inputs(self):
        return (1, )

async def main():
    await Tester().run()

if __name__ == '__main__':
    asyncio.run(main())