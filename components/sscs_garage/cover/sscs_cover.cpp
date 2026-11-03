#include "sscs_cover.h"

#ifdef USE_ESP32

#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::sscs_garage {

static const char *const TAG = "sscs_garage.cover";

using cover::COVER_CLOSED;
using cover::COVER_OPEN;
using cover::COVER_OPERATION_CLOSING;
using cover::COVER_OPERATION_IDLE;
using cover::COVER_OPERATION_OPENING;

// ---------------------------------------------------------------- cover

void SSCSCover::setup() {
  auto restore = this->restore_state_();
  if (restore.has_value()) {
    restore->apply(this);
  } else {
    this->position = COVER_CLOSED;
  }

  this->hub_->add_on_result_callback([this](Command cmd, Result result) { this->on_result_(cmd, result); });

  for (size_t i = 0; i < this->sources_.size(); i++) {
    const auto index = static_cast<uint8_t>(i + 1);
    this->sources_[i].sensor->add_on_state_callback([this, index](bool) { this->on_sensor_(index); });
  }

  if (this->active_source_ >= this->source_count())
    this->active_source_ = 0;
  this->apply_source_state_();
  this->publish_state(false);
}

void SSCSCover::dump_config() {
  LOG_COVER("", "SSCs Garage Cover", this);
  ESP_LOGCONFIG(TAG, "  Travel time: %" PRIu32 " ms", this->travel_time_);
  ESP_LOGCONFIG(TAG, "  State source %u: assumed from last command%s", 0, this->active_source_ == 0 ? " (active)" : "");
  for (size_t i = 0; i < this->sources_.size(); i++) {
    ESP_LOGCONFIG(TAG, "  State source %u: '%s', open when %s%s", static_cast<unsigned>(i + 1),
                  this->sources_[i].sensor->get_name().c_str(), this->sources_[i].open_when_on ? "ON" : "OFF",
                  this->active_source_ == i + 1 ? " (active)" : "");
  }
}

cover::CoverTraits SSCSCover::get_traits() {
  auto traits = cover::CoverTraits();
  traits.set_supports_stop(true);
  traits.set_supports_position(false);
  traits.set_supports_tilt(false);
  traits.set_supports_toggle(false);
  traits.set_is_assumed_state(true);
  return traits;
}

void SSCSCover::control(const cover::CoverCall &call) {
  if (call.get_stop()) {
    this->hub_->send(Command::STOP);
    this->current_operation = COVER_OPERATION_IDLE;
    this->publish_state();
    return;
  }
  const auto &target = call.get_position();
  if (!target.has_value())
    return;
  this->start_motion_(*target >= 0.5f ? Command::UP : Command::DOWN);
}

void SSCSCover::start_motion_(Command cmd) {
  this->hub_->send(cmd);
  this->position_before_ = this->position;
  this->motion_started_ = App.get_loop_component_start_time();
  this->current_operation = cmd == Command::UP ? COVER_OPERATION_OPENING : COVER_OPERATION_CLOSING;
  if (this->using_assumed_())
    this->position = cmd == Command::UP ? COVER_OPEN : COVER_CLOSED;
  this->publish_state();
}

void SSCSCover::loop() {
  if (this->current_operation == COVER_OPERATION_IDLE)
    return;
  if (App.get_loop_component_start_time() - this->motion_started_ >= this->travel_time_) {
    this->current_operation = COVER_OPERATION_IDLE;
    this->publish_state();
  }
}

void SSCSCover::on_result_(Command cmd, Result result) {
  if (result_delivered(result))
    return;
  const bool was_moving = (cmd == Command::UP && this->current_operation == COVER_OPERATION_OPENING) ||
                          (cmd == Command::DOWN && this->current_operation == COVER_OPERATION_CLOSING);
  if (!was_moving)
    return;
  // The command never reached the shutter: undo the optimistic change.
  this->current_operation = COVER_OPERATION_IDLE;
  if (this->using_assumed_())
    this->position = this->position_before_;
  this->publish_state();
}

// ---------------------------------------------------------------- state sources

void SSCSCover::select_source(uint8_t index) {
  if (index >= this->source_count()) {
    ESP_LOGW(TAG, "Invalid state source %u", index);
    return;
  }
  this->active_source_ = index;
  ESP_LOGI(TAG, "State source: %s",
           index == 0 ? "assumed" : this->sources_[index - 1].sensor->get_name().c_str());
  this->apply_source_state_();
  this->publish_state();
}

void SSCSCover::on_sensor_(uint8_t index) {
  if (index != this->active_source_)
    return;
  this->apply_source_state_();
  this->publish_state();
}

void SSCSCover::apply_source_state_() {
  if (this->using_assumed_())
    return;
  const auto &src = this->sources_[this->active_source_ - 1];
  if (!src.sensor->has_state())
    return;
  const bool open = src.sensor->state == src.open_when_on;
  this->position = open ? COVER_OPEN : COVER_CLOSED;
  // Reaching "closed" ends a close; "open" only means it left the closed end.
  if (!open && this->current_operation == COVER_OPERATION_CLOSING)
    this->current_operation = COVER_OPERATION_IDLE;
}

// ---------------------------------------------------------------- select

void StateSourceSelect::setup() {
  size_t index = this->cover_->active_source();
  if (this->restore_value_) {
    this->pref_ = this->make_entity_preference<size_t>();
    size_t stored;
    if (this->pref_.load(&stored) && this->has_index(stored)) {
      index = stored;
      this->cover_->select_source(static_cast<uint8_t>(index));
    }
  }
  this->publish_state(index);
}

void StateSourceSelect::dump_config() { LOG_SELECT("", "SSCs Garage State Source", this); }

void StateSourceSelect::control(size_t index) {
  this->cover_->select_source(static_cast<uint8_t>(index));
  if (this->restore_value_)
    this->pref_.save(&index);
  this->publish_state(index);
}

}  // namespace esphome::sscs_garage

#endif  // USE_ESP32
