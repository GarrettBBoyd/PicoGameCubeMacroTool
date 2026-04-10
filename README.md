# PicoGameCubeMacroTool

A Raspberry Pi Pico 2 W–based GameCube controller passthrough with Bluetooth macro scripting. Plug it inline between a GameCube controller and console — your physical controller works as normal, and you can upload timed button-input scripts over BLE from a PC to play back automatically (alongside your controller, not instead of it).

Originally built for Pokémon shiny hunting automation, but works for any GameCube game that benefits from repeatable button sequences.

---

## Features

- **Transparent passthrough** — controller input forwarded to console at full speed via dual-core PIO; zero added latency during normal play
- **BLE scripting** — connect from a PC (Python desktop app) via Bluetooth LE
- **Script overlay** — during playback, script button presses are OR'd on top of live controller input; you keep full control
- **WaveBird support** — detects standard wired and WaveBird wireless controllers
- **Script storage** — up to 256 steps per script, each with delay, buttons, and analog stick position
- **Named scripts** — save/load scripts as JSON on PC

---

## Hardware

### Bill of Materials

| Part | Description | Source |
|------|-------------|--------|
| Raspberry Pi Pico 2 W | RP2350 + CYW43439 BLE/WiFi | [DigiKey SC1633](https://www.digikey.com/en/products/detail/raspberry-pi/SC1633/25862726) |
| 1kΩ resistor | Signal line | [DigiKey CF14JT1K00](https://www.digikey.com/en/products/detail/stackpole-electronics-inc/CF14JT1K00/1741314) |
| 1N5819 Schottky diode | Voltage protection | [DigiKey 1N5819](https://www.digikey.com/en/products/detail/taiwan-semiconductor-corporation/1N5819/7357079) |
| GC controller socket | Console-side connector | [AliExpress](https://www.aliexpress.us/item/3256809038245775.html) |
| GC controller plug | Controller-side connector | [AliExpress](https://www.aliexpress.us/item/3256809767251472.html) |
| Custom PCB | See `Gerbers.zip` | — |

### PCB

Gerber files are included in `Gerbers.zip`. Order from any PCB fab (JLCPCB, PCBWay, OSHPark, etc.).

### Enclosure

A work-in-progress shell design is included as `GCMacroTool Shell WIP.stl`. Print in PLA or PETG.

---

## Project Structure

```
PicoGameCubeMacroTool/
├── firmware/               # C++ firmware for Pico 2 W
│   ├── src/
│   │   ├── main.cpp
│   │   ├── ble.cpp/hpp     # BLE (Nordic UART Service)
│   │   ├── script.cpp/hpp  # Script engine & playback
│   │   ├── device.cpp/hpp  # Controller detection (wired + WaveBird)
│   │   ├── simulatedController.cpp/hpp  # Passthrough + overlay
│   │   ├── joybus.cpp/hpp  # Joybus protocol (PIO)
│   │   ├── controller.pio
│   │   ├── joybus.pio
│   │   ├── types.hpp
│   │   ├── gcReport.hpp
│   │   ├── shiny_tool.gatt
│   │   └── btstack_config.h
│   ├── CMakeLists.txt
│   ├── pico_sdk_import.cmake
│   └── licenses/
├── app/
│   └── Macro_Tool.py       # Python desktop app (tkinter + bleak BLE)
├── tools/
│   ├── bin2uf2.py          # Convert .bin → .uf2
│   └── ble_test.py         # Standalone BLE protocol test
├── Gerbers.zip
├── GCMacroTool Shell WIP.stl
└── LICENSE
```

---

## Flashing

Download the latest `PicoGameCubeMacroTool.uf2` from the [Releases](../../releases) page.

Hold **BOOTSEL** on the Pico while plugging in USB — it mounts as a drive. Copy the `.uf2` onto it.

---

## Building the Firmware

### Prerequisites

- [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk) — clone alongside this repo
- ARM GCC toolchain 13+ (`arm-none-eabi-gcc`)
- CMake 3.13+
- Python 3

### Build

```bash
cd firmware
mkdir build && cd build
cmake -DPICO_SDK_PATH=../../pico-sdk ..
cmake --build . --target PicoGameCubeMacroTool
```

> **Windows note:** The GCC 12 linker crashes on this project. Use ARM GCC 13+ for linking, or use the pre-built UF2 from Releases.

---

## Desktop App (Python)

`app/Macro_Tool.py` — tkinter UI with BLE support.

**Requirements:**
```
pip install bleak
```

**Features:**
- Scan and connect to the Pico over BLE
- Create, edit, save, and load scripts (JSON)
- Upload scripts to the Pico and control playback

---

## BLE Protocol

The Pico advertises as **"GameCubeMacroTool"** using the Nordic UART Service (NUS).

| Command | Byte | Description |
|---------|------|-------------|
| Upload Script | `0x01` + step count (2B) + steps | Upload a script (8 bytes/step) |
| Start Playback | `0x02` | Begin script execution |
| Stop Playback | `0x03` | Halt script |
| Query Status | `0x04` | Ask current state |

| Response | Byte | Description |
|----------|------|-------------|
| ACK | `0x80` | Command accepted |
| Status | `0x81` + state byte | `0`=Idle, `1`=Playing, `2`=Waiting |

**Script step format (8 bytes per step):**
```
[delay_ms: 4 bytes BE] [buttons: 2 bytes BE] [stick_x: 1 byte] [stick_y: 1 byte]
```

**Button flags:**

| Button | Flag | Button | Flag |
|--------|------|--------|------|
| A | `0x0001` | D-Up | `0x0100` |
| B | `0x0002` | D-Down | `0x0080` |
| X | `0x0004` | D-Left | `0x0020` |
| Y | `0x0008` | D-Right | `0x0040` |
| Start | `0x0010` | Z | `0x0200` |
| L | `0x0800` | R | `0x0400` |

---

## Credits & Special Thanks

Special thanks to **[hunterirving](https://github.com/hunterirving)** whose project **[pico-crossing](https://github.com/hunterirving/pico-crossing)** was the direct inspiration and foundation for this tool. The Joybus passthrough architecture, PIO implementation, and controller handling all stem from that work.

Additional thanks to the retro-pico-switch project for the Joybus PIO approach (MIT licensed).

Concept: Garrett Boyd  
Engineering: Claude / Anthropic (opus-4-6, April 2026)

---

## License

GPL-3.0 — see [LICENSE](LICENSE). Third-party license notices in `firmware/licenses/`.
