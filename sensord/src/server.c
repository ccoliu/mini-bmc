#include "server.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "protocol.h"
#include "sensors.h"

#define MAX_CLIENTS 16

struct client {
    int fd;                             // -1 表示目前沒有用戶端連線
    char line[PROTOCOL_MAX_LINE + 1];   // 暫存讀取中的當前文字
    size_t len;                         // 目前暫存文字的長度
    bool discarding;                    // 長度超標設定為 true，代表正在丢棄
    bool bad_byte;                      // 目前暫存文字中包含不合法的字元 (null)
};

static volatile sig_atomic_t stop_requested;

// 當收到 SIGINT (Ctrl+C) 或 SIGTERM 時，將全域揮發變數 stop_requested = 1，通知主迴圈準備安全結束。
static void on_signal(int sig)
{
    (void)sig; // 忽略未使用的參數 sig，防止編譯器警告
    stop_requested = 1; // 設定停止請求標誌
}

// 註冊行程訊號
static int install_signals(void)
{
    struct sigaction sa;

    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal; /* no SA_RESTART: poll() must return EINTR */
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGINT, &sa, NULL) != 0 || sigaction(SIGTERM, &sa, NULL) != 0) // 捕捉 SIGINT (Ctrl+C) 和 SIGTERM (程式結束訊號)
        return -1;
    sa.sa_handler = SIG_IGN; // 設定為忽略
    return sigaction(SIGPIPE, &sa, NULL); // 忽略 SIGPIPE (管道破裂)
}

// 以毫秒（ms）為單位取得當前的單調時間（CLOCK_MONOTONIC）
static uint64_t now_ms(void)
{
    struct timespec ts; 

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

// 透過 fcntl 將 Socket 設定為非阻塞（O_NONBLOCK）與關閉時釋放（FD_CLOEXEC）
static int set_nonblock_cloexec(int fd)
{
    int fl = fcntl(fd, F_GETFL); // 取得檔案描述符（FD）目前的 flag（狀態）

    if (fl < 0 || fcntl(fd, F_SETFL, fl | O_NONBLOCK) < 0) // 若失敗(-1) 或 嘗試設定非阻塞失敗則回傳 -1
        return -1;
    return fcntl(fd, F_SETFD, FD_CLOEXEC); // 關閉時自動釋放
}

// 移除由當機程式遺留的 Socket
// 若連線成功（rc == 0）➔ 表示已經有另一個正在運行的服務，拒絕覆蓋並報錯退出
// 只有連線被拒絕（errno == ECONNREFUSED）➔ 證明沒人在聽，確定是死掉的殘留檔，才安心呼叫 unlink() 刪除它。
static int clear_stale_socket(const struct sockaddr_un *addr)
{
    struct stat st;
    int fd;
    int rc;
    int err;

    if (lstat(addr->sun_path, &st) != 0) // 若失敗(-1) 或 lstat 發現檔案不存在則回傳 0
        return errno == ENOENT ? 0 : -1;
    if (!S_ISSOCK(st.st_mode)) { // 若不是 Socket 則回傳 -1
        fprintf(stderr, "sensord: %s exists and is not a socket\n", addr->sun_path);
        return -1;
    }
    fd = socket(AF_UNIX, SOCK_STREAM, 0); // 建立 Socket
    if (fd < 0) 
        return -1;
    rc = connect(fd, (const struct sockaddr *)addr, sizeof *addr); // 嘗試連線
    err = errno; // 記錄錯誤碼
    close(fd); // 關閉 Socket
    if (rc == 0) { // 若成功(0)，表示已經有實例正在監聽，拒絕啟動
        fprintf(stderr, "sensord: another instance is listening on %s\n", addr->sun_path);
        return -1;
    }
    /* Only "nobody is listening" proves the socket is stale. Any other failure
     * (e.g. EACCES) says nothing about liveness, so leave the path alone. */
    if (err != ECONNREFUSED) { // 若不是連線被拒絕
        fprintf(stderr, "sensord: cannot probe %s: %s\n", addr->sun_path, strerror(err));
        return -1;
    }
    return unlink(addr->sun_path); // 移除 Socket
}

// 建立並綁定 Unix Domain Socket 的監聽端點。
static int open_listener(const char *path)
{
    struct sockaddr_un addr; // 用來存放 Socket 的位址資訊
    int fd; // Socket 的檔案描述符

    memset(&addr, 0, sizeof addr); // 將 addr 清零
    addr.sun_family = AF_UNIX; // 設定 Socket 的位址家族
    if (strlen(path) >= sizeof addr.sun_path) { // 若路徑長度超過 addr.sun_path 的長度
        fprintf(stderr, "sensord: socket path too long: %s\n", path);
        return -1;
    }
    strcpy(addr.sun_path, path); // 複製路徑到 addr.sun_path

    if (clear_stale_socket(&addr) != 0) // 移除舊的 Socket
        return -1;
    fd = socket(AF_UNIX, SOCK_STREAM, 0); // 建立 Socket
    if (fd < 0) {
        perror("sensord: socket");
        return -1;
    }
    if (set_nonblock_cloexec(fd) != 0 || // 設定為非阻塞與關閉時釋放
        bind(fd, (const struct sockaddr *)&addr, sizeof addr) != 0 || // 綁定 Socket
        listen(fd, MAX_CLIENTS) != 0) { // 監聽 Socket
        perror("sensord: bind/listen");
        close(fd);
        return -1;
    }
    return fd;
}

// 關閉客戶端的連線
static void drop_client(struct client *c)
{
    close(c->fd); // 關閉 Socket
    c->fd = -1;
}

// 發送一行的回應。
// 若客戶端的 Socket 沒有及時處理（drain）資料，
// 該客戶端會被中斷連線，以防止它拖慢整個守護進程。
static void send_line(struct client *c, const char *resp, size_t len)
{
    char buf[PROTOCOL_MAX_RESPONSE + 1];
    size_t off = 0;

    memcpy(buf, resp, len); // 複製回應到 buf
    buf[len++] = '\n'; // 在末尾加上換行符
    while (off < len) { // 若還沒發送完
        ssize_t n = send(c->fd, buf + off, len - off, MSG_NOSIGNAL); // 發送資料

        if (n < 0 && errno == EINTR) // 若因為中斷而失敗
            continue; // 繼續發送
        if (n <= 0) { // 若失敗或資料發送不完整
            drop_client(c); // 關閉連線
            return;
        }
        off += (size_t)n; // 更新已發送的資料長度
    }
}

// 回傳錯誤訊息
static void reply_error(struct client *c, const char *code)
{
    char out[PROTOCOL_MAX_RESPONSE];
    size_t len = protocol_error(code, out, sizeof out); // 格式化錯誤訊息

    send_line(c, out, len); // 發送錯誤訊息(json format)
}

// 結束一行指令
static void finish_line(struct client *c, struct sensor_set *set)
{
    if (c->len > 0 && c->line[c->len - 1] == '\r') // 若最後一個字元是 \r
        c->len--; // 去掉 \r
    c->line[c->len] = '\0'; // 加入 \0
    if (c->bad_byte) { // 若有不合法的字元
        reply_error(c, "bad_request"); // 回傳錯誤訊息
    } else {
        char out[PROTOCOL_MAX_RESPONSE]; // 準備輸出緩衝區
        size_t len = protocol_handle(set, c->line, out, sizeof out); // 處理指令

        send_line(c, out, len); // 輸出json格式的回應
    }
    c->len = 0;
    c->bad_byte = false;
}

// 讀取客戶端的資料
static void handle_readable(struct client *c, struct sensor_set *set)
{
    char chunk[512]; // 讀取 512 bytes 的資料
    ssize_t n = recv(c->fd, chunk, sizeof chunk, 0); // 接收資料

    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) // 若因為暫時無法讀取或中斷而失敗
        return;
    if (n <= 0) { // 若失敗或資料讀取不完整
        drop_client(c); // 關閉連線
        return;
    }
    for (ssize_t i = 0; i < n && c->fd >= 0; i++) { // 讀取每一個字元
        char ch = chunk[i]; // 取得當前字元

        if (ch == '\n') { // 若是換行符
            if (c->discarding) // 若正在忽略資料
                c->discarding = false; // 停止忽略
            else
                finish_line(c, set); // 結束一行指令
        } else if (c->discarding) {
            continue;
        } else if (c->len == PROTOCOL_MAX_LINE) { // 若單行指令長度超過上限
            reply_error(c, "line_too_long"); // 回傳錯誤訊息
            c->discarding = true; // 停止讀取
            c->len = 0;
            c->bad_byte = false;
        } else {
            if (ch == '\0') // 若是 \0
                c->bad_byte = true; // 設定為不合法字元
            c->line[c->len++] = ch; // 不是 \0 的話，加入到 c->line
        }
    }
}

// 接收客戶端連線
static void accept_client(int listen_fd, struct client *clients)
{
    int fd = accept(listen_fd, NULL, NULL); // 接收客戶端連線

    if (fd < 0) // 若失敗
        return;
    if (set_nonblock_cloexec(fd) != 0) { // 設定 nonblock 失敗
        close(fd); // 關閉連線
        return;
    }
    for (size_t i = 0; i < MAX_CLIENTS; i++) { // 遍歷所有客戶端
        if (clients[i].fd < 0) { // 若找到沒有連線的客戶端
            memset(&clients[i], 0, sizeof clients[i]); // 清零
            clients[i].fd = fd; // 設定為已連線
            return; // 回傳
        }
    }
    {
        struct client tmp = { .fd = fd }; // 建立暫時的客戶端

        reply_error(&tmp, "too_many_clients"); // 回傳錯誤訊息
        if (tmp.fd >= 0) // 若成功
            close(fd); // 關閉連線
    }
}

// server 主程式
int server_run(const struct server_config *cfg)
{
    struct sensor_set set; // sensor_set
    struct client clients[MAX_CLIENTS]; // 設定最大客戶端數量
    struct pollfd pfds[MAX_CLIENTS + 1]; // pollfd 陣列
    struct client *owners[MAX_CLIENTS + 1]; // owners 陣列
    uint64_t next_tick; // 下一次 tick 時間
    int listen_fd; // 監聽 Socket

    if (install_signals() != 0) { // 安裝 signal
        perror("sensord: sigaction");
        return 1;
    }
    listen_fd = open_listener(cfg->socket_path); // 打開監聽 Socket
    if (listen_fd < 0) // 若失敗
        return 1;

    sensors_init(&set, cfg->seed); // 初始化 sensor
    for (size_t i = 0; i < MAX_CLIENTS; i++) // 遍歷所有客戶端
        clients[i].fd = -1; // 設定為未連線
    fprintf(stderr, "sensord: listening on %s\n", cfg->socket_path);

    next_tick = now_ms() + cfg->tick_ms; // 設定下一次 tick 時間
    while (!stop_requested) { // 只要沒有收到 stop 訊號
        nfds_t nfds = 1; // pollfd 陣列的大小
        uint64_t now = now_ms(); // 當前時間
        int timeout = next_tick > now ? (int)(next_tick - now) : 0; // 等待時間
        int ready; // poll 回傳的值
        
        // 第 0 格放 監聽 socket
        pfds[0].fd = listen_fd;
        pfds[0].events = POLLIN; // 有資料可讀
        // 第 1 格後 放所有客戶端的 socket
        for (size_t i = 0; i < MAX_CLIENTS; i++) {
            if (clients[i].fd >= 0) { // 若客戶端已連線
                pfds[nfds].fd = clients[i].fd; // 設定客戶端的 socket
                pfds[nfds].events = POLLIN; // 有資料可讀
                owners[nfds] = &clients[i]; // 記錄該 socket 屬於哪個客戶端
                nfds++; // 增加 pollfd 陣列的大小
            }
        }

        ready = poll(pfds, nfds, timeout); // 檢查是否有 socket 有資料可讀
        if (ready < 0 && errno != EINTR) { // 若失敗且不是因為中斷，列印錯誤訊息
            perror("sensord: poll");
            break;
        }

        now = now_ms(); // 更新當前時間
        if (now >= next_tick) { // 若當前時間大於下一次 tick 時間
            sensors_tick(&set); // 執行 tick
            next_tick = now - next_tick >= cfg->tick_ms ? now + cfg->tick_ms
                                                         : next_tick + cfg->tick_ms;
        }
        if (ready <= 0) // 若沒有 socket 有資料可讀
            continue;

        for (nfds_t i = 1; i < nfds; i++) { // 遍歷所有客戶端
            if (pfds[i].revents & (POLLIN | POLLHUP | POLLERR)) // 若客戶端有資料可讀
                handle_readable(owners[i], &set); // 處理客戶端資料
        }
        if (pfds[0].revents & POLLIN) // 若監聽 socket 有資料可讀
            accept_client(listen_fd, clients); // 接受客戶端連線
    }

    // 停止連線後，逐一關閉連線
    for (size_t i = 0; i < MAX_CLIENTS; i++) {
        if (clients[i].fd >= 0) // 若客戶端已連線
            close(clients[i].fd); // 關閉連線
    }
    close(listen_fd); // 關閉監聽 socket
    unlink(cfg->socket_path); // 刪除 socket 檔案
    fprintf(stderr, "sensord: shutting down\n"); // 列印停止訊息
    return 0; // 回傳 0
}
