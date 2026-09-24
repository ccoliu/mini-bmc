/* Simulated sensor table, thresholds and fault injection. No I/O. */
#ifndef SENSORD_SENSORS_H
#define SENSORD_SENSORS_H

#include <stddef.h>
#include <stdint.h>

enum sensor_kind { SENSOR_TEMP, SENSOR_FAN, SENSOR_POWER };

enum fault_mode {
    FAULT_NONE,
    FAULT_OVERTEMP,
    FAULT_STUCK,
    FAULT_DISCONNECTED,
    FAULT_NOISE,
};

enum sensor_status {
    STATUS_OK,
    STATUS_WARNING,
    STATUS_CRITICAL,
    STATUS_UNAVAILABLE,
};

struct sensor {
    const char *name;
    enum sensor_kind kind;
    const char *unit;
    double nominal;
    double jitter;     /* max per-step random deviation */
    double lower_crit; /* -INFINITY if unused */
    double upper_warn; /* INFINITY if unused */
    double upper_crit; /* INFINITY if unused */
    double value;
    enum fault_mode fault;
};

#define SENSOR_COUNT 6

struct sensor_set {
    struct sensor s[SENSOR_COUNT];
    uint64_t rng;
};

void sensors_init(struct sensor_set *set, uint64_t seed);

/* Advances the simulation by one step. */
void sensors_tick(struct sensor_set *set);

struct sensor *sensors_find(struct sensor_set *set, const char *name);

enum sensor_status sensor_status(const struct sensor *s);
const char *sensor_status_name(enum sensor_status st);

/* Returns 0 and sets *out on success, -1 for an unknown mode name. */
int fault_mode_parse(const char *name, enum fault_mode *out);

/* Applies a fault immediately. Returns 0, or -1 if the mode does not make sense
 * for this sensor (overtemp on a non-temperature sensor). */
int sensor_inject_fault(struct sensor_set *set, struct sensor *s, enum fault_mode mode);

/* Clears all faults and returns every sensor to its nominal value. */
void sensors_clear_faults(struct sensor_set *set);

#endif
