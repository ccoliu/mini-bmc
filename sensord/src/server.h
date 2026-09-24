/* Unix domain socket server: newline-delimited requests, one response per line. */
#ifndef SENSORD_SERVER_H
#define SENSORD_SERVER_H

#include <stdint.h>

struct server_config {
    const char *socket_path;
    unsigned tick_ms;
    uint64_t seed;
};

/* Runs until SIGINT/SIGTERM. Returns 0 on clean shutdown, 1 on setup failure. */
int server_run(const struct server_config *cfg);

#endif
