// main.cpp - Entry point for GameCubeMacroTool firmware
// Originally from pico-crossing, stripped and extended with BLE
// Garrett Boyd (concept) & Claude/Anthropic (engineering) · opus-4-6 · April 2026
#include "types.hpp"
#include "device.hpp"
#include "display.hpp"
#include "simulatedController.hpp"
#include "ble.hpp"
#include "pico/multicore.h"
#include "joybus.hpp"
#include <stdio.h>

// Global variables
DeviceState device1;
DeviceState device2;
KeyBuffer keyBuffer;

int main() {
	stdio_init_all();
	sleep_ms(1000);
	
	printf("\x1B[2J");
	printf("\x1B[?25l");
	
	// Initialize BLE before launching Core 1 (CYW43 needs to claim PIO)
	ble_init();

	init_device_state(&device1, pio0, GPIO_INPUT_PIN_1);
	init_device_state(&device2, pio0, GPIO_INPUT_PIN_2);

	multicore_launch_core1([]() {
		enterMode(GPIO_OUTPUT_PIN, getControllerState);
	});

	absolute_time_t last_poll_1 = get_absolute_time();
	absolute_time_t last_poll_2 = get_absolute_time();
	absolute_time_t last_render = get_absolute_time();
	
	while (true) {
		absolute_time_t now = get_absolute_time();
		
		// Handle device polling
		if (absolute_time_diff_us(last_poll_1, now) >= 8000) {
			if (!device1.initialized) {
				detect_and_init_device(&device1);
			}
			if (device1.initialized) {
				if (device1.is_keyboard) {
					handle_keyboard_controller(&device1, 1);
				} else {
					handle_standard_controller(&device1, 1);
				}
			}
			last_poll_1 = now;
		}

		if (absolute_time_diff_us(last_poll_2, now) >= 8000) {
			if (!device2.initialized) {
				detect_and_init_device(&device2);
			}
			if (device2.initialized) {
				if (device2.is_keyboard) {
					handle_keyboard_controller(&device2, 2);
				} else {
					handle_standard_controller(&device2, 2);
				}
			}
			last_poll_2 = now;
		}
		
	}
	
	return 0;
}