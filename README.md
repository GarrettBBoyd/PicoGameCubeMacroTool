# PicoGameCubeMacroTool

A Raspberry Pi Pico 2 W–based GameCube controller passthrough with Bluetooth macro scripting. Plug it inline between a GameCube controller and console — your physical controller works as normal, and you can upload timed button-input scripts over BLE from a phone or PC to play back automatically (alongside your controller, not instead of it).

Originally built for Pokémon shiny hunting automation, but works for any GameCube game that benefits from repeatable button sequences.

---

## Features

- **Transparent passthrough** — controller input forwarded to console at full speed via dual-core PIO; zero added latency during normal play
- **BLE scripting** — connect from a phone (Android app) or PC (Python desktop app) via Bluetooth LE
- **Script overlay** — during playback, script button presses are OR'd on top of live controller input; you keep full control
- **WaveBird support** — detects standard wired and WaveBird wireless controllers
- **Script storage** — up to 256 steps per script, each with delay, buttons, and analog stick position
- **Named scripts** — save/load scripts as JSON on phone or PC

---

## Hardware

### Bill of Materials

| Part | Description | Source |
|------|-------------|--------|
| Raspberry Pi Pico 2 W | RP2350 + CYW43439 BLE/WiFi | [DigiKey SC1633](https://www.digikey.com/en/products/detail/raspberry-pi/SC1633/25862726) |
| 1kΩ resistor (×2) | Signal level / pull | [DigiKey CF14JT1K00](https://www.digikey.com/en/products/detail/stackpole-electronics-inc/CF14JT1K00/1741314) |
| 1N5819 Schottky diode | Voltage protection | [DigiKey 1N5819](https://www.digikey.com/en/products/detail/taiwan-semiconductor-corporation/1N5819/7357079) |
| GC controller socket | Console-side connector | [AliExpress](https://www.aliexpress.us/item/3256809038245775.html) |
| GC controller plug | Controller-side connector | [AliExpress](https://www.aliexpress.us/item/3256809767251472.html) |
| Custom PCB | See `Gerbers.zip` | — |

### PCB

Gerber files are included in `Gerbers.zip`. Order from any PCB fab (JLCPCB, PCBWay, OSHPark, etc.).

![PCB Front](docs/PCB%20FRONT.png)
![PCB Back](docs/PCB%20BACK.png)
![KiCad View](docs/PCB%20KICAD.PNG)

### Enclosure

A work-in-progress shell design is included as `GCMacroTool Shell WIP.stl`. Print in PLA or PETG.

---

## Project Structure

```
PicoGameCubeMacroTool/
├── firmware/               # C++ firmware for Pico 2 W
│   ├── src/                # Source files
│   │   ├── main.cpp        # Entry point, core init
│   │   ├── ble.cpp/hpp     # BLE (Nordic UART Service)
│   │   ├── script.cpp/hpp  # Script engine & playback
│   │   ├── device.cpp/hpp  # Controller detection (wired + WaveBird)
│   │   ├── simulatedController.cpp/hpp  # Passthrough + overlay
│   │   ├── joybus.cpp/hpp  # Joybus protocol (PIO)
│   │   ├── controller.pio  # PIO for console-side Joybus
│   │   ├── joybus.pio      # PIO for controller-side polling
│   │   ├── types.hpp       # Shared types and device ID constants
│   │   ├── gcReport.hpp    # GC controller report struct
│   │   ├── shiny_tool.gatt # BLE GATT profile (Nordic UART)
│   │   └── btstack_config.h
│   ├── CMakeLists.txt
│   ├── pico_sdk_import.cmake
│   └── licenses/           # Third-party license notices
├── app/
│   └── shiny_tool.py       # Python desktop app (tkinter + bleak BLE)
├── tools/
│   ├── bin2uf2.py          # Convert .bin → .uf2 for flashing
│   └── ble_test.py         # Standalone BLE protocol test script
├── builds/
│   └── PicoGameCubeMacroTool.uf2   # Pre-built firmware (flash directly)
├── docs/                   # PCB images + Pico 2 W datasheet
├── Gerbers.zip             # PCB manufacturing files
├── GCMacroTool Shell WIP.stl  # 3D printable enclosure (WIP)
└── LICENSE                 # GPL-3.0
```

---

## Building the Firmware

### Prerequisites

- [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk) — clone alongside this repo
- ARM GCC toolchain 13+ (`arm-none-eabi-gcc`)
- CMake 3.13+
- Python 3 (for `bin2uf2.py`)

### Build

```bash
cd firmware
mkdir build && cd build
cmake -DPICO_SDK_PATH=../../pico-sdk ..
cmake --build . --target PicoGameCubeMacroTool
```

> **Windows note:** The GCC 12 linker crashes on this project. Use ARM GCC 13+ for linking, or use the pre-built UF2 in `builds/`.

### Flash

Hold BOOTSEL on the Pico while plugging in USB — it mounts as a drive. Copy `PicoGameCubeMacroTool.uf2` onto it (or the pre-built one from `builds/`).

---

## Desktop App (Python)

`app/shiny_tool.py` — tkinter UI with BLE support.

**Requirements:**
```
pip install bleak
```

**Features:**
- Scan and connect to the Pico over BLE
- Create, edit, save, and load scripts (JSON)
- Upload scripts to the Pico and control playback
- Scripts auto-saved with `.json` extension

---

## BLE Protocol

The Pico advertises as **"Shiny Hunting Assistant Tool"** using the Nordic UART Service (NUS).

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
| Button | Flag |
|--------|------|
| A | `0x0001` |
| B | `0x0002` |
| X | `0x0004` |
| Y | `0x0008` |
| Start | `0x0010` |
| D-Left | `0x0020` |
| D-Right | `0x0040` |
| D-Down | `0x0080` |
| D-Up | `0x0100` |
| Z | `0x0200` |
| R | `0x0400` |
| L | `0x0800` |

---

## Credits

Firmware architecture based on [pico-crossing](https://github.com/arntsonl/pico-crossing) by arntsonl (GPL-3.0). Joybus PIO adapted from retro-pico-switch (MIT).

Concept: Garrett Boyd  
Engineering: Claude / Anthropic (opus-4-6, April 2026)

---

## License

GPL-3.0 — see [LICENSE](LICENSE). Third-party license notices in `firmware/licenses/`.
