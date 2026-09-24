/* Request dispatch: one request line in, one response line out. No I/O. */
#ifndef SENSORD_PROTOCOL_H
#define SENSORD_PROTOCOL_H

#include <stddef.h>

#include "sensors.h"

/* Longest accepted request line, excluding the newline. */
#define PROTOCOL_MAX_LINE 1023
/* Large enough for a read_all response. */
#define PROTOCOL_MAX_RESPONSE 2048

/* Handles one request (without its trailing newline) and writes the response,
 * also without a newline, into `out`. Always produces a valid JSON response.
 * Returns the response length. */
size_t protocol_handle(struct sensor_set *set, const char *line, char *out, size_t cap);

/* Writes the error response for `code` into `out`; returns its length. */
size_t protocol_error(const char *code, char *out, size_t cap);

#endif
