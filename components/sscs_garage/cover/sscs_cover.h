#pragma once

#ifdef USE_ESP32

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/cover/cover.h"
#include "esphome/components/select/select.h"
#include "esphome/core/component.h"
#include "esphome/core/preferences.h"

#include <vector>

#include "../sscs_garage.h"

namespace esphome::sscs_garage {

class StateSourceSelect;

/**
 * Open/close/stop cover for the shutter.
 *
 * Position comes from one "state source" chosen at runtime:
 *   index 0           -> assumed from the last delivered command
 *   index 1..N        -> a binary sensor (Zigbee contact imported from HA,
 *                        a future BLE/camera sensor, a local reed switch...)
 *
 * Only open/closed is known, so the cover always reports assumed_state: HA
 * keeps both buttons available (a bottom contact cannot tell "fully open"
 * from "partly open").
 */
class SSCSCover : public cover::Cover, public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  cover::CoverTraits get_traits() override;

  void set_hub(SSCSGarage *hub) { this->hub_ = hub; }
  void set_travel_time(uint32_t ms) { this->travel_time_ = ms; }
  void add_state_source(binary_sensor::BinarySensor *sensor, bool open_when_on) {
    this->sources_.push_back({sensor, open_when_on});
  }
  void set_default_source(uint8_t index) { this->active_source_ = index; }
  void set_source_select(StateSourceSelect *select) { this->source_select_ = select; }

  /// 0 = assumed; 1..N = state_sources[index - 1]
  void select_source(uint8_t index);
  uint8_t active_source() const { return this->active_source_; }
  size_t source_count() const { return this->sources_.size() + 1; }

 protected:
  struct StateSource {
    binary_sensor::BinarySensor *sensor;
    bool open_when_on;
  };

  void control(const cover::CoverCall &call) override;
  void start_motion_(Command cmd);
  void on_sensor_(uint8_t index);
  void on_result_(Command cmd, Result result);
  void apply_source_state_();
  bool using_assumed_() const { return this->active_source_ == 0; }

  SSCSGarage *hub_{nullptr};
  std::vector<StateSource> sources_;
  StateSourceSelect *source_select_{nullptr};
  uint8_t active_source_{0};

  uint32_t travel_time_{30000};
  uint32_t motion_started_{0};
  float position_before_{cover::COVER_CLOSED};
};

/// Select entity that switches the cover's state source; remembers the choice across reboots.
class StateSourceSelect : public select::Select, public Component {
 public:
  void setup() override;
  void dump_config() override;
  // After the cover, so the restored choice is applied last.
  float get_setup_priority() const override { return setup_priority::DATA - 1.0f; }

  void set_cover(SSCSCover *cover) { this->cover_ = cover; }
  void set_restore_value(bool restore) { this->restore_value_ = restore; }

 protected:
  void control(size_t index) override;

  SSCSCover *cover_{nullptr};
  bool restore_value_{true};
  ESPPreferenceObject pref_;
};

}  // namespace esphome::sscs_garage

#endif  // USE_ESP32
