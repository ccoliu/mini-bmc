#include <math.h>
#include <stdio.h>
#include <string.h>

#include "json.h"
#include "unity.h"

void setUp(void) {}
void tearDown(void) {}

static struct json_object obj;

static void test_parses_flat_object(void)
{
    TEST_ASSERT_EQUAL_INT(0, json_parse_flat(" { \"cmd\" : \"read\", \"sensor\":\"cpu_temp\" } ", &obj));
    TEST_ASSERT_EQUAL_UINT(2, obj.count);
    TEST_ASSERT_EQUAL_STRING("read", json_get(&obj, "cmd"));
    TEST_ASSERT_EQUAL_STRING("cpu_temp", json_get(&obj, "sensor"));
    TEST_ASSERT_NULL(json_get(&obj, "mode"));
}

static void test_parses_empty_object(void)
{
    TEST_ASSERT_EQUAL_INT(0, json_parse_flat("{}", &obj));
    TEST_ASSERT_EQUAL_UINT(0, obj.count);
}

static void test_decodes_escapes(void)
{
    TEST_ASSERT_EQUAL_INT(0, json_parse_flat("{\"k\":\"a\\\"b\\\\c\\/d\\n\"}", &obj));
    TEST_ASSERT_EQUAL_STRING("a\"b\\c/d\n", json_get(&obj, "k"));
}

static void test_rejects_malformed_input(void)
{
    static const char *bad[] = {
        "",
        "   ",
        "[]",
        "{",
        "{\"cmd\"}",
        "{\"cmd\":}",
        "{\"cmd\":\"read\"",
        "{\"cmd\":\"read\",}",
        "{\"cmd\":\"read\"} trailing",
        "{\"cmd\":\"read\" \"x\":\"y\"}",
        "{\"cmd\":42}",
        "{\"cmd\":true}",
        "{\"cmd\":null}",
        "{\"cmd\":{\"a\":\"b\"}}",
        "{\"cmd\":[\"a\"]}",
        "{cmd:\"read\"}",
        "{\"cmd\":\"re\nad\"}",
        "{\"cmd\":\"\\u0041\"}",
        "{\"cmd\":\"\\x\"}",
        "{\"cmd\":\"unterminated",
        "{\"cmd\":\"read\\",
    };

    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++)
        TEST_ASSERT_EQUAL_INT_MESSAGE(-1, json_parse_flat(bad[i], &obj), bad[i]);
    TEST_ASSERT_EQUAL_INT(-1, json_parse_flat(NULL, &obj));
}

static void test_rejects_oversized_values(void)
{
    char text[256];
    char val[JSON_MAX_VAL + 1];

    memset(val, 'a', JSON_MAX_VAL - 1);
    val[JSON_MAX_VAL - 1] = '\0';
    snprintf(text, sizeof text, "{\"k\":\"%s\"}", val);
    TEST_ASSERT_EQUAL_INT(0, json_parse_flat(text, &obj)); /* exactly fits */

    memset(val, 'a', JSON_MAX_VAL);
    val[JSON_MAX_VAL] = '\0';
    snprintf(text, sizeof text, "{\"k\":\"%s\"}", val);
    TEST_ASSERT_EQUAL_INT(-1, json_parse_flat(text, &obj));
}

static void test_rejects_too_many_fields(void)
{
    const char *ok = "{\"a\":\"1\",\"b\":\"2\",\"c\":\"3\",\"d\":\"4\","
                     "\"e\":\"5\",\"f\":\"6\",\"g\":\"7\",\"h\":\"8\"}";
    const char *bad = "{\"a\":\"1\",\"b\":\"2\",\"c\":\"3\",\"d\":\"4\","
                      "\"e\":\"5\",\"f\":\"6\",\"g\":\"7\",\"h\":\"8\",\"i\":\"9\"}";

    TEST_ASSERT_EQUAL_INT(0, json_parse_flat(ok, &obj));
    TEST_ASSERT_EQUAL_INT(-1, json_parse_flat(bad, &obj));
}

static void test_writer_escapes_and_formats(void)
{
    char storage[128];
    struct json_buf b;

    jb_init(&b, storage, sizeof storage);
    jb_raw(&b, "[");
    jb_str(&b, "q\"b\\n\n\x01");
    jb_raw(&b, ",");
    jb_num(&b, 42.0);
    jb_raw(&b, ",");
    jb_num(&b, -1.25);
    jb_raw(&b, ",");
    jb_num(&b, NAN);
    jb_raw(&b, ",");
    jb_num(&b, INFINITY);
    jb_raw(&b, "]");
    TEST_ASSERT_FALSE(b.overflow);
    TEST_ASSERT_EQUAL_STRING("[\"q\\\"b\\\\n\\n\\u0001\",42.0,-1.2,null,null]", storage);
}

static void test_writer_never_overruns(void)
{
    char storage[8];
    struct json_buf b;

    memset(storage, 'X', sizeof storage);
    jb_init(&b, storage, 5);
    jb_raw(&b, "abcdefgh");
    TEST_ASSERT_TRUE(b.overflow);
    TEST_ASSERT_EQUAL_STRING("abcd", storage);
    TEST_ASSERT_EQUAL_CHAR('X', storage[5]);

    jb_init(&b, storage, 0);
    TEST_ASSERT_TRUE(b.overflow);
    jb_raw(&b, "a");
    TEST_ASSERT_EQUAL_UINT(0, b.len);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_parses_flat_object);
    RUN_TEST(test_parses_empty_object);
    RUN_TEST(test_decodes_escapes);
    RUN_TEST(test_rejects_malformed_input);
    RUN_TEST(test_rejects_oversized_values);
    RUN_TEST(test_rejects_too_many_fields);
    RUN_TEST(test_writer_escapes_and_formats);
    RUN_TEST(test_writer_never_overruns);
    return UNITY_END();
}
