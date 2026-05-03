// script.cpp - Script storage and playback engine
// Part of the GameCubeMacroTool — Garrett Boyd (concept) & Claude/Anthropic (engineering)
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
// volatile because they're modified from the timer ISR and read from main loop
static volatile uint8_t  playback_state = PLAYBACK_IDLE;
static volatile uint16_t playback_index = 0;
static volatile bool     playback_finished_flag = false;  // ISR sets this; main loop logs

// Hardware timer alarm — replaces polling-based time checks for jitter-free playback
static alarm_id_t playback_alarm = 0;

// Hold duration (ms) — how long buttons stay pressed each step
#define PLAYBACK_HOLD_MS 50

// Forward declaration (defined below script_init)
static void cancel_playback_alarm();

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
    cancel_playback_alarm();
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

// Hardware timer alarm callback — runs in IRQ context.
// Returns next fire delay (microseconds) relative to the previous fire time:
//   - Positive value: reschedule N µs after this firing → deterministic, no drift
//   - Zero: cancel the alarm (we're done)
// Keep this short and avoid printf / blocking calls — runs in interrupt context.
static int64_t playback_alarm_cb(alarm_id_t /*id*/, void* /*user*/) {
    uint8_t state = playback_state;

    if (state == PLAYBACK_IDLE) {
        return 0;  // playback was stopped externally
    }

    if (state == PLAYBACK_WAITING) {
        // Delay elapsed → press buttons for this step
        playback_state = PLAYBACK_RUNNING;
        return (int64_t)PLAYBACK_HOLD_MS * 1000;  // schedule release exactly N ms later
    }

    if (state == PLAYBACK_RUNNING) {
        // Hold elapsed → advance to next step (or finish)
        uint16_t next = playback_index + 1;
        if (next >= script_num_steps) {
            playback_state = PLAYBACK_IDLE;
            playback_finished_flag = true;
            return 0;  // cancel — no more steps
        }
        playback_index = next;
        playback_state = PLAYBACK_WAITING;
        return (int64_t)script_steps[next].delay_ms * 1000;  // wait next delay
    }

    return 0;  // unknown state → stop
}

static void cancel_playback_alarm() {
    if (playback_alarm > 0) {
        cancel_alarm(playback_alarm);
        playback_alarm = 0;
    }
}

void script_init() {
    script_num_steps = 0;
    playback_state = PLAYBACK_IDLE;
    playback_index = 0;
    upload_in_progress = false;
    upload_buf_len = 0;
    cancel_playback_alarm();
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
                printf("[Script] Starting playback (%d steps) — hardware-timer mode\n",
                       script_num_steps);
                cancel_playback_alarm();              // belt-and-suspenders cleanup
                playback_finished_flag = false;
                playback_index = 0;
                playback_state = PLAYBACK_WAITING;
                // Schedule first wakeup at step[0].delay_ms — the ISR self-reschedules
                // for every subsequent transition, so timing is purely hardware-driven.
                uint32_t first_delay_ms = script_steps[0].delay_ms;
                if (first_delay_ms == 0) first_delay_ms = 1;  // alarm needs >0
                playback_alarm = add_alarm_in_ms(first_delay_ms,
                                                 playback_alarm_cb, nullptr, true);
                send_ack(STATUS_OK);
            }
            break;

        case CMD_STOP_PLAYBACK:
            printf("[Script] Stopping playback\n");
            playback_state = PLAYBACK_IDLE;
            cancel_playback_alarm();
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
    //
    // The hardware timer ISR (playback_alarm_cb) advances state at exact times.
    // This function is a pure state-reader — no time math, no jitter — just:
    //   IDLE / WAITING → no overlay (passthrough only)
    //   RUNNING        → OR script buttons onto the report, override stick if non-center
    //
    // Log "playback complete" lazily here (printf is unsafe from the ISR).
    if (playback_finished_flag) {
        playback_finished_flag = false;
        printf("[Script] Playback complete\n");
    }

    if (playback_state != PLAYBACK_RUNNING) return;

    // Single 16-bit read is atomic on Cortex-M33; capture index for safe access
    uint16_t idx = playback_index;
    if (idx >= script_num_steps) return;  // sanity guard against ISR race

    ScriptStep &step = script_steps[idx];
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
}
