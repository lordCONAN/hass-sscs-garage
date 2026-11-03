#pragma once

#ifdef USE_ESP32

#include "esphome/core/automation.h"
#include "esphome/core/helpers.h"
#include "sscs_garage.h"

namespace esphome::sscs_garage {

/// sscs_garage.send: { command: up | stop | down }
template<typename... Ts> class SendAction : public Action<Ts...>, public Parented<SSCSGarage> {
 public:
  void set_command(Command cmd) { this->command_ = cmd; }
  void play(const Ts &...x) override { this->parent_->send(this->command_); }

 protected:
  Command command_{Command::STOP};
};

}  // namespace esphome::sscs_garage

#endif  // USE_ESP32
