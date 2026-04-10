// script.hpp - Script engine protocol and data structures
// Part of the Shiny Hunting Assistant Tool — Garrett Boyd (concept) & Claude/Anthropic (engineering)
// Claude · opus-4-6 · April 2026
#pragma once

#include <stdint.h>
#include "gcReport.hpp"

// Protocol command bytes
#define CMD_UPLOAD_SCRIPT  0x01
#define CMD_START_PLAYBACK 0x02
#define CMD_STOP_PLAYBACK  0x03
#define CMD_QUERY_STATUS   0x04

// Response bytes
#define RSP_ACK            0x80
#define RSP_STATUS         0x81

// Status codes
#define STATUS_OK          0x00
#define STATUS_ERROR       0x01

// Playback states
#define PLAYBACK_IDLE      0x00
#define PLAYBACK_RUNNING   0x01
#define PLAYBACK_WAITING   0x02

// Max script steps (each step is 8 bytes, 256 steps = 2KB)
#define MAX_SCRIPT_STEPS   256

// Button bitmask layout (matches GC controller bit layout)
// Byte 0 (low byte): A=0x01, B=0x02, X=0x04, Y=0x08, Start=0x10
// Byte 1 (high byte): Z=0x10, L=0x40, R=0x20, DUp=0x08, DDown=0x04, DLeft=0x01, DRight=0x02

struct ScriptStep {
    uint32_t delay_ms;     // Milliseconds to wait before this step
    uint16_t buttons;      // Button bitmask (low=byte0, high=byte1)
    uint8_t stick_x;       // Left stick X (128=center)
    uint8_t stick_y;       // Left stick Y (128=center)
};

// Initialize the script engine
void script_init();

// Set the BLE send callback (called by ble.cpp during init)
void script_set_ble_send(void (*fn)(const uint8_t *data, uint16_t len));

// Process incoming BLE data (called from BLE handler)
void script_process_ble_data(const uint8_t *data, uint16_t len);

// Check if a script is currently playing
bool script_is_playing();

// Legacy: get full script report (replaced by overlay approach)
bool script_get_report(GCReport &report);

// Overlay script inputs on top of existing controller report
// Called after handlePassthrough so physical controller always works
void script_overlay_report(GCReport &report);
