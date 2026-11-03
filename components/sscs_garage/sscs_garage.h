#pragma once

#ifdef USE_ESP32

#include "esphome/components/esp32_ble_client/ble_client_base.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/core/preferences.h"

#ifdef USE_TEXT_SENSOR
#include "esphome/components/text_sensor/text_sensor.h"
#endif
#ifdef USE_TEXT
#include "esphome/components/text/text.h"
#endif
#ifdef USE_BUTTON
#include "esphome/components/button/button.h"
#endif

#include <array>

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
  NO_KEY,        // no key in the YAML and none entered from Home Assistant
  NO_DEVICE,     // no shutter selected and not exactly one nearby to pick
};

const char *result_to_string(Result result);
/// True when the command reached the controller (with or without ACK).
inline bool result_delivered(Result result) { return result == Result::ACKED || result == Result::SENT_NO_ACK; }

class DeviceText;

/**
 * BLE client for one SSCs shutter controller.
 *
 * Runs one short session per command: connect, subscribe, answer the challenge,
 * write the command, wait for cc00, disconnect. Everything runs on the ESPHome
 * main loop (esp32_ble queues GATT events there), so there is no task or locking.
 * A command that arrives while a session is still authenticating replaces the
 * queued one; a command arriving after the write runs in the next session.
 *
 * The shutter's address is not compiled in: the component watches advertisements
 * for the SSCs name, auto-selects the only one nearby, and lets Home Assistant
 * pick a specific one. The choice is stored on the device.
 */
class SSCSGarage : public esp32_ble_client::BLEClientBase {
 public:
  void setup() override;
  void dump_config() override;

  bool parse_device(const espbt::ESPBTDevice &device) override;
  bool gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override;

  /// Queue a command. Safe to call at any time from the main loop.
  void send(Command cmd);
  bool is_busy() const { return this->phase_ != Phase::IDLE; }

  template<typename F> void add_on_result_callback(F &&callback) {
    this->result_callbacks_.add(std::forward<F>(callback));
  }

  // ---- key
  /// Key from the YAML. A key entered from Home Assistant (stored on the device) takes precedence.
  void set_key(const protocol::Key &key) {
    this->config_key_ = key;
    this->has_config_key_ = true;
  }
  /// Key typed or pasted in Home Assistant: hex, or the setup QR text. "clear" or empty
  /// removes the stored key and falls back to the YAML key. Returns false if rejected.
  bool set_key_from_text(const std::string &value);

  // ---- device selection
  /// Address from the YAML (optional). A device chosen in Home Assistant takes precedence.
  void set_config_address(uint64_t address) { this->config_address_ = address; }
  void add_name_prefix(const char *prefix) {
    if (this->name_prefix_count_ < this->name_prefixes_.size())
      this->name_prefixes_[this->name_prefix_count_++] = prefix;
  }
  /// A MAC address ("AA:BB:CC:DD:EE:FF"), or "auto"/empty to forget the choice and search again.
  bool set_device_from_text(const std::string &value);
  void set_auto_select_window(uint32_t ms) { this->auto_select_window_ = ms; }

  void set_passcode(int64_t passcode) { this->passcode_ = passcode; }
  void set_connect_timeout(uint32_t ms) { this->connect_timeout_ = ms; }
  void set_challenge_timeout(uint32_t ms) { this->challenge_timeout_ = ms; }
  void set_challenge_nudge(uint32_t ms) { this->challenge_nudge_ = ms; }
  void set_auth_settle(uint32_t ms) { this->auth_settle_ = ms; }
  void set_ack_timeout(uint32_t ms) { this->ack_timeout_ = ms; }
#ifdef USE_TEXT_SENSOR
  void set_status_text_sensor(text_sensor::TextSensor *sensor) { this->status_sensor_ = sensor; }
  void set_key_status_text_sensor(text_sensor::TextSensor *sensor) { this->key_status_sensor_ = sensor; }
  void set_nearby_text_sensor(text_sensor::TextSensor *sensor) { this->nearby_sensor_ = sensor; }
#endif
#ifdef USE_TEXT
  void set_device_text(DeviceText *text) { this->device_text_ = text; }
#endif

 protected:
  enum class Phase : uint8_t {
    IDLE,
    CONNECTING,        // waiting for the advert, connect and service discovery
    WAIT_CHALLENGE,    // subscribed, waiting for the 20-byte challenge
    WAIT_AUTH_WRITE,   // token written, waiting for the write response
    AUTH_SETTLE,       // waiting for cb00 status (or settle timeout)
    WAIT_COMMAND_WRITE,
    WAIT_ACK,
  };

  // Stored in NVS; the magic tells a stored value from an empty/cleared slot.
  struct KeyRecord {
    uint32_t magic;
    uint8_t key[protocol::KEY_LEN];
  } __attribute__((packed));
  struct AddressRecord {
    uint32_t magic;
    uint64_t address;
  } __attribute__((packed));
  static constexpr uint32_t KEY_RECORD_MAGIC = 0x53534B31;      // "SSK1"
  static constexpr uint32_t ADDRESS_RECORD_MAGIC = 0x53534131;  // "SSA1"

  enum class KeySource : uint8_t { KEY_SOURCE_NONE, KEY_SOURCE_CONFIG, KEY_SOURCE_STORED };
  enum class DeviceSource : uint8_t { DEVICE_SOURCE_NONE, DEVICE_SOURCE_CONFIG, DEVICE_SOURCE_STORED };

  struct Candidate {
    uint64_t address;
    uint32_t last_seen;
    int8_t rssi;
    char name[20];
  };
  static constexpr size_t MAX_CANDIDATES = 6;
  static constexpr uint32_t CANDIDATE_EXPIRY_MS = 5 * 60 * 1000;

  // session
  void start_session_();
  void finish_(Result result);
  void enter_(Phase phase, uint32_t timeout_ms);
  void on_phase_timeout_();
  void on_services_discovered_();
  void on_cmd_notify_(const uint8_t *data, uint16_t len);
  void on_cmd_write_(esp_gatt_status_t status);
  void write_token_(const uint8_t *packet20);
  void write_command_();
  bool write_cmd_char_(const uint8_t *data, uint16_t len);
  void end_link_();
  void publish_status_(const char *text);

  // key
  void load_key_();
  void publish_key_status_(const char *note = nullptr);

  // device selection
  void load_device_();
  void select_device_(uint64_t address, bool store);
  void forget_device_();
  bool matches_name_(const espbt::ESPBTDevice &device) const;
  void track_candidate_(const espbt::ESPBTDevice &device);
  void maintain_candidates_();
  void publish_device_();
  void publish_nearby_();
  bool has_device_() const { return this->device_source_ != DeviceSource::DEVICE_SOURCE_NONE; }

  // ---- key state
  protocol::Key key_{};
  protocol::Key config_key_{};
  bool has_config_key_{false};
  KeySource key_source_{KeySource::KEY_SOURCE_NONE};
  ESPPreferenceObject key_pref_;
  int64_t passcode_{protocol::NO_PASSCODE};

  // ---- device state
  uint64_t config_address_{0};
  uint64_t device_address_{0};
  DeviceSource device_source_{DeviceSource::DEVICE_SOURCE_NONE};
  ESPPreferenceObject device_pref_;
  std::array<const char *, 4> name_prefixes_{};
  uint8_t name_prefix_count_{0};
  std::array<Candidate, MAX_CANDIDATES> candidates_{};
  uint8_t candidate_count_{0};
  bool candidates_changed_{false};
  uint32_t search_started_{0};
  uint32_t auto_select_window_{10000};
  uint32_t last_nearby_publish_{0};

  // ---- timing
  uint32_t connect_timeout_{20000};
  uint32_t challenge_timeout_{5000};
  uint32_t challenge_nudge_{1200};  // 0 = never send the 00 00 nudge
  uint32_t auth_settle_{300};
  uint32_t ack_timeout_{3000};

  // ---- session state
  Phase phase_{Phase::IDLE};
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
  text_sensor::TextSensor *key_status_sensor_{nullptr};
  text_sensor::TextSensor *nearby_sensor_{nullptr};
#endif
#ifdef USE_TEXT
  DeviceText *device_text_{nullptr};
#endif
};

#ifdef USE_TEXT
/// Write-only key input. The entered value is never published back: the state stays empty.
class KeyText : public text::Text, public Component, public Parented<SSCSGarage> {
 public:
  void setup() override;

 protected:
  void control(const std::string &value) override;
};

/// Shows the selected shutter's address; accepts a MAC or "auto".
class DeviceText : public text::Text, public Component, public Parented<SSCSGarage> {
 protected:
  void control(const std::string &value) override;
};
#endif

#ifdef USE_BUTTON
/// Forget the selected shutter and search again.
class FindButton : public button::Button, public Parented<SSCSGarage> {
 protected:
  void press_action() override;
};
#endif

}  // namespace esphome::sscs_garage

#endif  // USE_ESP32
