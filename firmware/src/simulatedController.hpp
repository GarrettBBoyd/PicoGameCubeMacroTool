#pragma once

#include "types.hpp"
#include "gcReport.hpp"

// Pass through controller inputs to GC report
void handlePassthrough(GCReport& report, uint8_t buttons1, uint8_t buttons2, uint8_t dpadState,
					  uint8_t analogX, uint8_t analogY, uint8_t cX, uint8_t cY);

// Get the final state of the simulated controller
GCReport getControllerState();
