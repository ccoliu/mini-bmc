#include "protocol.h"

#include <string.h>

#include "json.h"

static void write_sensor(struct json_buf *b, const struct sensor *s)
{
    jb_raw(b, "{\"name\":");
    jb_str(b, s->name);
    jb_raw(b, ",\"value\":");
    jb_num(b, s->value); /* NaN (disconnected) becomes null */
    jb_raw(b, ",\"unit\":");
    jb_str(b, s->unit);
    jb_raw(b, ",\"status\":");
    jb_str(b, sensor_status_name(sensor_status(s)));
    jb_raw(b, "}");
}

size_t protocol_error(const char *code, char *out, size_t cap)
{
    struct json_buf b;

    jb_init(&b, out, cap);
    jb_raw(&b, "{\"ok\":false,\"error\":");
    jb_str(&b, code);
    jb_raw(&b, "}");
    return b.len;
}

// 檢查緩衝區是否有溢位並回傳字串長度
static size_t finish(const struct json_buf *b, char *out, size_t cap)
{
    /* Only reachable if PROTOCOL_MAX_RESPONSE is too small for the sensor table;
     * never send a truncated object. */
    if (b->overflow)
        return protocol_error("internal_error", out, cap);
    return b->len;
}

// 尋找指定的 sensor
static const char *lookup_sensor(struct sensor_set *set, const struct json_object *req,
                                 struct sensor **found)
{
    const char *name = json_get(req, "sensor"); // 取得 sensor 名稱

    if (name == NULL) // 若沒有 sensor
        return "missing_field";
    *found = sensors_find(set, name); // 尋找 sensor
    return *found == NULL ? "unknown_sensor" : NULL; // 若沒有找到 sensor，回傳 "unknown_sensor"
}

// 解析指令
size_t protocol_handle(struct sensor_set *set, const char *line, char *out, size_t cap)
{
    struct json_object req;
    struct json_buf b;
    const char *cmd;
    const char *err;
    struct sensor *s = NULL;

    if (json_parse_flat(line, &req) != 0)
        return protocol_error("bad_request", out, cap); // 格式錯誤
    cmd = json_get(&req, "cmd");
    if (cmd == NULL)
        return protocol_error("missing_field", out, cap); // 缺少指令

    jb_init(&b, out, cap); // 初始化 json_buf

    if (strcmp(cmd, "read_all") == 0) { // 若指令為 read_all
        jb_raw(&b, "{\"ok\":true,\"sensors\":["); // 遍歷所有 6 顆感測器
        for (size_t i = 0; i < SENSOR_COUNT; i++) {
            if (i > 0)
                jb_raw(&b, ",");
            write_sensor(&b, &set->s[i]);
        }
        jb_raw(&b, "]}");
        return finish(&b, out, cap); // 回傳字串長度
    }

    if (strcmp(cmd, "read") == 0) { // 若指令為 read
        err = lookup_sensor(set, &req, &s); // 尋找指定的 sensor
        if (err != NULL) // 若沒有找到 sensor，回傳錯誤訊息
            return protocol_error(err, out, cap);
        jb_raw(&b, "{\"ok\":true,\"sensor\":");
        write_sensor(&b, s);
        jb_raw(&b, "}");
        return finish(&b, out, cap);
    }

    if (strcmp(cmd, "inject_fault") == 0) { // 若指令為 inject_fault
        const char *mode_name = json_get(&req, "mode"); // 取得 mode
        enum fault_mode mode;

        err = lookup_sensor(set, &req, &s); // 尋找指定的 sensor
        if (err != NULL) // 若沒有找到 sensor，回傳錯誤訊息
            return protocol_error(err, out, cap);
        if (mode_name == NULL) // 若沒有 mode
            return protocol_error("missing_field", out, cap);
        if (fault_mode_parse(mode_name, &mode) != 0) // 解析 mode
            return protocol_error("unknown_mode", out, cap);
        if (sensor_inject_fault(set, s, mode) != 0) // 注入 fault
            return protocol_error("invalid_fault_for_sensor", out, cap);
        jb_raw(&b, "{\"ok\":true}");
        return finish(&b, out, cap);
    }

    if (strcmp(cmd, "clear_faults") == 0) { // 若指令為 clear_faults
        sensors_clear_faults(set); // 清除所有 fault
        jb_raw(&b, "{\"ok\":true}"); // 回傳 ok
        return finish(&b, out, cap);
    }

    return protocol_error("unknown_cmd", out, cap); // 若指令為未知的指令，回傳未知指令
}
