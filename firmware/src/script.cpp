// script.cpp - Script storage and playback engine
// Part of the Shiny Hunting Assistant Tool — Garrett Boyd (concept) & Claude/Anthropic (engineering)
// Claude · opus-4-6 · April 2026
#include "script.hpp"
#include "gcReport.hpp"
#include "pico/time.h"
#include <cstdio>
#include <cstring>

// Script storage
static ScriptStep script_steps[MAX_SCRIPT_STEPS];
static uint16_t script_num_steps = 0;

// Playback state
static uint8_t playback_state = PLAYBACK_IDLE;
static uint16_t playback_index = 0;
static absolute_time_t step_start_time;

// Upload buffer for multi-packet script data
static uint8_t upload_buf[2 + MAX_SCRIPT_STEPS * 8]; // cmd + num_steps(2) + step data
static uint16_t upload_buf_len = 0;
static uint16_t upload_expected_len = 0;
static bool upload_in_progress = false;

// BLE send callback (set by ble.cpp)
static void (*ble_send_fn)(const uint8_t *data, uint16_t len) = nullptr;

void script_set_ble_send(void (*fn)(const uint8_t *data, uint16_t len)) {
    ble_send_fn = fn;
}

static void send_response(const uint8_t *data, uint16_t len) {
    if (ble_send_fn) {
        ble_send_fn(data, len);
    }
}

static void send_ack(uint8_t status) {
    uint8_t rsp[] = { RSP_ACK, status };
    send_response(rsp, 2);
}

static void send_status() {
    uint8_t rsp[6];
    rsp[0] = RSP_STATUS;
    rsp[1] = playback_state;
    rsp[2] = (uint8_t)(playback_index & 0xFF);
    rsp[3] = (uint8_t)(playback_index >> 8);
    rsp[4] = (uint8_t)(script_num_steps & 0xFF);
    rsp[5] = (uint8_t)(script_num_steps >> 8);
    send_response(rsp, 6);
}

static void handle_upload(const uint8_t *data, uint16_t len) {
    // Data format: [num_steps_lo] [num_steps_hi] [step0...] [step1...] ...
    // Each step: [delay_ms (4 bytes LE)] [buttons (2 bytes LE)] [stick_x] [stick_y]
    if (len < 2) {
        printf("[Script] Upload too short\n");
        send_ack(STATUS_ERROR);
        return;
    }

    uint16_t num_steps = data[0] | (data[1] << 8);
    uint16_t expected_data = 2 + num_steps * 8;

    if (num_steps > MAX_SCRIPT_STEPS) {
        printf("[Script] Too many steps: %d (max %d)\n", num_steps, MAX_SCRIPT_STEPS);
        send_ack(STATUS_ERROR);
        return;
    }

    if (len < expected_data) {
        // Multi-packet upload - buffer the data
        upload_in_progress = true;
        upload_expected_len = expected_data;
        upload_buf_len = len;
        memcpy(upload_buf, data, len);
        printf("[Script] Upload started, got %d/%d bytes\n", len, expected_data);
        return;
    }

    // Complete upload - parse steps
    // Stop any current playback
    playback_state = PLAYBACK_IDLE;
    script_num_steps = num_steps;

    for (uint16_t i = 0; i < num_steps; i++) {
        const uint8_t *step_data = &data[2 + i * 8];
        script_steps[i].delay_ms = step_data[0] | (step_data[1] << 8) |
                                   (step_data[2] << 16) | (step_data[3] << 24);
        script_steps[i].buttons = step_data[4] | (step_data[5] << 8);
        script_steps[i].stick_x = step_data[6];
        script_steps[i].stick_y = step_data[7];
    }

    printf("[Script] Uploaded %d steps\n", num_steps);
    for (uint16_t i = 0; i < num_steps; i++) {
        printf("  Step %d: wait %lums, buttons=0x%04x, stick=(%d,%d)\n",
               i, script_steps[i].delay_ms, script_steps[i].buttons,
               script_steps[i].stick_x, script_steps[i].stick_y);
    }

    send_ack(STATUS_OK);
}

void script_init() {
    script_num_steps = 0;
    playback_state = PLAYBACK_IDLE;
    playback_index = 0;
    upload_in_progress = false;
    upload_buf_len = 0;
}

void script_process_ble_data(const uint8_t *data, uint16_t len) {
    if (len == 0) return;

    // Handle multi-packet upload continuation
    if (upload_in_progress) {
        uint16_t remaining = upload_expected_len - upload_buf_len;
        uint16_t to_copy = (len < remaining) ? len : remaining;
        memcpy(&upload_buf[upload_buf_len], data, to_copy);
        upload_buf_len += to_copy;
        printf("[Script] Upload chunk: %d/%d bytes\n", upload_buf_len, upload_expected_len);

        if (upload_buf_len >= upload_expected_len) {
            upload_in_progress = false;
            handle_upload(upload_buf, upload_buf_len);
        }
        return;
    }

    uint8_t cmd = data[0];

    switch (cmd) {
        case CMD_UPLOAD_SCRIPT:
            if (len > 1) {
                handle_upload(&data[1], len - 1);
            } else {
                send_ack(STATUS_ERROR);
            }
            break;

        case CMD_START_PLAYBACK:
            if (script_num_steps == 0) {
                printf("[Script] No script loaded\n");
                send_ack(STATUS_ERROR);
            } else {
                printf("[Script] Starting playback (%d steps)\n", script_num_steps);
                playback_index = 0;
                playback_state = PLAYBACK_WAITING;
                step_start_time = get_absolute_time();
                send_ack(STATUS_OK);
            }
            break;

        case CMD_STOP_PLAYBACK:
            printf("[Script] Stopping playback\n");
            playback_state = PLAYBACK_IDLE;
            send_ack(STATUS_OK);
            break;

        case CMD_QUERY_STATUS:
            send_status();
            break;

        default:
            printf("[Script] Unknown command: 0x%02x\n", cmd);
            send_ack(STATUS_ERROR);
            break;
    }
}

bool script_is_playing() {
    return playback_state != PLAYBACK_IDLE;
}

bool script_get_report(GCReport &report) {
    // Legacy function — kept for API compatibility but no longer used
    return false;
}

void script_overlay_report(GCReport &report) {
    // Overlay script inputs on top of the existing controller report.
    // During WAITING: controller passthrough works normally (no overlay).
    // During RUNNING: script button presses are OR'd with controller buttons,
    //                 and script stick positions override only if non-center.

    if (playback_state == PLAYBACK_IDLE) return;

    absolute_time_t now = get_absolute_time();

    if (playback_state == PLAYBACK_WAITING) {
        int64_t elapsed_ms = absolute_time_diff_us(step_start_time, now) / 1000;
        if (elapsed_ms < (int64_t)script_steps[playback_index].delay_ms) {
            // Still waiting — controller passthrough continues unchanged
            return;
        }
        // Delay elapsed, start executing this step
        playback_state = PLAYBACK_RUNNING;
        step_start_time = now;
    }

    if (playback_state == PLAYBACK_RUNNING) {
        int64_t elapsed_ms = absolute_time_diff_us(step_start_time, now) / 1000;

        ScriptStep &step = script_steps[playback_index];
        uint8_t buttons0 = step.buttons & 0xFF;
        uint8_t buttons1 = (step.buttons >> 8) & 0xFF;

        // OR script buttons with physical controller buttons
        if (buttons0 & 0x01) report.a = 1;
        if (buttons0 & 0x02) report.b = 1;
        if (buttons0 & 0x04) report.x = 1;
        if (buttons0 & 0x08) report.y = 1;
        if (buttons0 & 0x10) report.start = 1;
        if (buttons1 & 0x10) report.z = 1;
        if (buttons1 & 0x40) { report.l = 1; report.analogL = 255; }
        if (buttons1 & 0x20) { report.r = 1; report.analogR = 255; }
        if (buttons1 & 0x01) report.dLeft = 1;
        if (buttons1 & 0x02) report.dRight = 1;
        if (buttons1 & 0x04) report.dDown = 1;
        if (buttons1 & 0x08) report.dUp = 1;

        // Override stick only if script specifies non-center position
        if (step.stick_x != 128) report.xStick = step.stick_x;
        if (step.stick_y != 128) report.yStick = step.stick_y;

        // Hold the button press for ~50ms then advance
        if (elapsed_ms >= 50) {
            playback_index++;
            if (playback_index >= script_num_steps) {
                printf("[Script] Playback complete\n");
                playback_state = PLAYBACK_IDLE;
                return;
            }
            playback_state = PLAYBACK_WAITING;
            step_start_time = now;
        }
    }
}
