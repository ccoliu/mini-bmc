#include "sensors.h"

#include <math.h>
#include <string.h>

static const struct sensor defaults[SENSOR_COUNT] = {
    /* name          kind          unit    nominal jitter lower_crit  upper_warn upper_crit */
    { "cpu_temp",   SENSOR_TEMP,  "C",    45.0,   0.5,   -INFINITY,  85.0,      95.0,     0, FAULT_NONE },
    { "gpu_temp",   SENSOR_TEMP,  "C",    50.0,   0.5,   -INFINITY,  83.0,      93.0,     0, FAULT_NONE },
    { "inlet_temp", SENSOR_TEMP,  "C",    24.0,   0.2,   -INFINITY,  35.0,      40.0,     0, FAULT_NONE },
    { "fan1_rpm",   SENSOR_FAN,   "RPM",  6000.0, 50.0,  1000.0,     INFINITY,  INFINITY, 0, FAULT_NONE },
    { "fan2_rpm",   SENSOR_FAN,   "RPM",  6000.0, 50.0,  1000.0,     INFINITY,  INFINITY, 0, FAULT_NONE },
    { "psu_watts",  SENSOR_POWER, "W",    350.0,  10.0,  -INFINITY,  750.0,     800.0,    0, FAULT_NONE },
};

/* Degrees above the critical threshold that an overtemp fault drives a sensor to. */
#define OVERTEMP_MARGIN 5.0
/* Fraction of the distance back to nominal recovered each healthy step. */
#define REVERSION 0.2
#define NOISE_FACTOR 10.0

/* splitmix64: tiny, well-distributed, and valid for any seed including 0. */
static uint64_t next_u64(uint64_t *state)
{
    uint64_t z = (*state += 0x9E3779B97F4A7C15ULL);

    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

/* Uniform in [-1, 1). */
static double next_unit(uint64_t *state)
{
    return (double)(next_u64(state) >> 11) * 0x1.0p-52 - 1.0;
}

static void step(struct sensor *s, uint64_t *rng)
{
    switch (s->fault) {
    case FAULT_NONE: //正常狀態，若斷線(NAN)則恢復為nominal
        if (isnan(s->value))
            s->value = s->nominal;
        s->value += REVERSION * (s->nominal - s->value) + s->jitter * next_unit(rng); // REVERSION: 從當前值往 ideal 漂移；jitter 帶來隨機雜訊。
        break;
    case FAULT_OVERTEMP: //過熱
        s->value = s->upper_crit + OVERTEMP_MARGIN + s->jitter * next_unit(rng); // 數值拉到 upper_crit 並加上 OVERTEMP_MARGIN
        break;
    case FAULT_STUCK: //卡住
        break; // 不更新，保持當前值
    case FAULT_DISCONNECTED: //斷線
        s->value = NAN;
        break;
    case FAULT_NOISE: //噪聲
        s->value = s->nominal + NOISE_FACTOR * s->jitter * next_unit(rng);
        break;
    }
    if (s->value < 0.0)
        s->value = 0.0;
}

void sensors_init(struct sensor_set *set, uint64_t seed)
{
    memcpy(set->s, defaults, sizeof defaults); // 將 defaults 的內容複製到 set->s
    for (size_t i = 0; i < SENSOR_COUNT; i++) { 
        set->s[i].value = set->s[i].nominal; // 設定初始數值為 nominal
    }
    set->rng = seed;
}

void sensors_tick(struct sensor_set *set)
{
    for (size_t i = 0; i < SENSOR_COUNT; i++)
        step(&set->s[i], &set->rng); // 更新 sensor 的數值
}

struct sensor *sensors_find(struct sensor_set *set, const char *name) // 尋找 name 相同的 sensor
{
    for (size_t i = 0; i < SENSOR_COUNT; i++) {
        if (strcmp(set->s[i].name, name) == 0)
            return &set->s[i]; // 回傳找到的 sensor
    }
    return NULL;
}

enum sensor_status sensor_status(const struct sensor *s) // 判斷 sensor 的狀態
{
    if (s->fault == FAULT_DISCONNECTED || isnan(s->value)) // 若斷線或值為 NAN
        return STATUS_UNAVAILABLE;
    if (s->value >= s->upper_crit || s->value <= s->lower_crit) // 若超過上下限
        return STATUS_CRITICAL;
    if (s->value >= s->upper_warn) // 若超過上限
        return STATUS_WARNING;
    return STATUS_OK; // 正常狀態
}

const char *sensor_status_name(enum sensor_status st) // 取得狀態名稱
{
    switch (st) {
    case STATUS_OK:          return "ok";
    case STATUS_WARNING:     return "warning";
    case STATUS_CRITICAL:    return "critical";
    case STATUS_UNAVAILABLE: return "unavailable";
    }
    return "unknown";
}

// 將外部輸入的故障名稱字串解析為 enum fault_mode 列舉
int fault_mode_parse(const char *name, enum fault_mode *out)
{
    static const struct {
        const char *name;
        enum fault_mode mode;
    } modes[] = {
        { "overtemp", FAULT_OVERTEMP },
        { "stuck", FAULT_STUCK },
        { "disconnected", FAULT_DISCONNECTED },
        { "noise", FAULT_NOISE },
    };

    for (size_t i = 0; i < sizeof modes / sizeof modes[0]; i++) {
        if (strcmp(modes[i].name, name) == 0) {
            *out = modes[i].mode;
            return 0;
        }
    }
    return -1;
}

// 對特定感測器立即注入指定的故障
int sensor_inject_fault(struct sensor_set *set, struct sensor *s, enum fault_mode mode)
{
    if (mode == FAULT_OVERTEMP && s->kind != SENSOR_TEMP) // 若是過熱且 sensor 不是溫度類型則回傳 -1
        return -1;
    s->fault = mode; // 設定故障模式
    step(s, &set->rng); // 執行一次 step 更新數值
    return 0;
}

// 清除所有故障，將所有 sensor 恢復到 nominal 值
void sensors_clear_faults(struct sensor_set *set)
{
    for (size_t i = 0; i < SENSOR_COUNT; i++) {
        set->s[i].fault = FAULT_NONE;
        set->s[i].value = set->s[i].nominal;
    }
}
