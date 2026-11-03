#include "sscs_garage.h"

#ifdef USE_ESP32

#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include <cinttypes>
#include <cstdio>

namespace esphome::sscs_garage {

static const char *const TAG = "sscs_garage";

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
  }
  return "unknown";
}

void SSCSGarage::setup() {
  // ble_client connects on the next advertisement whenever it is enabled.
  // Keep it disabled between commands so the shutter stays free for the phone app.
  this->parent()->set_auto_connect(true);
  this->parent()->set_enabled(false);
  this->publish_status_("Idle");
}

void SSCSGarage::dump_config() {
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
  ESP_LOGI(TAG, "Sending '%s'", command_to_string(this->current_));
  this->cmd_handle_ = 0;
  this->data_handle_ = 0;
  this->nudged_ = false;
  this->status_seen_ = false;
  this->notify_regs_pending_ = 0;
  this->publish_status_("Connecting");
  this->enter_(Phase::CONNECTING, this->connect_timeout_);
  // If the previous session is still disconnecting, ble_client waits for IDLE before reconnecting.
  this->parent()->set_enabled(true);
}

void SSCSGarage::finish_(Result result) {
  const Command cmd = this->current_;
  if (result == Result::ACKED) {
    ESP_LOGI(TAG, "'%s' %s", command_to_string(cmd), result_to_string(result));
  } else {
    ESP_LOGW(TAG, "'%s' %s", command_to_string(cmd), result_to_string(result));
  }
  this->phase_ = Phase::IDLE;
  this->parent()->set_enabled(false);  // disconnects if connected

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

void SSCSGarage::enter_(Phase phase, uint32_t timeout_ms) {
  this->phase_ = phase;
  this->phase_started_ = App.get_loop_component_start_time();
  this->phase_timeout_ = timeout_ms;
}

// ---------------------------------------------------------------- timeouts

void SSCSGarage::loop() {
  // Runs from both Component::loop and BLEClient's node loop; everything here is idempotent.
  if (this->phase_ == Phase::IDLE) {
    if (this->parent()->enabled) {
      // Should not happen, but never leave the link held open with nothing to do.
      this->parent()->set_enabled(false);
    }
    return;
  }
  const uint32_t elapsed = App.get_loop_component_start_time() - this->phase_started_;

  if (this->phase_ == Phase::WAIT_CHALLENGE && !this->nudged_ && this->challenge_nudge_ > 0 &&
      elapsed >= this->challenge_nudge_ && this->notify_regs_pending_ == 0) {
    // Some sessions only produce the challenge after an INI write of 00 00.
    ESP_LOGD(TAG, "No challenge yet, sending 00 00");
    this->nudged_ = true;
    static const uint8_t INI[2] = {0x00, 0x00};
    this->write_cmd_char_(INI, sizeof(INI));
  }

  if (elapsed < this->phase_timeout_)
    return;

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

void SSCSGarage::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t /*gattc_if*/,
                                     esp_ble_gattc_cb_param_t *param) {
  switch (event) {
    case ESP_GATTC_SEARCH_CMPL_EVT:
      if (this->phase_ == Phase::CONNECTING) {
        this->on_services_discovered_();
      } else {
        // Connected without a command in flight (e.g. a boot-time race): let go.
        this->node_state = espbt::ClientState::ESTABLISHED;
        this->parent()->set_enabled(false);
      }
      break;

    case ESP_GATTC_REG_FOR_NOTIFY_EVT:
      if (param->reg_for_notify.handle != this->cmd_handle_ && param->reg_for_notify.handle != this->data_handle_)
        break;
      if (this->notify_regs_pending_ > 0 && --this->notify_regs_pending_ == 0) {
        // Handles are cached; ble_client may now free its copy of the GATT table.
        this->node_state = espbt::ClientState::ESTABLISHED;
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
        case Phase::CONNECTING:  // ble_client retries on the next advertisement
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
}

void SSCSGarage::on_services_discovered_() {
  auto *cmd = this->parent()->get_characteristic(espbt::ESPBTUUID::from_uint16(protocol::SERVICE_UUID16),
                                                 espbt::ESPBTUUID::from_uint16(protocol::CMD_UUID16));
  auto *data = this->parent()->get_characteristic(espbt::ESPBTUUID::from_uint16(protocol::SERVICE_UUID16),
                                                  espbt::ESPBTUUID::from_uint16(protocol::DATA_UUID16));
  if (cmd == nullptr || data == nullptr) {
    this->node_state = espbt::ClientState::ESTABLISHED;
    this->finish_(Result::NO_SERVICE);
    return;
  }
  this->cmd_handle_ = cmd->handle;
  this->data_handle_ = data->handle;

  // Same order as the app: DATA first, then CMD. ble_client writes each CCCD
  // when the registration completes; the challenge follows on CMD.
  this->notify_regs_pending_ = 0;
  if (this->parent()->register_for_notify(this->data_handle_) == ESP_OK)
    this->notify_regs_pending_++;
  if (this->parent()->register_for_notify(this->cmd_handle_) == ESP_OK) {
    this->notify_regs_pending_++;
  } else {
    ESP_LOGW(TAG, "Could not subscribe to CMD");
  }
  if (this->notify_regs_pending_ == 0)
    this->node_state = espbt::ClientState::ESTABLISHED;

  this->publish_status_("Authenticating");
  this->enter_(Phase::WAIT_CHALLENGE, this->challenge_timeout_);
}

void SSCSGarage::on_cmd_notify_(const uint8_t *data, uint16_t len) {
  if (this->phase_ == Phase::WAIT_CHALLENGE && len == protocol::CHALLENGE_PACKET_LEN) {
    ESP_LOGV(TAG, "Challenge received (flags 0x%08" PRIx32 ")", protocol::challenge_flags(data));
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
  auto err = esp_ble_gattc_write_char(this->parent()->get_gattc_if(), this->parent()->get_conn_id(), this->cmd_handle_,
                                      len, const_cast<uint8_t *>(data), ESP_GATT_WRITE_TYPE_RSP, ESP_GATT_AUTH_REQ_NONE);
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

}  // namespace esphome::sscs_garage

#endif  // USE_ESP32
