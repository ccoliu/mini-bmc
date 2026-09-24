#include <math.h>

#include "sensors.h"
#include "unity.h"

static struct sensor_set set;

void setUp(void)
{
    sensors_init(&set, 42);
}

void tearDown(void) {}

static void test_all_sensors_start_nominal_and_ok(void)
{
    static const char *names[SENSOR_COUNT] = {
        "cpu_temp", "gpu_temp", "inlet_temp", "fan1_rpm", "fan2_rpm", "psu_watts",
    };

    for (size_t i = 0; i < SENSOR_COUNT; i++) {
        struct sensor *s = sensors_find(&set, names[i]);

        TEST_ASSERT_NOT_NULL_MESSAGE(s, names[i]);
        TEST_ASSERT_EQUAL_DOUBLE(s->nominal, s->value);
        TEST_ASSERT_EQUAL(STATUS_OK, sensor_status(s));
    }
    TEST_ASSERT_NULL(sensors_find(&set, "nope"));
    TEST_ASSERT_NULL(sensors_find(&set, ""));
}

static void test_healthy_values_stay_near_nominal(void)
{
    for (int t = 0; t < 10000; t++) {
        sensors_tick(&set);
        for (size_t i = 0; i < SENSOR_COUNT; i++) {
            const struct sensor *s = &set.s[i];

            /* Mean reversion bounds the walk to jitter / REVERSION = 5x jitter. */
            TEST_ASSERT_DOUBLE_WITHIN(5.0 * s->jitter, s->nominal, s->value);
            TEST_ASSERT_EQUAL(STATUS_OK, sensor_status(s));
        }
    }
}

static void test_simulation_is_reproducible_per_seed(void)
{
    struct sensor_set a, b, c;

    sensors_init(&a, 7);
    sensors_init(&b, 7);
    sensors_init(&c, 8);
    for (int t = 0; t < 100; t++) {
        sensors_tick(&a);
        sensors_tick(&b);
        sensors_tick(&c);
    }
    TEST_ASSERT_EQUAL_DOUBLE(a.s[0].value, b.s[0].value);
    TEST_ASSERT_TRUE(a.s[0].value != c.s[0].value);
}

static void test_thresholds(void)
{
    struct sensor *cpu = sensors_find(&set, "cpu_temp");
    struct sensor *fan = sensors_find(&set, "fan1_rpm");

    cpu->value = 84.9;
    TEST_ASSERT_EQUAL(STATUS_OK, sensor_status(cpu));
    cpu->value = 85.0;
    TEST_ASSERT_EQUAL(STATUS_WARNING, sensor_status(cpu));
    cpu->value = 95.0;
    TEST_ASSERT_EQUAL(STATUS_CRITICAL, sensor_status(cpu));

    fan->value = 1000.1;
    TEST_ASSERT_EQUAL(STATUS_OK, sensor_status(fan));
    fan->value = 1000.0;
    TEST_ASSERT_EQUAL(STATUS_CRITICAL, sensor_status(fan));
    fan->value = 1e9; /* fans have no upper limit */
    TEST_ASSERT_EQUAL(STATUS_OK, sensor_status(fan));
}

static void test_status_names(void)
{
    TEST_ASSERT_EQUAL_STRING("ok", sensor_status_name(STATUS_OK));
    TEST_ASSERT_EQUAL_STRING("warning", sensor_status_name(STATUS_WARNING));
    TEST_ASSERT_EQUAL_STRING("critical", sensor_status_name(STATUS_CRITICAL));
    TEST_ASSERT_EQUAL_STRING("unavailable", sensor_status_name(STATUS_UNAVAILABLE));
    TEST_ASSERT_EQUAL_STRING("unknown", sensor_status_name((enum sensor_status)99));
}

static void test_fault_mode_parse(void)
{
    enum fault_mode m;

    TEST_ASSERT_EQUAL_INT(0, fault_mode_parse("overtemp", &m));
    TEST_ASSERT_EQUAL(FAULT_OVERTEMP, m);
    TEST_ASSERT_EQUAL_INT(0, fault_mode_parse("stuck", &m));
    TEST_ASSERT_EQUAL(FAULT_STUCK, m);
    TEST_ASSERT_EQUAL_INT(0, fault_mode_parse("disconnected", &m));
    TEST_ASSERT_EQUAL(FAULT_DISCONNECTED, m);
    TEST_ASSERT_EQUAL_INT(0, fault_mode_parse("noise", &m));
    TEST_ASSERT_EQUAL(FAULT_NOISE, m);
    TEST_ASSERT_EQUAL_INT(-1, fault_mode_parse("OVERTEMP", &m));
    TEST_ASSERT_EQUAL_INT(-1, fault_mode_parse("", &m));
}

static void test_overtemp_is_critical_immediately_and_persists(void)
{
    struct sensor *cpu = sensors_find(&set, "cpu_temp");

    TEST_ASSERT_EQUAL_INT(0, sensor_inject_fault(&set, cpu, FAULT_OVERTEMP));
    for (int t = 0; t < 100; t++) {
        TEST_ASSERT_EQUAL(STATUS_CRITICAL, sensor_status(cpu));
        TEST_ASSERT_TRUE(cpu->value > cpu->upper_crit);
        sensors_tick(&set);
    }
}

static void test_overtemp_rejected_for_non_temperature_sensors(void)
{
    struct sensor *fan = sensors_find(&set, "fan1_rpm");
    struct sensor *psu = sensors_find(&set, "psu_watts");

    TEST_ASSERT_EQUAL_INT(-1, sensor_inject_fault(&set, fan, FAULT_OVERTEMP));
    TEST_ASSERT_EQUAL_INT(-1, sensor_inject_fault(&set, psu, FAULT_OVERTEMP));
    TEST_ASSERT_EQUAL(FAULT_NONE, fan->fault);
    TEST_ASSERT_EQUAL(FAULT_NONE, psu->fault);
}

static void test_stuck_freezes_value(void)
{
    struct sensor *fan = sensors_find(&set, "fan2_rpm");
    double frozen;

    sensors_tick(&set);
    frozen = fan->value;
    TEST_ASSERT_EQUAL_INT(0, sensor_inject_fault(&set, fan, FAULT_STUCK));
    for (int t = 0; t < 50; t++) {
        sensors_tick(&set);
        TEST_ASSERT_EQUAL_DOUBLE(frozen, fan->value);
    }
    TEST_ASSERT_EQUAL(STATUS_OK, sensor_status(fan)); /* silent failure */
}

static void test_disconnected_is_unavailable(void)
{
    struct sensor *psu = sensors_find(&set, "psu_watts");

    TEST_ASSERT_EQUAL_INT(0, sensor_inject_fault(&set, psu, FAULT_DISCONNECTED));
    sensors_tick(&set);
    TEST_ASSERT_TRUE(isnan(psu->value));
    TEST_ASSERT_EQUAL(STATUS_UNAVAILABLE, sensor_status(psu));

    /* Switching a disconnected sensor to "stuck" keeps it unavailable. */
    TEST_ASSERT_EQUAL_INT(0, sensor_inject_fault(&set, psu, FAULT_STUCK));
    TEST_ASSERT_EQUAL(STATUS_UNAVAILABLE, sensor_status(psu));
}

static void test_noise_widens_spread(void)
{
    struct sensor *inlet = sensors_find(&set, "inlet_temp");
    double lo = INFINITY, hi = -INFINITY;

    TEST_ASSERT_EQUAL_INT(0, sensor_inject_fault(&set, inlet, FAULT_NOISE));
    for (int t = 0; t < 1000; t++) {
        sensors_tick(&set);
        TEST_ASSERT_DOUBLE_WITHIN(10.0 * inlet->jitter, inlet->nominal, inlet->value);
        lo = fmin(lo, inlet->value);
        hi = fmax(hi, inlet->value);
    }
    /* Healthy jitter alone could never produce this spread in one step. */
    TEST_ASSERT_TRUE(hi - lo > 10.0 * inlet->jitter);
}

static void test_clear_faults_restores_nominal(void)
{
    sensor_inject_fault(&set, sensors_find(&set, "cpu_temp"), FAULT_OVERTEMP);
    sensor_inject_fault(&set, sensors_find(&set, "fan1_rpm"), FAULT_DISCONNECTED);
    sensors_clear_faults(&set);
    for (size_t i = 0; i < SENSOR_COUNT; i++) {
        TEST_ASSERT_EQUAL(FAULT_NONE, set.s[i].fault);
        TEST_ASSERT_EQUAL_DOUBLE(set.s[i].nominal, set.s[i].value);
        TEST_ASSERT_EQUAL(STATUS_OK, sensor_status(&set.s[i]));
    }
}

static void test_recovers_from_nan_after_manual_fault_reset(void)
{
    struct sensor *psu = sensors_find(&set, "psu_watts");

    sensor_inject_fault(&set, psu, FAULT_DISCONNECTED);
    psu->fault = FAULT_NONE; /* fault cleared without resetting the value */
    sensors_tick(&set);
    TEST_ASSERT_FALSE(isnan(psu->value));
    TEST_ASSERT_EQUAL(STATUS_OK, sensor_status(psu));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_all_sensors_start_nominal_and_ok);
    RUN_TEST(test_healthy_values_stay_near_nominal);
    RUN_TEST(test_simulation_is_reproducible_per_seed);
    RUN_TEST(test_thresholds);
    RUN_TEST(test_status_names);
    RUN_TEST(test_fault_mode_parse);
    RUN_TEST(test_overtemp_is_critical_immediately_and_persists);
    RUN_TEST(test_overtemp_rejected_for_non_temperature_sensors);
    RUN_TEST(test_stuck_freezes_value);
    RUN_TEST(test_disconnected_is_unavailable);
    RUN_TEST(test_noise_widens_spread);
    RUN_TEST(test_clear_faults_restores_nominal);
    RUN_TEST(test_recovers_from_nan_after_manual_fault_reset);
    return UNITY_END();
}
