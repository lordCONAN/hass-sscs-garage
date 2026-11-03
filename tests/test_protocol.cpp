// Host test: the firmware's token code must match the verified Python reference.
#include <cstdio>
#include <cstring>

#include "../components/sscs_garage/sscs_protocol.h"
#include "vectors.inc"

using namespace esphome::sscs_garage::protocol;

static int failures = 0;

static void check(bool ok, const char *what, int idx) {
  if (!ok) {
    std::printf("FAIL %s (case %d)\n", what, idx);
    failures++;
  }
}

int main() {
  int idx = 0;
  for (const auto &v : VECTORS) {
    Key key;
    std::memcpy(key.data(), v.key, 32);
    Token token = make_token(key, v.packet, v.passcode);
    check(std::memcmp(token.data(), v.token, 20) == 0, "token", idx);
    idx++;
  }

  const uint8_t ack[] = {0xCC, 0x00};
  const uint8_t not_ack[] = {0xCC, 0x01};
  const uint8_t status[] = {0xCB, 0x00, 0x01, 0x02};
  check(is_ack(ack, 2), "is_ack", 0);
  check(!is_ack(not_ack, 2), "is_ack negative", 0);
  check(!is_ack(status, 4), "is_ack length", 0);
  check(is_status(status, 4), "is_status", 0);

  const auto up = command_bytes(Command::UP);
  const auto stop = command_bytes(Command::STOP);
  const auto down = command_bytes(Command::DOWN);
  check(up == std::array<uint8_t, 4>{0xC9, 0, 0, 0}, "cmd up", 0);
  check(stop == std::array<uint8_t, 4>{0xC9, 0, 0, 1}, "cmd stop", 0);
  check(down == std::array<uint8_t, 4>{0xC9, 0, 0, 2}, "cmd down", 0);

  const uint8_t first[20] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x12, 0x10, 0x09, 0x00};
  check(challenge_flags(first) == 0x00091012u, "flags", 0);

  std::printf("%d vectors, %s\n", idx, failures == 0 ? "all passed" : "FAILURES");
  return failures == 0 ? 0 : 1;
}
