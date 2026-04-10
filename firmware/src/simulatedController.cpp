// simulatedController.cpp - Controller state aggregation with script overlay
// Originally from pico-crossing, stripped and reworked for GameCubeMacroTool
// Garrett Boyd (concept) & Claude/Anthropic (engineering) · opus-4-6 · April 2026
#include "simulatedController.hpp"
#include "script.hpp"
#include "types.hpp"
#include <cstdio>
#include <cstring>

// External declarations for key tracking functions
extern bool tracker_is_active(KeyTracker* tracker, uint8_t keycode);

extern DeviceState device1;
extern DeviceState device2;
extern KeyBuffer keyBuffer;

SimulatedState simulatedState = {
	.xStick = 128,
	.yStick = 128,
	.hold_duration_us = 17000,
	.keyboard_calibrated = false,
};

void handlePassthrough(GCReport& report, uint8_t buttons1, uint8_t buttons2, uint8_t dpadState,
					  uint8_t analogX, uint8_t analogY, uint8_t cX, uint8_t cY) {
	static uint8_t arrowKeyDpad = 0; // Tracks D-Pad state from arrow keys

	// Handle arrow key inputs from attached keyboard
	arrowKeyDpad = 0;

	// Check for arrow key activations in devices
	if (device1.initialized && device1.is_keyboard) {
		if (tracker_is_active(&device1.key_tracker, 0x5C) || tracker_is_active(&device1.key_tracker, 0x08)) {
			arrowKeyDpad |= 0x01; // D-Left
		}
		if (tracker_is_active(&device1.key_tracker, 0x5D) || tracker_is_active(&device1.key_tracker, 0x09)) {
			arrowKeyDpad |= 0x04; // D-Down
		}
		if (tracker_is_active(&device1.key_tracker, 0x5E) || tracker_is_active(&device1.key_tracker, 0x06)) {
			arrowKeyDpad |= 0x08; // D-Up
		}
		if (tracker_is_active(&device1.key_tracker, 0x5F) || tracker_is_active(&device1.key_tracker, 0x07)) {
			arrowKeyDpad |= 0x02; // D-Right
		}
	}

	if (device2.initialized && device2.is_keyboard) {
		if (tracker_is_active(&device2.key_tracker, 0x5C)) {
			arrowKeyDpad |= 0x01; // D-Left
		}
		if (tracker_is_active(&device2.key_tracker, 0x5D)) {
			arrowKeyDpad |= 0x04; // D-Down
		}
		if (tracker_is_active(&device2.key_tracker, 0x5E)) {
			arrowKeyDpad |= 0x08; // D-Up
		}
		if (tracker_is_active(&device2.key_tracker, 0x5F)) {
			arrowKeyDpad |= 0x02; // D-Right
		}
	}

	// Pass through all inputs
	report.xStick = analogX;
	report.yStick = analogY;
	report.cxStick = cX;
	report.cyStick = cY;

	// Combine controller D-Pad with arrow key inputs
	report.dLeft = ((dpadState & 0x01) || (arrowKeyDpad & 0x01)) ? 1 : 0;
	report.dRight = ((dpadState & 0x02) || (arrowKeyDpad & 0x02)) ? 1 : 0;
	report.dDown = ((dpadState & 0x04) || (arrowKeyDpad & 0x04)) ? 1 : 0;
	report.dUp = ((dpadState & 0x08) || (arrowKeyDpad & 0x08)) ? 1 : 0;

	report.a = (buttons1 & 0x01) ? 1 : 0;
	report.b = (buttons1 & 0x02) ? 1 : 0;
	report.x = (buttons1 & 0x04) ? 1 : 0;
	report.y = (buttons1 & 0x08) ? 1 : 0;
	report.start = (buttons1 & 0x10) ? 1 : 0;

	report.z = (buttons2 & 0x10) ? 1 : 0;

	if (buttons2 & 0x40) {
		report.l = 1;
		report.analogL = 255;
	}
	if (buttons2 & 0x20) {
		report.r = 1;
		report.analogR = 255;
	}
}

GCReport getControllerState() {
	GCReport report = defaultGcReport;

	uint8_t dpadState = 0;
	uint8_t buttons1 = 0;
	uint8_t buttons2 = 0;
	uint8_t analogX = 128;
	uint8_t analogY = 128;
	uint8_t cX = 128;
	uint8_t cY = 128;

	// Handle device 1
	if (device1.initialized) {
		if (device1.is_keyboard) {
			buttons1 |= device1.backspace_held ? 0x02 : 0;
		} else {
			buttons1 |= device1.last_state[0];
			buttons2 |= device1.last_state[1];
			dpadState |= (device1.last_state[1] & 0x0F);
			analogX = device1.last_state[2];
			analogY = device1.last_state[3];
			cX = device1.last_state[4];
			cY = device1.last_state[5];
		}
	}

	// Handle device 2
	if (device2.initialized) {
		if (device2.is_keyboard) {
			buttons1 |= device2.backspace_held ? 0x02 : 0;
		} else {
			buttons1 |= device2.last_state[0];
			buttons2 |= device2.last_state[1];
			dpadState |= (device2.last_state[1] & 0x0F);
			if (!device1.initialized || device1.is_keyboard) {
				analogX = device2.last_state[2];
				analogY = device2.last_state[3];
				cX = device2.last_state[4];
				cY = device2.last_state[5];
			}
		}
	}

	// Always pass through controller inputs first
	handlePassthrough(report, buttons1, buttons2, dpadState, analogX, analogY, cX, cY);

	// If a script is playing, overlay script inputs on top of controller
	if (script_is_playing()) {
		script_overlay_report(report);
	}

	return report;
}
