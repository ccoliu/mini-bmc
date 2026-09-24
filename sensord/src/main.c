#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "server.h"

#define DEFAULT_SOCKET "/tmp/sensord.sock" // socket 路徑
#define DEFAULT_TICK_MS 1000u // 更新頻率 1000ms = 1s
#define MAX_TICK_MS 3600000ul // 最大更新頻率 3600000ms = 3600s = 1h

static void usage(FILE *f) // 顯示使用說明
{
    fprintf(f,
            "usage: sensord [-s SOCKET] [-t TICK_MS] [-S SEED]\n"
            "  -s  socket path   (env SENSORD_SOCKET,  default " DEFAULT_SOCKET ")\n"
            "  -t  tick interval (env SENSORD_TICK_MS, default 1000, range 1..3600000)\n"
            "  -S  PRNG seed     (env SENSORD_SEED,    default 1)\n");
}

// 解析無符號長整數
static int parse_ulong(const char *s, unsigned long long max, unsigned long long *out)
{
    char *end;
    unsigned long long v;

    if (s == NULL || *s == '\0' || *s == '-')
        return -1;
    errno = 0;
    v = strtoull(s, &end, 10); // 解析無符號長整數
    if (errno != 0 || *end != '\0' || v > max) // 若失敗或超出範圍則回傳 -1
        return -1;
    *out = v; // 回傳解析的無符號長整數
    return 0;
}

int main(int argc, char **argv)
{
    // 提取預設值
    struct server_config cfg = { DEFAULT_SOCKET, DEFAULT_TICK_MS, 1 };
    const char *sock = getenv("SENSORD_SOCKET"); // 取得 socket 路徑
    const char *tick = getenv("SENSORD_TICK_MS"); // 取得更新頻率
    const char *seed = getenv("SENSORD_SEED"); // 取得 PRNG 種子
    unsigned long long v;
    int opt;

    // 解析指令列參數
    while ((opt = getopt(argc, argv, "s:t:S:h")) != -1) {
        switch (opt) {
        case 's': sock = optarg; break;
        case 't': tick = optarg; break;
        case 'S': seed = optarg; break;
        case 'h': usage(stdout); return 0;
        default:  usage(stderr); return 2;
        }
    }
    if (optind != argc) { // 若還有未處理的參數
        usage(stderr); // 顯示使用說明
        return 2; // 回傳 2
    }
    
    if (sock != NULL && *sock != '\0') // 若 socket 路徑不為空
        cfg.socket_path = sock; // 設定 socket 路徑
    if (tick != NULL) { // 若更新頻率不為空
        if (parse_ulong(tick, MAX_TICK_MS, &v) != 0 || v == 0) { // 若解析失敗或小於 0
            fprintf(stderr, "sensord: invalid tick interval: %s\n", tick); // 列印錯誤訊息
            return 2;
        }
        cfg.tick_ms = (unsigned)v; // 設定更新頻率
    }
    if (seed != NULL) { // 若 PRNG 種子不為空
        if (parse_ulong(seed, UINT64_MAX, &v) != 0) { // 若解析失敗
            fprintf(stderr, "sensord: invalid seed: %s\n", seed); // 列印錯誤訊息
            return 2;
        }
        cfg.seed = v; // 設定 PRNG 種子
    }

    return server_run(&cfg); // 執行 server
}
