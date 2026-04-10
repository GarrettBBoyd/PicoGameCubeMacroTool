
cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico2_w)

include(pico_sdk_import.cmake)
project(PicoGameCubeMacroTool C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
pico_sdk_init()

add_executable(PicoGameCubeMacroTool
	src/main.cpp
	src/joybus.cpp
	src/device.cpp
	src/simulatedController.cpp
	src/display.cpp
	src/ble.cpp
	src/script.cpp
)

# Include src/ so btstack_config.h is found
target_include_directories(PicoGameCubeMacroTool PRIVATE
	${CMAKE_CURRENT_LIST_DIR}/src
)

pico_enable_stdio_usb(PicoGameCubeMacroTool 1)
pico_enable_stdio_uart(PicoGameCubeMacroTool 0)

pico_add_extra_outputs(PicoGameCubeMacroTool)

# Generate PIO headers
pico_generate_pio_header(PicoGameCubeMacroTool ${CMAKE_CURRENT_LIST_DIR}/src/controller.pio)
pico_generate_pio_header(PicoGameCubeMacroTool ${CMAKE_CURRENT_LIST_DIR}/src/joybus.pio)

# Generate GATT header from .gatt file
pico_btstack_make_gatt_header(PicoGameCubeMacroTool PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src/shiny_tool.gatt)

target_link_libraries(PicoGameCubeMacroTool
	pico_stdlib
	hardware_pio
	pico_multicore
	pico_platform
	pico_cyw43_arch_none
	pico_btstack_ble
	pico_btstack_cyw43
)
