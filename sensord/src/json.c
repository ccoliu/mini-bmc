#include "json.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

// 跳過空白字元
static const char *skip_ws(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')
        p++;
    return p;
}

//解析以雙引號開頭的 JSON 字串值或鍵名，並將解碼後的內容寫入 dst（會補 \0 結尾）。
/* Parses a JSON string starting at the opening quote into dst (NUL-terminated).
 * Returns a pointer past the closing quote, or NULL on error / overflow. */
static const char *parse_string(const char *p, char *dst, size_t cap)
{
    size_t n = 0;

    if (*p != '"')
        return NULL;
    p++;
    for (;;) {
        char c = *p++;

        if (c == '\0')
            return NULL;
        if (c == '"')
            break;
        if ((unsigned char)c < 0x20)
            return NULL; /* control characters must be escaped */
        if (c == '\\') {
            switch (*p++) {
            case '"':  c = '"';  break;
            case '\\': c = '\\'; break;
            case '/':  c = '/';  break;
            case 'b':  c = '\b'; break;
            case 'f':  c = '\f'; break;
            case 'n':  c = '\n'; break;
            case 'r':  c = '\r'; break;
            case 't':  c = '\t'; break;
            default:   return NULL; /* includes \u, which no request needs */
            }
        }
        if (n + 1 >= cap)
            return NULL;
        dst[n++] = c;
    }
    dst[n] = '\0';
    return p;
}

//解析頂層為單層物件（flat object）的 JSON 字串，例如 {"key1": "val1", "key2": "val2"}
int json_parse_flat(const char *text, struct json_object *out)
{
    const char *p;

    out->count = 0;
    if (text == NULL)
        return -1;

    p = skip_ws(text);
    if (*p++ != '{') //檢查是否以 { 開頭，支援空物件 {}。
        return -1;
    p = skip_ws(p);
    if (*p == '}') {
        p++;
        goto done;
    }

    for (;;) {
        struct json_field *f;

        if (out->count == JSON_MAX_FIELDS) // 若鍵的數量超過限制就回傳錯誤
            return -1;
        f = &out->fields[out->count]; // 參照 json object 的 fields 陣列中的第 index 個元素

        p = parse_string(skip_ws(p), f->key, sizeof f->key); //從有內容的key開始解析
        if (p == NULL)
            return -1;
        p = skip_ws(p);
        if (*p++ != ':') // 檢查是否為冒號
            return -1;
        p = parse_string(skip_ws(p), f->val, sizeof f->val);
        if (p == NULL)
            return -1;
        out->count++;

        p = skip_ws(p);
        if (*p == ',') {
            p++;
            continue;
        }
        if (*p == '}') {
            p++;
            break;
        }
        return -1;
    }

done:
    return *skip_ws(p) == '\0' ? 0 : -1;
}


// 在解析完成的 json_object 結構中搜尋指定的 key
const char *json_get(const struct json_object *obj, const char *key)
{
    for (size_t i = 0; i < obj->count; i++) {
        if (strcmp(obj->fields[i].key, key) == 0)
            return obj->fields[i].val;
    }
    return NULL;
}

// 初始化一個 JSON 輸出緩衝區結構體
void jb_init(struct json_buf *b, char *storage, size_t cap)
{
    b->data = storage;
    b->cap = cap;
    b->len = 0;
    b->overflow = cap == 0;
    if (cap > 0)
        storage[0] = '\0';
}


//向緩衝區寫入單一字元，並隨時維持以 \0 結尾。
static void jb_putc(struct json_buf *b, char c)
{
    if (b->overflow)
        return;
    if (b->len + 1 >= b->cap) {
        b->overflow = true;
        return;
    }
    b->data[b->len++] = c;
    b->data[b->len] = '\0';
}


// 將字串 s 原封不動 寫入緩衝區（不加雙引號也不進行任何跳脫轉義）。常用於寫入語法符號如 {、}、:、, 或固定關鍵字。
void jb_raw(struct json_buf *b, const char *s)
{
    while (*s != '\0')
        jb_putc(b, *s++);
}

// 將字串 s 格式化為合法的 JSON 字串輸出
void jb_str(struct json_buf *b, const char *s)
{
    jb_putc(b, '"');
    for (; *s != '\0'; s++) {
        unsigned char c = (unsigned char)*s;

        switch (c) {
        case '"':  jb_raw(b, "\\\""); break;
        case '\\': jb_raw(b, "\\\\"); break;
        case '\n': jb_raw(b, "\\n");  break;
        case '\r': jb_raw(b, "\\r");  break;
        case '\t': jb_raw(b, "\\t");  break;
        default:
            if (c < 0x20) {
                char esc[8];

                snprintf(esc, sizeof esc, "\\u%04x", c);
                jb_raw(b, esc);
            } else {
                jb_putc(b, (char)c);
            }
        }
    }
    jb_putc(b, '"');
}

// 將浮點數 v 格式化為小數點後 1 位的數字寫入 JSON（例如 36.5）。
void jb_num(struct json_buf *b, double v)
{
    char tmp[64];

    if (!isfinite(v)) {
        jb_raw(b, "null");
        return;
    }
    snprintf(tmp, sizeof tmp, "%.1f", v);
    jb_raw(b, tmp);
}
