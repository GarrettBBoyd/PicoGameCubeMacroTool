// ble.cpp - BLE connectivity using Nordic UART Service
// Part of the GameCubeMacroTool — Garrett Boyd (concept) & Claude/Anthropic (engineering)
// Claude · opus-4-6 · April 2026
#include "ble.hpp"
#include "script.hpp"

extern "C" {
#include "pico/cyw43_arch.h"
#include "btstack.h"
#include "ble/gatt-service/nordic_spp_service_server.h"
}

// Generated GATT database
#include "shiny_tool.h"

// Connection state
static hci_con_handle_t con_handle = HCI_CON_HANDLE_INVALID;
static bool connected = false;
static bool notifications_enabled = false;  // True when client subscribes to TX

// Send queue (for async "can send now" fallback)
static uint8_t send_buf[64];
static uint16_t send_buf_len = 0;
static bool send_pending = false;
static btstack_context_callback_registration_t send_request;

// Advertisement data: flags + Nordic UART Service UUID
static const uint8_t adv_data[] = {
    0x02, 0x01, 0x06,
    0x11, 0x06,
    0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0,
    0x93, 0xF3, 0xA3, 0xB5, 0x01, 0x00, 0x40, 0x6E,
};
static const uint8_t adv_data_len = sizeof(adv_data);

// Scan response: complete local name ("GameCubeMacroTool" = 17 chars, +1 type byte = 18)
static const uint8_t scan_resp_data[] = {
    18, 0x09,
    'G','a','m','e','C','u','b','e',
    'M','a','c','r','o','T','o','o','l',
};
static const uint8_t scan_resp_data_len = sizeof(scan_resp_data);

static btstack_packet_callback_registration_t hci_event_callback_registration;

// Forward declarations
static void packet_handler(uint8_t packet_type, uint16_t channel, uint8_t *packet, uint16_t size);
static void nordic_spp_handler(uint8_t packet_type, uint16_t channel, uint8_t *packet, uint16_t size);
static void start_advertising(void);

// BLE send function (called by script engine)
static void ble_send_data(const uint8_t *data, uint16_t len) {
    if (!connected || con_handle == HCI_CON_HANDLE_INVALID) {
        printf("[BLE] Send failed: not connected\n");
        return;
    }
    if (!notifications_enabled) {
        printf("[BLE] Send failed: client hasn't subscribed to notifications yet\n");
        return;
    }
    if (len > sizeof(send_buf)) len = sizeof(send_buf);

    // Try direct send via att_server_notify
    int err = nordic_spp_service_server_send(con_handle, data, len);
    if (err == 0) {
        printf("[BLE] Send OK (%d bytes)\n", len);
        return;
    }

    // Direct send failed (ATT busy), queue for async retry
    printf("[BLE] Send queued (%d bytes, err=%d)\n", len, err);
    memcpy(send_buf, data, len);
    send_buf_len = len;
    send_pending = true;
    nordic_spp_service_server_request_can_send_now(&send_request, con_handle);
}

static void start_advertising(void) {
    uint16_t adv_int_min = 0x0030;
    uint16_t adv_int_max = 0x00A0;
    uint8_t adv_type = 0;
    bd_addr_t null_addr = {0};
    gap_advertisements_set_params(adv_int_min, adv_int_max, adv_type, 0, null_addr, 0x07, 0x00);
    gap_advertisements_set_data(adv_data_len, (uint8_t *)adv_data);
    gap_scan_response_set_data(scan_resp_data_len, (uint8_t *)scan_resp_data);
    gap_advertisements_enable(1);
}

static void packet_handler(uint8_t packet_type, uint16_t channel, uint8_t *packet, uint16_t size) {
    (void)channel;
    (void)size;

    if (packet_type != HCI_EVENT_PACKET) return;

    uint8_t event_type = hci_event_packet_get_type(packet);

    switch (event_type) {
        case BTSTACK_EVENT_STATE:
            if (btstack_event_state_get_state(packet) == HCI_STATE_WORKING) {
                printf("[BLE] Stack running, starting advertising\n");
                start_advertising();
            }
            break;

        case HCI_EVENT_LE_META:
            if (hci_event_le_meta_get_subevent_code(packet) == HCI_SUBEVENT_LE_CONNECTION_COMPLETE) {
                con_handle = hci_subevent_le_connection_complete_get_connection_handle(packet);
                connected = true;
                printf("[BLE] Connected (handle 0x%04x)\n", con_handle);
            }
            break;

        case HCI_EVENT_DISCONNECTION_COMPLETE:
            con_handle = HCI_CON_HANDLE_INVALID;
            connected = false;
            notifications_enabled = false;
            send_pending = false;
            printf("[BLE] Disconnected, re-advertising\n");
            start_advertising();
            break;

        case SM_EVENT_JUST_WORKS_REQUEST:
            sm_just_works_confirm(sm_event_just_works_request_get_handle(packet));
            break;

        default:
            break;
    }
}

static void nordic_spp_handler(uint8_t packet_type, uint16_t channel, uint8_t *packet, uint16_t size) {
    (void)channel;

    switch (packet_type) {
        case RFCOMM_DATA_PACKET:
            // Data received from BLE client
            printf("[BLE] Received %d bytes: ", size);
            for (uint16_t i = 0; i < size && i < 16; i++) printf("%02x ", packet[i]);
            if (size > 16) printf("...");
            printf("\n");
            script_process_ble_data(packet, size);
            break;

        case HCI_EVENT_PACKET: {
            uint8_t event_type = hci_event_packet_get_type(packet);

            if (event_type == HCI_EVENT_GATTSERVICE_META) {
                uint8_t subevent = packet[2];  // subevent code is at offset 2
                switch (subevent) {
                    case GATTSERVICE_SUBEVENT_SPP_SERVICE_CONNECTED:
                        notifications_enabled = true;
                        printf("[BLE] Notifications enabled (SPP service connected)\n");
                        break;
                    case GATTSERVICE_SUBEVENT_SPP_SERVICE_DISCONNECTED:
                        notifications_enabled = false;
                        printf("[BLE] Notifications disabled (SPP service disconnected)\n");
                        break;
                    default:
                        printf("[BLE] GATT service meta subevent: 0x%02x\n", subevent);
                        break;
                }
            } else if (event_type == ATT_EVENT_CAN_SEND_NOW) {
                // Flush pending send buffer
                if (send_pending && con_handle != HCI_CON_HANDLE_INVALID) {
                    int err = nordic_spp_service_server_send(con_handle, send_buf, send_buf_len);
                    printf("[BLE] Async send: %d bytes, err=%d\n", send_buf_len, err);
                    send_pending = false;
                }
            } else {
                printf("[BLE] Nordic handler event: 0x%02x\n", event_type);
            }
            break;
        }

        default:
            break;
    }
}

void ble_init() {
    printf("[BLE] Initializing CYW43...\n");

    if (cyw43_arch_init()) {
        printf("[BLE] CYW43 init failed!\n");
        return;
    }

    // Initialize script engine and wire up BLE send
    script_init();
    script_set_ble_send(&ble_send_data);

    l2cap_init();

    sm_init();
    sm_set_io_capabilities(IO_CAPABILITY_NO_INPUT_NO_OUTPUT);
    sm_set_authentication_requirements(SM_AUTHREQ_NO_BONDING);

    att_server_init(profile_data, NULL, NULL);

    nordic_spp_service_server_init(&nordic_spp_handler);

    hci_event_callback_registration.callback = &packet_handler;
    hci_add_event_handler(&hci_event_callback_registration);

    hci_power_control(HCI_POWER_ON);

    printf("[BLE] Init complete\n");
}

bool ble_is_connected() {
    return connected;
}
