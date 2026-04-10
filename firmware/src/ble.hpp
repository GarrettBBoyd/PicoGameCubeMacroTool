// ble.hpp - BLE connectivity interface
// Part of the Shiny Hunting Assistant Tool — Garrett Boyd (concept) & Claude/Anthropic (engineering)
// Claude · opus-4-6 · April 2026
#pragma once

#include <stdint.h>

// Initialize BLE stack and start advertising as "Shiny Hunting Assistant Tool"
// Must be called from Core 0 before multicore_launch_core1()
void ble_init();

// Check if a BLE client is currently connected
bool ble_is_connected();
