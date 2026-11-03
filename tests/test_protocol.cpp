// Host test: the firmware's token code must match the verified Python reference.
#include <cstdio>
#include <cstring>
#include <string>

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

  // Key input: QR text and hex forms.
  int qr_idx = 0;
  for (const auto &v : QR_VECTORS) {
    Key key{};
    const auto r = parse_key(v.text, std::strlen(v.text), key);
    check(r == KeyParseResult::KEY_PARSE_RESULT_OK, "qr parse", qr_idx);
    check(std::memcmp(key.data(), v.key, 32) == 0, "qr key", qr_idx);

    char hex[3 * 32 + 8];
    size_t n = 0;
    n += std::snprintf(hex + n, sizeof(hex) - n, "  ");
    for (int i = 0; i < 32; i++)
      n += std::snprintf(hex + n, sizeof(hex) - n, i % 4 == 3 ? "%02X:" : "%02x", v.key[i]);
    Key key2{};
    check(parse_key(hex, n, key2) == KeyParseResult::KEY_PARSE_RESULT_OK, "hex parse", qr_idx);
    check(std::memcmp(key2.data(), v.key, 32) == 0, "hex key", qr_idx);
    qr_idx++;
  }
  {
    Key k{};
    std::string r2 = std::string("R2") + (QR_VECTORS[0].text + 2);
    check(parse_key(r2.c_str(), r2.size(), k) == KeyParseResult::KEY_PARSE_RESULT_UNSUPPORTED_SLOT, "R2", 0);
    std::string share = std::string("0") + QR_VECTORS[0].text;
    check(parse_key(share.c_str(), share.size(), k) == KeyParseResult::KEY_PARSE_RESULT_SHARE_CODE, "share", 0);
    std::string bad = std::string(QR_VECTORS[0].text) + "0";  // '0' is not Base58
    check(parse_key(bad.c_str(), bad.size(), k) == KeyParseResult::KEY_PARSE_RESULT_BAD_FORMAT, "bad b58", 0);
    std::string extra = std::string(QR_VECTORS[0].text) + "z";  // too long for 32 bytes
    check(parse_key(extra.c_str(), extra.size(), k) == KeyParseResult::KEY_PARSE_RESULT_BAD_LENGTH, "len", 0);
    check(parse_key("   ", 3, k) == KeyParseResult::KEY_PARSE_RESULT_EMPTY, "empty", 0);
    check(parse_key("abc", 3, k) == KeyParseResult::KEY_PARSE_RESULT_BAD_FORMAT, "short", 0);
    char fp[9];
    key_fingerprint(k, fp);
    check(std::strlen(fp) == 8, "fingerprint", 0);
  }

  std::printf("%d token vectors, %d QR vectors, %s\n", idx, qr_idx, failures == 0 ? "all passed" : "FAILURES");
  return failures == 0 ? 0 : 1;
}
