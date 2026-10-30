#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include "watch_protocol.h"
int main(void) {
    const uint8_t frame[] = {1, 85, 72, 0, 232, 3, 0, 0};
    struct kleos_telemetry value;
    if (kleos_telemetry_decode(frame, sizeof(frame), &value)) {
        printk("Kleos demo: battery=%u bpm=%u steps=%u\n",
               value.battery_percent, value.heart_rate_bpm, value.steps);
    }
    return 0;
}
