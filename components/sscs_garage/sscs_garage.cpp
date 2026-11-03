#include "sscs_garage.h"

#ifdef USE_ESP32

#include "esphome/components/esp32_ble/ble.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <algorithm>
#include <cctype>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <strings.h>

namespace esphome::sscs_garage {

static const char *const TAG = "sscs_garage";

static const char *const PHASE_TIMEOUT = "sscs_phase";
static const char *const NUDGE_TIMEOUT = "sscs_nudge";
static const char *const SCAN_INTERVAL = "sscs_scan";
static constexpr uint32_t SCAN_MAINTENANCE_MS = 2000;
static constexpr uint32_t NEARBY_REFRESH_MS = 60000;

using protocol::command_to_string;

const char *result_to_string(Result result) {
  switch (result) {
    case Result::ACKED:
      return "acknowledged";
    case Result::SENT_NO_ACK:
      return "sent, no acknowledgement";
    case Result::NOT_FOUND:
      return "shutter not found";
    case Result::NO_SERVICE:
      return "GATT service missing";
    case Result::NO_CHALLENGE:
      return "no challenge received";
    case Result::REJECTED:
      return "write rejected";
    case Result::DISCONNECTED:
      return "disconnected early";
    case Result::NO_KEY:
      return "no key set";
    case Result::NO_DEVICE:
      return "no shutter selected";
  }
  return "unknown";
}

static void trim(const std::string &value, const char *&text, size_t &len) {
  size_t start = 0;
  size_t end = value.size();
  while (start < end && isspace(static_cast<unsigned char>(value[start])))
    start++;
  while (end > start && isspace(static_cast<unsigned char>(value[end - 1])))
    end--;
  text = value.c_str() + start;
  len = end - start;
}

static void format_address(uint64_t address, char out[18]) {
  snprintf(out, 18, "%02X:%02X:%02X:%02X:%02X:%02X", static_cast<unsigned>((address >> 40) & 0xFF),
           static_cast<unsigned>((address >> 32) & 0xFF), static_cast<unsigned>((address >> 24) & 0xFF),
           static_cast<unsigned>((address >> 16) & 0xFF), static_cast<unsigned>((address >> 8) & 0xFF),
           static_cast<unsigned>(address & 0xFF));
}

/// 12 hex digits, separators ':' '-' ' ' allowed. Returns false on anything else.
static bool parse_address(const char *text, size_t len, uint64_t &out) {
  uint64_t value = 0;
  int digits = 0;
  for (size_t i = 0; i < len; i++) {
    const char c = text[i];
    if (c == ':' || c == '-' || c == ' ')
      continue;
    int v;
    if (c >= '0' && c <= '9')
      v = c - '0';
    else if (c >= 'a' && c <= 'f')
      v = c - 'a' + 10;
    else if (c >= 'A' && c <= 'F')
      v = c - 'A' + 10;
    else
      return false;
    value = (value << 4) | static_cast<uint64_t>(v);
    digits++;
  }
  if (digits != 12 || value == 0)
    return false;
  out = value;
  return true;
}

// ---------------------------------------------------------------- lifecycle

void SSCSGarage::setup() {
  BLEClientBase::setup();
  // The base class connects on an advertisement from address_ whenever auto-connect is on;
  // parse_device() below only lets that happen while a command session wants a link.
  this->set_auto_connect(true);
  this->load_key_();
  this->load_device_();
  this->search_started_ = millis();
  this->set_interval(SCAN_INTERVAL, SCAN_MAINTENANCE_MS, [this]() { this->maintain_candidates_(); });
  // BLE is not up yet: this only sets whether the tracker's first scan after boot runs.
  this->update_scanning_();
  this->publish_nearby_();
  this->publish_status_("Idle");
}

void SSCSGarage::dump_config() {
  BLEClientBase::dump_config();
  ESP_LOGCONFIG(TAG,
                "SSCs Garage:\n"
                "  Passcode mode: %s\n"
                "  Connect timeout: %" PRIu32 " ms\n"
                "  Challenge timeout: %" PRIu32 " ms (nudge after %" PRIu32 " ms)\n"
                "  Auth settle: %" PRIu32 " ms\n"
                "  ACK timeout: %" PRIu32 " ms",
                this->passcode_ == protocol::NO_PASSCODE ? "none (verified)" : "passcode (UNVERIFIED)",
                this->connect_timeout_, this->challenge_timeout_, this->challenge_nudge_, this->auth_settle_,
                this->ack_timeout_);
  for (uint8_t i = 0; i < this->name_prefix_count_; i++)
    ESP_LOGCONFIG(TAG, "  Name prefix: '%s'", this->name_prefixes_[i]);
  if (this->has_device_()) {
    char addr[18];
    format_address(this->device_address_, addr);
    ESP_LOGCONFIG(TAG, "  Shutter: %s (%s)", addr,
                  this->device_source_ == DeviceSource::DEVICE_SOURCE_STORED ? "chosen" : "from YAML");
  } else {
    ESP_LOGCONFIG(TAG, "  Shutter: not selected, searching");
  }
}

// ---------------------------------------------------------------- key

void SSCSGarage::load_key_() {
  this->key_pref_ = global_preferences->make_preference<KeyRecord>(fnv1_hash("sscs_garage_key"), true);
  KeyRecord record{};
  if (this->key_pref_.load(&record) && record.magic == KEY_RECORD_MAGIC) {
    std::memcpy(this->key_.data(), record.key, protocol::KEY_LEN);
    this->key_source_ = KeySource::KEY_SOURCE_STORED;
  } else if (this->has_config_key_) {
    this->key_ = this->config_key_;
    this->key_source_ = KeySource::KEY_SOURCE_CONFIG;
  } else {
    this->key_source_ = KeySource::KEY_SOURCE_NONE;
  }
  std::memset(&record, 0, sizeof(record));
  this->publish_key_status_();
}

bool SSCSGarage::set_key_from_text(const std::string &value) {
  const char *text;
  size_t len;
  trim(value, text, len);

  KeyRecord record{};
  if (len == 0 || (len == 5 && strncasecmp(text, "clear", 5) == 0)) {
    record.magic = 0;
    this->key_pref_.save(&record);
    global_preferences->sync();
    if (this->has_config_key_) {
      this->key_ = this->config_key_;
      this->key_source_ = KeySource::KEY_SOURCE_CONFIG;
    } else {
      this->key_.fill(0);
      this->key_source_ = KeySource::KEY_SOURCE_NONE;
    }
    ESP_LOGI(TAG, "Stored key cleared");
    this->publish_key_status_();
    return true;
  }

  protocol::Key parsed{};
  const auto result = protocol::parse_key(text, len, parsed);
  if (result != protocol::KeyParseResult::KEY_PARSE_RESULT_OK) {
    ESP_LOGW(TAG, "Key rejected: %s", protocol::key_parse_result_to_string(result));
    this->publish_key_status_(protocol::key_parse_result_to_string(result));
    return false;
  }

  record.magic = KEY_RECORD_MAGIC;
  std::memcpy(record.key, parsed.data(), protocol::KEY_LEN);
  const bool saved = this->key_pref_.save(&record) && global_preferences->sync();
  std::memset(&record, 0, sizeof(record));
  this->key_ = parsed;
  parsed.fill(0);
  this->key_source_ = KeySource::KEY_SOURCE_STORED;

  char fp[9];
  protocol::key_fingerprint(this->key_, fp);
  ESP_LOGI(TAG, "Key updated (fingerprint %s)%s", fp, saved ? "" : ", but saving to flash failed");
  this->publish_key_status_(saved ? nullptr : "saving to flash failed");
  return true;
}

void SSCSGarage::publish_key_status_(const char *note) {
#ifdef USE_TEXT_SENSOR
  if (this->key_status_sensor_ == nullptr)
    return;
  char fp[9];
  protocol::key_fingerprint(this->key_, fp);
  char current[48];
  switch (this->key_source_) {
    case KeySource::KEY_SOURCE_STORED:
      snprintf(current, sizeof(current), "Set from HA, fingerprint %s", fp);
      break;
    case KeySource::KEY_SOURCE_CONFIG:
      snprintf(current, sizeof(current), "From YAML, fingerprint %s", fp);
      break;
    case KeySource::KEY_SOURCE_NONE:
      snprintf(current, sizeof(current), "Not set");
      break;
  }
  if (note == nullptr) {
    this->key_status_sensor_->publish_state(current);
    return;
  }
  char buf[160];
  snprintf(buf, sizeof(buf), "%s (%s)", current, note);
  this->key_status_sensor_->publish_state(buf);
#endif
}

// ---------------------------------------------------------------- device selection

void SSCSGarage::load_device_() {
  this->device_pref_ = global_preferences->make_preference<AddressRecord>(fnv1_hash("sscs_garage_device"), true);
  AddressRecord record{};
  if (this->device_pref_.load(&record) && record.magic == ADDRESS_RECORD_MAGIC && record.address != 0) {
    this->device_address_ = record.address;
    this->device_source_ = DeviceSource::DEVICE_SOURCE_STORED;
  } else if (this->config_address_ != 0) {
    this->device_address_ = this->config_address_;
    this->device_source_ = DeviceSource::DEVICE_SOURCE_CONFIG;
  } else {
    this->device_address_ = 0;
    this->device_source_ = DeviceSource::DEVICE_SOURCE_NONE;
  }
  this->set_address(this->device_address_);
  this->publish_device_();
}

void SSCSGarage::select_device_(uint64_t address, bool store) {
  this->device_address_ = address;
  this->device_source_ = store ? DeviceSource::DEVICE_SOURCE_STORED : DeviceSource::DEVICE_SOURCE_CONFIG;
  this->set_address(address);
  if (store) {
    AddressRecord record{ADDRESS_RECORD_MAGIC, address};
    this->device_pref_.save(&record);
    global_preferences->sync();
  }
  char addr[18];
  format_address(address, addr);
  ESP_LOGI(TAG, "Shutter selected: %s", addr);
  this->publish_device_();
  this->publish_nearby_();  // freeze the list with the "selected" marker
  this->update_scanning_();
}

void SSCSGarage::forget_device_() {
  AddressRecord record{0, 0};
  this->device_pref_.save(&record);
  global_preferences->sync();
  if (this->config_address_ != 0) {
    this->select_device_(this->config_address_, false);
    return;
  }
  this->device_address_ = 0;
  this->device_source_ = DeviceSource::DEVICE_SOURCE_NONE;
  this->set_address(0);
  this->search_started_ = millis();
  this->candidate_count_ = 0;  // only shutters heard during this search can be auto-selected
  ESP_LOGI(TAG, "Shutter choice cleared, searching");
  this->publish_device_();
  this->publish_nearby_();
  this->update_scanning_();
}

bool SSCSGarage::set_device_from_text(const std::string &value) {
  if (this->is_busy()) {
    ESP_LOGW(TAG, "Busy sending a command; change the shutter afterwards");
    this->publish_device_();
    return false;
  }
  const char *text;
  size_t len;
  trim(value, text, len);
  if (len == 0 || (len == 4 && strncasecmp(text, "auto", 4) == 0)) {
    this->forget_device_();
    return true;
  }
  uint64_t address;
  if (!parse_address(text, len, address)) {
    ESP_LOGW(TAG, "Not a Bluetooth address; use AA:BB:CC:DD:EE:FF or 'auto'");
    this->publish_device_();
    return false;
  }
  this->select_device_(address, true);
  return true;
}

bool SSCSGarage::matches_name_(const espbt::ESPBTDevice &device) const {
  const auto name = device.get_name();
  if (name.empty())
    return false;
  for (uint8_t i = 0; i < this->name_prefix_count_; i++) {
    if (name.starts_with(this->name_prefixes_[i]))
      return true;
  }
  return false;
}

void SSCSGarage::track_candidate_(const espbt::ESPBTDevice &device) {
  const uint64_t address = device.address_uint64();
  const uint32_t now = millis();
  Candidate *slot = nullptr;
  for (uint8_t i = 0; i < this->candidate_count_; i++) {
    if (this->candidates_[i].address == address) {
      slot = &this->candidates_[i];
      break;
    }
  }
  if (slot == nullptr) {
    if (this->candidate_count_ < MAX_CANDIDATES) {
      slot = &this->candidates_[this->candidate_count_++];
    } else {
      slot = &this->candidates_[0];  // replace the one heard from longest ago
      for (auto &c : this->candidates_) {
        if (c.last_seen < slot->last_seen)
          slot = &c;
      }
    }
    slot->address = address;
    const auto name = device.get_name();
    const size_t n = std::min(name.size(), sizeof(slot->name) - 1);
    std::memcpy(slot->name, name.c_str(), n);
    slot->name[n] = '\0';
    this->candidates_changed_ = true;
    char addr[18];
    format_address(address, addr);
    ESP_LOGI(TAG, "Found shutter %s '%s'", addr, slot->name);
  }
  slot->last_seen = now;
  slot->rssi = static_cast<int8_t>(device.get_rssi());
}

void SSCSGarage::update_scanning_() {
  auto *tracker = espbt::global_esp32_ble_tracker;
  if (tracker == nullptr)
    return;
  const bool wanted = this->always_scan_ || this->searching_() || this->phase_ == Phase::CONNECTING;
  tracker->set_scan_continuous(wanted);
  if (!esp32_ble::global_ble->is_active())
    return;  // the tracker starts (or skips) its first scan itself
  if (wanted) {
    // Never start a scan while our own link is being set up; the tracker restarts it afterwards.
    if (this->state() == espbt::ClientState::IDLE)
      tracker->start_scan_if_idle();
  } else if (tracker->scan_running()) {
    ESP_LOGD(TAG, "Shutter selected and idle, stopping BLE scan");
    tracker->stop_scan();
  }
}

void SSCSGarage::set_always_scan(bool always_scan) {
  if (always_scan == this->always_scan_)
    return;
  this->always_scan_ = always_scan;
  ESP_LOGI(TAG, "Scanning %s", always_scan ? "all the time" : "only when needed");
  this->publish_nearby_();
  this->update_scanning_();
}

void SSCSGarage::maintain_candidates_() {
  // Also catches a scan that was still starting when it was last told to stop, or one
  // restarted after an aborted OTA.
  this->update_scanning_();
  if (!this->tracking_())
    return;

  const uint32_t now = millis();
  for (uint8_t i = 0; i < this->candidate_count_;) {
    if (now - this->candidates_[i].last_seen > CANDIDATE_EXPIRY_MS) {
      this->candidates_[i] = this->candidates_[--this->candidate_count_];
      this->candidates_changed_ = true;
    } else {
      i++;
    }
  }

  // With nothing chosen, take the only shutter heard during the search window.
  if (this->searching_() && !this->is_busy() && this->candidate_count_ == 1 &&
      now - this->search_started_ >= this->auto_select_window_) {
    ESP_LOGI(TAG, "Only one shutter nearby, selecting it");
    this->select_device_(this->candidates_[0].address, true);
  }

  if (this->candidates_changed_ || now - this->last_nearby_publish_ >= NEARBY_REFRESH_MS)
    this->publish_nearby_();
}

void SSCSGarage::publish_device_() {
#ifdef USE_TEXT
  if (this->device_text_ == nullptr)
    return;
  if (!this->has_device_()) {
    this->device_text_->publish_state("auto");
    return;
  }
  char addr[18];
  format_address(this->device_address_, addr);
  this->device_text_->publish_state(addr);
#endif
}

void SSCSGarage::publish_nearby_() {
  this->candidates_changed_ = false;
  this->last_nearby_publish_ = millis();
#ifdef USE_TEXT_SENSOR
  if (this->nearby_sensor_ == nullptr)
    return;
  if (this->candidate_count_ == 0) {
    this->nearby_sensor_->publish_state(this->tracking_() ? "Searching" : "Not searching");
    return;
  }
  char buf[MAX_CANDIDATES * 56];
  size_t n = 0;
  for (uint8_t i = 0; i < this->candidate_count_ && n < sizeof(buf); i++) {
    const auto &c = this->candidates_[i];
    char addr[18];
    format_address(c.address, addr);
    const bool selected = this->has_device_() && c.address == this->device_address_;
    n += snprintf(buf + n, sizeof(buf) - n, "%s%s %s %d dBm%s", i ? "; " : "", addr, c.name, c.rssi,
                  selected ? " (selected)" : "");
  }
  this->nearby_sensor_->publish_state(buf);
#endif
}

bool SSCSGarage::parse_device(const espbt::ESPBTDevice &device) {
  bool ours = false;
  if (this->matches_name_(device)) {
    if (this->tracking_())
      this->track_candidate_(device);
    ours = true;
  }
  // Only let the base class start a connection while a command is waiting for one.
  if (this->phase_ == Phase::CONNECTING && this->has_device_())
    return BLEClientBase::parse_device(device) || ours;
  return ours;
}

// ---------------------------------------------------------------- commands

void SSCSGarage::send(Command cmd) {
  switch (this->phase_) {
    case Phase::IDLE:
      this->current_ = cmd;
      this->start_session_();
      return;
    case Phase::CONNECTING:
    case Phase::WAIT_CHALLENGE:
    case Phase::WAIT_AUTH_WRITE:
    case Phase::AUTH_SETTLE:
      // Nothing written yet: the newest request wins.
      if (cmd != this->current_) {
        ESP_LOGI(TAG, "Replacing queued '%s' with '%s'", command_to_string(this->current_), command_to_string(cmd));
        this->current_ = cmd;
      }
      return;
    case Phase::WAIT_COMMAND_WRITE:
    case Phase::WAIT_ACK:
      ESP_LOGI(TAG, "'%s' will be sent after the current session", command_to_string(cmd));
      this->pending_ = cmd;
      return;
  }
}

void SSCSGarage::start_session_() {
  if (this->key_source_ == KeySource::KEY_SOURCE_NONE) {
    this->finish_(Result::NO_KEY);
    return;
  }
  if (!this->has_device_()) {
    if (this->candidate_count_ != 1) {
      this->finish_(Result::NO_DEVICE);
      return;
    }
    this->select_device_(this->candidates_[0].address, true);
  }
  ESP_LOGI(TAG, "Sending '%s'", command_to_string(this->current_));
  this->cmd_handle_ = 0;
  this->data_handle_ = 0;
  this->status_seen_ = false;
  this->notify_regs_pending_ = 0;
  this->publish_status_("Connecting");
  // The link opens on the next advertisement (parse_device), once any previous one has closed.
  this->enter_(Phase::CONNECTING, this->connect_timeout_);
}

void SSCSGarage::finish_(Result result) {
  const Command cmd = this->current_;
  if (result == Result::ACKED) {
    ESP_LOGI(TAG, "'%s' %s", command_to_string(cmd), result_to_string(result));
  } else {
    ESP_LOGW(TAG, "'%s' %s", command_to_string(cmd), result_to_string(result));
  }
  this->cancel_timeout(PHASE_TIMEOUT);
  this->cancel_timeout(NUDGE_TIMEOUT);
  const bool was_connecting = this->phase_ == Phase::CONNECTING;
  this->phase_ = Phase::IDLE;
  this->end_link_();
  if (was_connecting)
    this->update_scanning_();

  char buf[64];
  snprintf(buf, sizeof(buf), "%s: %s", command_to_string(cmd), result_to_string(result));
  this->publish_status_(buf);
  this->result_callbacks_.call(cmd, result);

  if (this->pending_.has_value() && this->phase_ == Phase::IDLE) {
    this->current_ = *this->pending_;
    this->pending_.reset();
    this->start_session_();
  }
}

void SSCSGarage::end_link_() {
  const auto st = this->state();
  if (st != espbt::ClientState::IDLE && st != espbt::ClientState::DISCONNECTING && st != espbt::ClientState::INIT)
    this->disconnect();
}

void SSCSGarage::enter_(Phase phase, uint32_t timeout_ms) {
  const bool was_connecting = this->phase_ == Phase::CONNECTING;
  this->phase_ = phase;
  if (was_connecting != (phase == Phase::CONNECTING))
    this->update_scanning_();
  // Same name replaces the previous phase's timer.
  this->set_timeout(PHASE_TIMEOUT, timeout_ms, [this]() { this->on_phase_timeout_(); });
}

void SSCSGarage::on_phase_timeout_() {
  switch (this->phase_) {
    case Phase::CONNECTING:
      this->finish_(Result::NOT_FOUND);
      break;
    case Phase::WAIT_CHALLENGE:
      this->finish_(Result::NO_CHALLENGE);
      break;
    case Phase::AUTH_SETTLE:
      // No cb00 seen; the working firmware just waited, so carry on.
      this->write_command_();
      break;
    case Phase::WAIT_AUTH_WRITE:
    case Phase::WAIT_COMMAND_WRITE:
      this->finish_(Result::REJECTED);
      break;
    case Phase::WAIT_ACK:
      this->finish_(Result::SENT_NO_ACK);
      break;
    case Phase::IDLE:
      break;
  }
}

// ---------------------------------------------------------------- GATT

bool SSCSGarage::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                                     esp_ble_gattc_cb_param_t *param) {
  if (!BLEClientBase::gattc_event_handler(event, gattc_if, param))
    return false;

  switch (event) {
    case ESP_GATTC_SEARCH_CMPL_EVT:
      if (this->phase_ == Phase::CONNECTING) {
        this->on_services_discovered_();
      } else {
        this->end_link_();  // connected without a command waiting
      }
      break;

    case ESP_GATTC_REG_FOR_NOTIFY_EVT:
      if (param->reg_for_notify.handle == this->cmd_handle_ || param->reg_for_notify.handle == this->data_handle_) {
        if (this->notify_regs_pending_ > 0)
          this->notify_regs_pending_--;
      }
      break;

    case ESP_GATTC_NOTIFY_EVT:
      if (param->notify.handle == this->cmd_handle_)
        this->on_cmd_notify_(param->notify.value, param->notify.value_len);
      break;

    case ESP_GATTC_WRITE_CHAR_EVT:
      if (param->write.handle == this->cmd_handle_)
        this->on_cmd_write_(param->write.status);
      break;

    case ESP_GATTC_DISCONNECT_EVT:
      this->cmd_handle_ = 0;
      this->data_handle_ = 0;
      switch (this->phase_) {
        case Phase::IDLE:
        case Phase::CONNECTING:  // retried on the next advertisement
          break;
        case Phase::WAIT_ACK:
          this->finish_(Result::SENT_NO_ACK);
          break;
        default:
          this->finish_(Result::DISCONNECTED);
          break;
      }
      break;

    default:
      break;
  }
  return true;
}

void SSCSGarage::on_services_discovered_() {
  auto *cmd = this->get_characteristic(espbt::ESPBTUUID::from_uint16(protocol::SERVICE_UUID16),
                                       espbt::ESPBTUUID::from_uint16(protocol::CMD_UUID16));
  auto *data = this->get_characteristic(espbt::ESPBTUUID::from_uint16(protocol::SERVICE_UUID16),
                                        espbt::ESPBTUUID::from_uint16(protocol::DATA_UUID16));
  if (cmd == nullptr || data == nullptr) {
    this->finish_(Result::NO_SERVICE);
    return;
  }
  this->cmd_handle_ = cmd->handle;
  this->data_handle_ = data->handle;

  // Same order as the app: DATA first, then CMD. The base class writes each CCCD
  // when the registration completes; the challenge follows on CMD.
  this->notify_regs_pending_ = 0;
  if (this->register_for_notify(this->data_handle_) == ESP_OK)
    this->notify_regs_pending_++;
  if (this->register_for_notify(this->cmd_handle_) == ESP_OK) {
    this->notify_regs_pending_++;
  } else {
    ESP_LOGW(TAG, "Could not subscribe to CMD");
  }

  this->publish_status_("Authenticating");
  this->enter_(Phase::WAIT_CHALLENGE, this->challenge_timeout_);
  if (this->challenge_nudge_ > 0 && this->challenge_nudge_ < this->challenge_timeout_) {
    this->set_timeout(NUDGE_TIMEOUT, this->challenge_nudge_, [this]() {
      if (this->phase_ != Phase::WAIT_CHALLENGE || this->notify_regs_pending_ != 0)
        return;
      // Some sessions only produce the challenge after an INI write of 00 00.
      ESP_LOGD(TAG, "No challenge yet, sending 00 00");
      static const uint8_t INI[2] = {0x00, 0x00};
      this->write_cmd_char_(INI, sizeof(INI));
    });
  }
}

void SSCSGarage::on_cmd_notify_(const uint8_t *data, uint16_t len) {
  if (this->phase_ == Phase::WAIT_CHALLENGE && len == protocol::CHALLENGE_PACKET_LEN) {
    ESP_LOGV(TAG, "Challenge received (flags 0x%08" PRIx32 ")", protocol::challenge_flags(data));
    this->cancel_timeout(NUDGE_TIMEOUT);
    this->write_token_(data);
    return;
  }
  if (protocol::is_status(data, len)) {
    ESP_LOGD(TAG, "Status notification");
    this->status_seen_ = true;
    if (this->phase_ == Phase::AUTH_SETTLE)
      this->write_command_();
    return;
  }
  if (protocol::is_ack(data, len)) {
    if (this->phase_ == Phase::WAIT_ACK || this->phase_ == Phase::WAIT_COMMAND_WRITE)
      this->finish_(Result::ACKED);
    return;
  }
  ESP_LOGV(TAG, "Unhandled CMD notification (%u bytes)", len);
}

void SSCSGarage::on_cmd_write_(esp_gatt_status_t status) {
  switch (this->phase_) {
    case Phase::WAIT_AUTH_WRITE:
      if (status != ESP_GATT_OK) {
        ESP_LOGW(TAG, "Token write failed (status %d)", status);
        this->finish_(Result::REJECTED);
      } else if (this->status_seen_) {
        this->write_command_();
      } else {
        this->enter_(Phase::AUTH_SETTLE, this->auth_settle_);
      }
      break;
    case Phase::WAIT_COMMAND_WRITE:
      if (status != ESP_GATT_OK) {
        ESP_LOGW(TAG, "Command write failed (status %d)", status);
        this->finish_(Result::REJECTED);
      } else {
        this->enter_(Phase::WAIT_ACK, this->ack_timeout_);
      }
      break;
    default:
      // Response to the 00 00 nudge; nothing to do.
      break;
  }
}

void SSCSGarage::write_token_(const uint8_t *packet20) {
  this->token_ = protocol::make_token(this->key_, packet20, this->passcode_);
  this->enter_(Phase::WAIT_AUTH_WRITE, this->ack_timeout_);
  if (!this->write_cmd_char_(this->token_.data(), this->token_.size()))
    this->finish_(Result::REJECTED);
}

void SSCSGarage::write_command_() {
  this->command_buf_ = protocol::command_bytes(this->current_);
  this->publish_status_("Sending");
  this->enter_(Phase::WAIT_COMMAND_WRITE, this->ack_timeout_);
  if (!this->write_cmd_char_(this->command_buf_.data(), this->command_buf_.size()))
    this->finish_(Result::REJECTED);
}

bool SSCSGarage::write_cmd_char_(const uint8_t *data, uint16_t len) {
  if (this->cmd_handle_ == 0)
    return false;
  // Bluedroid copies the value before returning, so const_cast is safe.
  auto err = esp_ble_gattc_write_char(this->get_gattc_if(), this->get_conn_id(), this->cmd_handle_, len,
                                      const_cast<uint8_t *>(data), ESP_GATT_WRITE_TYPE_RSP, ESP_GATT_AUTH_REQ_NONE);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "esp_ble_gattc_write_char failed: %d", err);
    return false;
  }
  return true;
}

void SSCSGarage::publish_status_(const char *text) {
#ifdef USE_TEXT_SENSOR
  if (this->status_sensor_ != nullptr)
    this->status_sensor_->publish_state(text);
#endif
}

// ---------------------------------------------------------------- HA entities

#ifdef USE_TEXT
void KeyText::setup() { this->publish_state(""); }

void KeyText::control(const std::string &value) {
  this->parent_->set_key_from_text(value);
  // Never echo the key back to Home Assistant.
  this->publish_state("");
}

void DeviceText::control(const std::string &value) {
  // The hub publishes the resulting selection (or the unchanged one on error).
  this->parent_->set_device_from_text(value);
}
#endif

#ifdef USE_BUTTON
void FindButton::press_action() { this->parent_->set_device_from_text("auto"); }
#endif

#ifdef USE_SWITCH
void ScanSwitch::setup() {
  const bool state = this->get_initial_state_with_restore_mode().value_or(false);
  this->write_state(state);
}

void ScanSwitch::write_state(bool state) {
  this->parent_->set_always_scan(state);
  this->publish_state(state);
}
#endif

}  // namespace esphome::sscs_garage

#endif  // USE_ESP32
