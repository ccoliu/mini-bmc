#include <stdio.h>
#include <string.h>

#include "protocol.h"
#include "unity.h"

static struct sensor_set set;
static char out[PROTOCOL_MAX_RESPONSE];

void setUp(void)
{
    sensors_init(&set, 1);
}

void tearDown(void) {}

static const char *handle(const char *line)
{
    size_t n = protocol_handle(&set, line, out, sizeof out);

    TEST_ASSERT_EQUAL_UINT(strlen(out), n);
    TEST_ASSERT_NULL_MESSAGE(strchr(out, '\n'), "response must be a single line");
    return out;
}

static void test_read_all_lists_every_sensor(void)
{
    handle("{\"cmd\":\"read_all\"}");
    TEST_ASSERT_EQUAL_STRING(
        "{\"ok\":true,\"sensors\":["
        "{\"name\":\"cpu_temp\",\"value\":45.0,\"unit\":\"C\",\"status\":\"ok\"},"
        "{\"name\":\"gpu_temp\",\"value\":50.0,\"unit\":\"C\",\"status\":\"ok\"},"
        "{\"name\":\"inlet_temp\",\"value\":24.0,\"unit\":\"C\",\"status\":\"ok\"},"
        "{\"name\":\"fan1_rpm\",\"value\":6000.0,\"unit\":\"RPM\",\"status\":\"ok\"},"
        "{\"name\":\"fan2_rpm\",\"value\":6000.0,\"unit\":\"RPM\",\"status\":\"ok\"},"
        "{\"name\":\"psu_watts\",\"value\":350.0,\"unit\":\"W\",\"status\":\"ok\"}]}",
        out);
}

static void test_read_single_sensor(void)
{
    handle("{\"cmd\":\"read\",\"sensor\":\"inlet_temp\"}");
    TEST_ASSERT_EQUAL_STRING(
        "{\"ok\":true,\"sensor\":{\"name\":\"inlet_temp\",\"value\":24.0,"
        "\"unit\":\"C\",\"status\":\"ok\"}}",
        out);
}

static void test_inject_overtemp_then_read(void)
{
    TEST_ASSERT_EQUAL_STRING("{\"ok\":true}",
        handle("{\"cmd\":\"inject_fault\",\"sensor\":\"cpu_temp\",\"mode\":\"overtemp\"}"));
    handle("{\"cmd\":\"read\",\"sensor\":\"cpu_temp\"}");
    TEST_ASSERT_NOT_NULL(strstr(out, "\"status\":\"critical\""));
}

static void test_disconnected_reports_null_value(void)
{
    handle("{\"cmd\":\"inject_fault\",\"sensor\":\"fan2_rpm\",\"mode\":\"disconnected\"}");
    handle("{\"cmd\":\"read\",\"sensor\":\"fan2_rpm\"}");
    TEST_ASSERT_EQUAL_STRING(
        "{\"ok\":true,\"sensor\":{\"name\":\"fan2_rpm\",\"value\":null,"
        "\"unit\":\"RPM\",\"status\":\"unavailable\"}}",
        out);
}

static void test_clear_faults(void)
{
    handle("{\"cmd\":\"inject_fault\",\"sensor\":\"gpu_temp\",\"mode\":\"overtemp\"}");
    TEST_ASSERT_EQUAL_STRING("{\"ok\":true}", handle("{\"cmd\":\"clear_faults\"}"));
    handle("{\"cmd\":\"read\",\"sensor\":\"gpu_temp\"}");
    TEST_ASSERT_NOT_NULL(strstr(out, "\"value\":50.0"));
    TEST_ASSERT_NOT_NULL(strstr(out, "\"status\":\"ok\""));
}

static void test_error_codes(void)
{
    static const struct {
        const char *req;
        const char *error;
    } cases[] = {
        { "", "bad_request" },
        { "not json", "bad_request" },
        { "{\"cmd\":1}", "bad_request" },
        { "{}", "missing_field" },
        { "{\"sensor\":\"cpu_temp\"}", "missing_field" },
        { "{\"cmd\":\"reboot\"}", "unknown_cmd" },
        { "{\"cmd\":\"read\"}", "missing_field" },
        { "{\"cmd\":\"read\",\"sensor\":\"cpu\"}", "unknown_sensor" },
        { "{\"cmd\":\"inject_fault\",\"mode\":\"stuck\"}", "missing_field" },
        { "{\"cmd\":\"inject_fault\",\"sensor\":\"cpu_temp\"}", "missing_field" },
        { "{\"cmd\":\"inject_fault\",\"sensor\":\"x\",\"mode\":\"stuck\"}", "unknown_sensor" },
        { "{\"cmd\":\"inject_fault\",\"sensor\":\"cpu_temp\",\"mode\":\"melt\"}", "unknown_mode" },
        { "{\"cmd\":\"inject_fault\",\"sensor\":\"fan1_rpm\",\"mode\":\"overtemp\"}",
          "invalid_fault_for_sensor" },
    };
    char expected[128];

    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        snprintf(expected, sizeof expected, "{\"ok\":false,\"error\":\"%s\"}", cases[i].error);
        TEST_ASSERT_EQUAL_STRING_MESSAGE(expected, handle(cases[i].req), cases[i].req);
    }
}

static void test_failed_inject_leaves_state_untouched(void)
{
    handle("{\"cmd\":\"inject_fault\",\"sensor\":\"psu_watts\",\"mode\":\"overtemp\"}");
    TEST_ASSERT_EQUAL(FAULT_NONE, sensors_find(&set, "psu_watts")->fault);
}

static void test_small_buffer_yields_valid_error_not_truncated_json(void)
{
    char small[64];

    protocol_handle(&set, "{\"cmd\":\"read_all\"}", small, sizeof small);
    TEST_ASSERT_EQUAL_STRING("{\"ok\":false,\"error\":\"internal_error\"}", small);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_read_all_lists_every_sensor);
    RUN_TEST(test_read_single_sensor);
    RUN_TEST(test_inject_overtemp_then_read);
    RUN_TEST(test_disconnected_reports_null_value);
    RUN_TEST(test_clear_faults);
    RUN_TEST(test_error_codes);
    RUN_TEST(test_failed_inject_leaves_state_untouched);
    RUN_TEST(test_small_buffer_yields_valid_error_not_truncated_json);
    return UNITY_END();
}
