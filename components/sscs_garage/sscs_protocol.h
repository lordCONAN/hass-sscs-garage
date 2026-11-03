#pragma once

// Pure protocol logic for the Sysdes "SSCs" Smart Garage BLE controller.
// No ESPHome or BLE-stack dependencies, so it can be unit-tested on a PC.
// See docs/protocol.md for how each piece was established.

#include <array>
#include <cstddef>
#include <cstdint>

namespace esphome::sscs_garage::protocol {

// GATT layout (16-bit UUIDs on the Bluetooth base UUID).
static constexpr uint16_t SERVICE_UUID16 = 0x3333;
static constexpr uint16_t CMD_UUID16 = 0x9001;   // challenge, status, ACK and all writes
static constexpr uint16_t DATA_UUID16 = 0x9002;  // subscribed like the app does; no traffic used

static constexpr size_t KEY_LEN = 32;
static constexpr size_t CHALLENGE_PACKET_LEN = 20;  // 16-byte challenge + 4-byte LE flags
static constexpr size_t TOKEN_LEN = 20;
static constexpr size_t COMMAND_LEN = 4;

// Passcode value meaning "no passcode" (the only mode verified on hardware).
static constexpr int64_t NO_PASSCODE = -1;

using Key = std::array<uint8_t, KEY_LEN>;
using Token = std::array<uint8_t, TOKEN_LEN>;

enum class Command : uint8_t { UP = 0x00, STOP = 0x01, DOWN = 0x02 };

const char *command_to_string(Command cmd);

// c9 00 00 <cmd>
std::array<uint8_t, COMMAND_LEN> command_bytes(Command cmd);

// cc 00: the controller received the command (not proof the shutter moved).
bool is_ack(const uint8_t *data, size_t len);

// cb 00 ...: status notification the controller sends once authenticated.
bool is_status(const uint8_t *data, size_t len);

// Little-endian flags in bytes 16..19 of the challenge packet.
uint32_t challenge_flags(const uint8_t *packet20);

// token[0..15]  = upper16(SHA256(key || challenge)) XOR lower16(...)
// token[16..19] = passcode tail (FF FF FF FF for NO_PASSCODE)
// Only the NO_PASSCODE path has been verified on hardware.
Token make_token(const Key &key, const uint8_t *packet20, int64_t passcode);

// SHA-256 of a || b. Implemented with mbedTLS on the device, OpenSSL in host tests.
void sha256_concat(const uint8_t *a, size_t alen, const uint8_t *b, size_t blen, uint8_t out[32]);

}  // namespace esphome::sscs_garage::protocol
