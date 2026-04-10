"""
BLE test script for Shiny Hunting Assistant Tool
Tests: connect, upload script, start/stop playback, query status

Run with: python ble_test.py

Garrett Boyd (concept) & Claude/Anthropic (engineering) · opus-4-6 · April 2026
"""
import asyncio
import struct
from bleak import BleakScanner, BleakClient

DEVICE_NAME = "Shiny Hunting Assistant Tool"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# Protocol constants
CMD_UPLOAD  = 0x01
CMD_START   = 0x02
CMD_STOP    = 0x03
CMD_STATUS  = 0x04
RSP_ACK     = 0x80
RSP_STATUS  = 0x81

# Button bitmask helpers (low byte = buttons0, high byte = buttons1)
BTN_A     = 0x0001
BTN_B     = 0x0002
BTN_X     = 0x0004
BTN_Y     = 0x0008
BTN_START = 0x0010
BTN_Z     = 0x1000
BTN_L     = 0x4000
BTN_R     = 0x2000
BTN_DUP   = 0x0800
BTN_DDOWN = 0x0400
BTN_DLEFT = 0x0100
BTN_DRIGHT= 0x0200

responses = []

def on_notify(sender, data):
    responses.append(data)
    if data[0] == RSP_ACK:
        status = "OK" if data[1] == 0 else "ERROR"
        print(f"  <- ACK: {status}")
    elif data[0] == RSP_STATUS:
        states = {0: "IDLE", 1: "RUNNING", 2: "WAITING"}
        state = states.get(data[1], f"UNKNOWN({data[1]})")
        step = data[2] | (data[3] << 8)
        total = data[4] | (data[5] << 8)
        print(f"  <- STATUS: {state}, step {step}/{total}")
    else:
        print(f"  <- Unknown: {data.hex()}")

def make_step(delay_ms, buttons=0, stick_x=128, stick_y=128):
    """Create an 8-byte script step."""
    return struct.pack('<IHBB', delay_ms, buttons, stick_x, stick_y)

def make_script(steps):
    """Build upload payload: [cmd] [num_steps_le16] [step_data...]"""
    num = len(steps)
    payload = struct.pack('<BH', CMD_UPLOAD, num)
    for delay_ms, buttons, sx, sy in steps:
        payload += struct.pack('<IHBB', delay_ms, buttons, sx, sy)
    return payload

async def ble_write(client, data):
    """Write data to NUS RX characteristic (write without response)."""
    await client.write_gatt_char(NUS_RX_UUID, data, response=False)

async def ble_write_chunked(client, data, chunk_size=20):
    """Write data in chunks that fit within BLE MTU."""
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i+chunk_size]
        await ble_write(client, chunk)
        if i + chunk_size < len(data):
            await asyncio.sleep(0.05)  # Small delay between chunks

async def main():
    print("Scanning for BLE devices (5 seconds)...")
    devices = await BleakScanner.discover(timeout=5.0)

    target = None
    for d in devices:
        name = d.name or "Unknown"
        if DEVICE_NAME in (d.name or ""):
            target = d
            print(f"  Found target: {name} [{d.address}]")

    if not target:
        print(f"'{DEVICE_NAME}' not found.")
        return

    print("Connecting...")
    try:
        async with BleakClient(target.address, timeout=10.0) as client:
            mtu = client.mtu_size
            print(f"Connected! (MTU={mtu})")

            # Subscribe to notifications on TX characteristic
            print("Subscribing to notifications...")
            await client.start_notify(NUS_TX_UUID, on_notify)
            # Wait for Pico to process the CCCD write (notifications_enabled flag)
            await asyncio.sleep(1.0)
            print("Ready!\n")

            # --- Test 1: Query status (should be IDLE) ---
            print("=== Test 1: Query Status ===")
            responses.clear()
            await ble_write(client, bytes([CMD_STATUS]))
            await asyncio.sleep(1.0)
            if not responses:
                print("  (no response received)")

            # --- Test 2: Upload a test script ---
            # Script: wait 1s press Start, wait 1s press A, wait 1s press A, wait 0.5s press B
            print("\n=== Test 2: Upload Script ===")
            steps = [
                (1000, BTN_START, 128, 128),
                (1000, BTN_A,     128, 128),
                (1000, BTN_A,     128, 128),
                (500,  BTN_B,     128, 128),
            ]
            script_data = make_script(steps)
            eff_mtu = mtu - 3  # ATT header overhead
            print(f"  Sending {len(script_data)} bytes ({len(steps)} steps), effective MTU={eff_mtu}")
            responses.clear()
            if len(script_data) <= eff_mtu:
                await ble_write(client, script_data)
            else:
                print(f"  Chunking into {eff_mtu}-byte packets...")
                await ble_write_chunked(client, script_data, chunk_size=eff_mtu)
            await asyncio.sleep(1.0)
            if not responses:
                print("  (no response received)")

            # --- Test 3: Query status (should show 4 steps loaded) ---
            print("\n=== Test 3: Query Status After Upload ===")
            responses.clear()
            await ble_write(client, bytes([CMD_STATUS]))
            await asyncio.sleep(1.0)
            if not responses:
                print("  (no response received)")

            # --- Test 4: Start playback ---
            print("\n=== Test 4: Start Playback ===")
            responses.clear()
            await ble_write(client, bytes([CMD_START]))
            await asyncio.sleep(1.0)
            if not responses:
                print("  (no response received)")

            # --- Test 5: Query status during playback ---
            print("\n=== Test 5: Status During Playback ===")
            responses.clear()
            await ble_write(client, bytes([CMD_STATUS]))
            await asyncio.sleep(1.0)
            await ble_write(client, bytes([CMD_STATUS]))
            await asyncio.sleep(1.0)

            # --- Test 6: Wait for completion ---
            print("\n=== Test 6: Wait for Completion ===")
            await asyncio.sleep(3.0)
            responses.clear()
            await ble_write(client, bytes([CMD_STATUS]))
            await asyncio.sleep(1.0)
            if not responses:
                print("  (no response received)")

            print("\n=== All Tests Complete ===")
            print("Disconnecting...")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
