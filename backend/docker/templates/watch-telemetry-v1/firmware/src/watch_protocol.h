#ifndef KLEOS_WATCH_PROTOCOL_H
#define KLEOS_WATCH_PROTOCOL_H
#include <stddef.h>
#include <stdint.h>
struct kleos_telemetry {
    uint8_t battery_percent;
    uint16_t heart_rate_bpm;
    uint32_t steps;
};
int kleos_telemetry_decode(const uint8_t *data, size_t len, struct kleos_telemetry *out);
#endif
