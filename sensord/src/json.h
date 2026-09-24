/* Minimal JSON support for the sensord wire protocol.
 *
 * Parsing: only flat objects whose values are all strings, e.g.
 *   {"cmd": "read", "sensor": "cpu_temp"}
 * Anything else (nested values, numbers, trailing garbage, \u escapes) is rejected.
 *
 * Writing: a bounded buffer builder. Output never overruns the buffer; once it is
 * full the builder is marked as overflowed and further writes are ignored.
 */
#ifndef SENSORD_JSON_H
#define SENSORD_JSON_H

#include <stdbool.h>
#include <stddef.h>

#define JSON_MAX_FIELDS 8
#define JSON_MAX_KEY 32
#define JSON_MAX_VAL 64

struct json_field {
    char key[JSON_MAX_KEY];
    char val[JSON_MAX_VAL];
};

struct json_object {
    struct json_field fields[JSON_MAX_FIELDS];
    size_t count;
};

/* Returns 0 on success, -1 if `text` is not a flat object of string values
 * (or exceeds the size limits above). */
int json_parse_flat(const char *text, struct json_object *out);

/* Returns the value for `key`, or NULL if absent. */
const char *json_get(const struct json_object *obj, const char *key);

struct json_buf {
    char *data;
    size_t cap;
    size_t len;
    bool overflow;
};

void jb_init(struct json_buf *b, char *storage, size_t cap);
void jb_raw(struct json_buf *b, const char *s);
void jb_str(struct json_buf *b, const char *s); /* quoted and escaped */
void jb_num(struct json_buf *b, double v);      /* one decimal place */

#endif
