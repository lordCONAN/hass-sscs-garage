#pragma once

#ifdef USE_ESP32

#include "esphome/components/ble_client/ble_client.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

#ifdef USE_TEXT_SENSOR
#include "esphome/components/text_sensor/text_sensor.h"
#endif

#include "sscs_protocol.h"

namespace esphome::sscs_garage {

namespace espbt = esphome::esp32_ble_tracker;
using protocol::Command;

/// Outcome of one command session.
enum class Result : uint8_t {
  ACKED,         // cc00 received
  SENT_NO_ACK,   // command written, no cc00 in time (may still have moved)
  NOT_FOUND,     // never connected within connect_timeout
  NO_SERVICE,    // connected but GATT service/characteristics missing
  NO_CHALLENGE,  // no 20-byte challenge after subscribing
  REJECTED,      // token or command write refused
  DISCONNECTED,  // link dropped before the command was written
};

const char *result_to_string(Result result);
/// True when the command reached the controller (with or without ACK).
inline bool result_delivered(Result result) { return result == Result::ACKED || result == Result::SENT_NO_ACK; }

/**
 * Runs one short BLE session per command: connect, subscribe, answer the
 * challenge, write the command, wait for cc00, disconnect.
 *
 * Everything runs on the ESPHome main loop (GATT events are queued there by
 * esp32_ble), so there is no task or locking. A command that arrives while a
 * session is still authenticating replaces the queued one; a command arriving
 * after the write is held and runs in the next session.
 */
class SSCSGarage : public Component, public ble_client::BLEClientNode {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_BLUETOOTH - 1.0f; }

  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override;

  /// Queue a command. Safe to call at any time from the main loop.
  void send(Command cmd);
  bool is_busy() const { return this->phase_ != Phase::IDLE; }

  template<typename F> void add_on_result_callback(F &&callback) {
    this->result_callbacks_.add(std::forward<F>(callback));
  }

  void set_key(const protocol::Key &key) { this->key_ = key; }
  void set_passcode(int64_t passcode) { this->passcode_ = passcode; }
  void set_connect_timeout(uint32_t ms) { this->connect_timeout_ = ms; }
  void set_challenge_timeout(uint32_t ms) { this->challenge_timeout_ = ms; }
  void set_challenge_nudge(uint32_t ms) { this->challenge_nudge_ = ms; }
  void set_auth_settle(uint32_t ms) { this->auth_settle_ = ms; }
  void set_ack_timeout(uint32_t ms) { this->ack_timeout_ = ms; }
#ifdef USE_TEXT_SENSOR
  void set_status_text_sensor(text_sensor::TextSensor *sensor) { this->status_sensor_ = sensor; }
#endif

 protected:
  enum class Phase : uint8_t {
    IDLE,
    CONNECTING,        // waiting for ble_client to find + connect + discover
    WAIT_CHALLENGE,    // subscribed, waiting for the 20-byte challenge
    WAIT_AUTH_WRITE,   // token written, waiting for the write response
    AUTH_SETTLE,       // waiting for cb00 status (or settle timeout)
    WAIT_COMMAND_WRITE,
    WAIT_ACK,
  };

  void start_session_();
  void finish_(Result result);
  void enter_(Phase phase, uint32_t timeout_ms);
  void on_services_discovered_();
  void on_cmd_notify_(const uint8_t *data, uint16_t len);
  void on_cmd_write_(esp_gatt_status_t status);
  void write_token_(const uint8_t *packet20);
  void write_command_();
  bool write_cmd_char_(const uint8_t *data, uint16_t len);
  void publish_status_(const char *text);

  protocol::Key key_{};
  int64_t passcode_{protocol::NO_PASSCODE};

  uint32_t connect_timeout_{20000};
  uint32_t challenge_timeout_{5000};
  uint32_t challenge_nudge_{1200};  // 0 = never send the 00 00 nudge
  uint32_t auth_settle_{300};
  uint32_t ack_timeout_{3000};

  Phase phase_{Phase::IDLE};
  uint32_t phase_started_{0};
  uint32_t phase_timeout_{0};
  bool nudged_{false};
  bool status_seen_{false};
  uint8_t notify_regs_pending_{0};

  Command current_{Command::STOP};
  optional<Command> pending_{};  // next session

  uint16_t cmd_handle_{0};
  uint16_t data_handle_{0};

  // Write buffers live in the object so they outlive the async GATT write.
  protocol::Token token_{};
  std::array<uint8_t, protocol::COMMAND_LEN> command_buf_{};

  CallbackManager<void(Command, Result)> result_callbacks_{};
#ifdef USE_TEXT_SENSOR
  text_sensor::TextSensor *status_sensor_{nullptr};
#endif
};

}  // namespace esphome::sscs_garage

#endif  // USE_ESP32
