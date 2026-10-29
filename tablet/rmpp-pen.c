/*
 * rmpp-pen: tiny helper copied to /tmp on the reMarkable (nothing installed).
 *
 *   rmpp-pen info               JSON description of every /dev/input/event*:
 *                               name, event bits, tool keys, ABS ranges+resolution
 *   rmpp-pen stream DEV [grab] [keepalive]  copy raw input_event records from DEV to stdout.
 *                               "grab" takes EVIOCGRAB so xochitl stops seeing the pen.
 *   rmpp-pen watch DEV          human-readable event dump (for verifying hover/tilt).
 *
 * Read-only apart from the optional grab, which is released when the process exits.
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

#define BITS_PER_LONG (sizeof(long) * 8)
#define NLONGS(x) (((x) + BITS_PER_LONG - 1) / BITS_PER_LONG)
#define TEST_BIT(bit, arr) (((arr)[(bit) / BITS_PER_LONG] >> ((bit) % BITS_PER_LONG)) & 1UL)

static const struct { int code; const char *name; } ABS_NAMES[] = {
    {ABS_X, "X"}, {ABS_Y, "Y"}, {ABS_PRESSURE, "PRESSURE"}, {ABS_DISTANCE, "DISTANCE"},
    {ABS_TILT_X, "TILT_X"}, {ABS_TILT_Y, "TILT_Y"}, {ABS_MT_SLOT, "MT_SLOT"},
    {ABS_MT_POSITION_X, "MT_POSITION_X"}, {ABS_MT_POSITION_Y, "MT_POSITION_Y"},
    {ABS_MT_PRESSURE, "MT_PRESSURE"}, {ABS_MT_TOUCH_MAJOR, "MT_TOUCH_MAJOR"},
    {ABS_MT_TRACKING_ID, "MT_TRACKING_ID"}, {ABS_MT_ORIENTATION, "MT_ORIENTATION"},
};
static const struct { int code; const char *name; } KEY_NAMES[] = {
    {BTN_TOOL_PEN, "TOOL_PEN"}, {BTN_TOOL_RUBBER, "TOOL_RUBBER"}, {BTN_TOUCH, "TOUCH"},
    {BTN_STYLUS, "STYLUS"}, {BTN_STYLUS2, "STYLUS2"}, {BTN_TOOL_FINGER, "TOOL_FINGER"},
    {KEY_POWER, "POWER"},
};

static void json_str(const char *s) {
    putchar('"');
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') putchar('\\');
        if ((unsigned char)*s >= 0x20) putchar(*s);
    }
    putchar('"');
}

static int cmp_names(const struct dirent **a, const struct dirent **b) {
    return atoi((*a)->d_name + 5) - atoi((*b)->d_name + 5);
}
static int is_event(const struct dirent *d) { return strncmp(d->d_name, "event", 5) == 0; }

/* First line of a small text file, JSON-quoted, or null (device-tree strings end in NUL). */
static void json_file(const char *path) {
    char buf[128] = "";
    FILE *f = fopen(path, "r");
    if (!f || !fgets(buf, sizeof buf, f)) { if (f) fclose(f); printf("null"); return; }
    fclose(f);
    buf[strcspn(buf, "\n")] = 0;
    json_str(buf);
}

static int cmd_info(void) {
    struct utsname u;
    uname(&u);
    printf("{\"machine\":"); json_str(u.machine);
    printf(",\"release\":"); json_str(u.release);
    /* which reMarkable: "reMarkable 1.0", "reMarkable 2.0", ... */
    printf(",\"model\":"); json_file("/sys/devices/soc0/machine");
    printf(",\"dt_model\":"); json_file("/proc/device-tree/model");
    printf(",\"hwrevision\":"); json_file("/etc/hwrevision");
    printf(",\"input_event_size\":%zu,\"devices\":[", sizeof(struct input_event));
    struct dirent **list;
    int n = scandir("/dev/input", &list, is_event, cmp_names);
    for (int i = 0; i < n; i++) {
        char path[300];
        snprintf(path, sizeof path, "/dev/input/%s", list[i]->d_name);
        int fd = open(path, O_RDONLY | O_NONBLOCK);
        printf("%s{\"path\":", i ? "," : ""); json_str(path);
        if (fd < 0) { printf(",\"error\":"); json_str(strerror(errno)); printf("}"); continue; }
        char name[256] = "";
        ioctl(fd, EVIOCGNAME(sizeof name), name);
        printf(",\"name\":"); json_str(name);
        unsigned long evbits[NLONGS(EV_CNT)] = {0}, keybits[NLONGS(KEY_CNT)] = {0},
                      absbits[NLONGS(ABS_CNT)] = {0}, props[NLONGS(INPUT_PROP_CNT)] = {0};
        ioctl(fd, EVIOCGBIT(0, sizeof evbits), evbits);
        ioctl(fd, EVIOCGBIT(EV_KEY, sizeof keybits), keybits);
        ioctl(fd, EVIOCGBIT(EV_ABS, sizeof absbits), absbits);
        ioctl(fd, EVIOCGPROP(sizeof props), props);
        printf(",\"direct\":%s", TEST_BIT(INPUT_PROP_DIRECT, props) ? "true" : "false");
        printf(",\"keys\":[");
        int first = 1;
        for (size_t k = 0; k < sizeof KEY_NAMES / sizeof *KEY_NAMES; k++)
            if (TEST_BIT(KEY_NAMES[k].code, keybits)) {
                printf("%s", first ? "" : ","); json_str(KEY_NAMES[k].name); first = 0;
            }
        printf("],\"abs\":{");
        first = 1;
        for (int code = 0; code < ABS_CNT; code++) {
            if (!TEST_BIT(code, absbits)) continue;
            struct input_absinfo ai;
            if (ioctl(fd, EVIOCGABS(code), &ai) < 0) continue;
            const char *nm = NULL;
            for (size_t k = 0; k < sizeof ABS_NAMES / sizeof *ABS_NAMES; k++)
                if (ABS_NAMES[k].code == code) nm = ABS_NAMES[k].name;
            char buf[32];
            if (!nm) { snprintf(buf, sizeof buf, "ABS_0x%02x", code); nm = buf; }
            printf("%s", first ? "" : ","); json_str(nm);
            printf(":{\"code\":%d,\"min\":%d,\"max\":%d,\"fuzz\":%d,\"flat\":%d,\"res\":%d}",
                   code, ai.minimum, ai.maximum, ai.fuzz, ai.flat, ai.resolution);
            first = 0;
        }
        printf("}}");
        close(fd);
    }
    printf("]}\n");
    return 0;
}

static long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000L + t.tv_nsec / 1000000L;
}

/* With `keepalive`, the PC sends a byte on stdin about once a second; if none arrives for
   KEEPALIVE_MS the link is treated as dead. That covers a pulled cable or a sleeping PC,
   where no EOF or hangup ever reaches this side and a grab would otherwise keep the pen
   from xochitl until the tablet reboots. Writes are non-blocking for the same reason: a
   write stuck on a dead link would never get to check the timer. */
#define KEEPALIVE_MS 4000

static int write_all(const char *b, ssize_t n, long deadline) {
    while (n > 0) {
        ssize_t w = write(1, b, n);
        if (w > 0) { b += w; n -= w; continue; }
        if (w < 0 && errno != EAGAIN && errno != EINTR) return -1;
        if (deadline && now_ms() > deadline) return -1;
        struct pollfd o = {.fd = 1, .events = POLLOUT};
        poll(&o, 1, 200);
        if (o.revents & (POLLERR | POLLHUP | POLLNVAL)) return -1;
    }
    return 0;
}

static int cmd_stream(const char *dev, int grab, int keepalive) {
    int fd = open(dev, O_RDONLY);
    if (fd < 0) { perror(dev); return 1; }
    if (grab && ioctl(fd, EVIOCGRAB, 1) < 0) perror("EVIOCGRAB");
    signal(SIGPIPE, SIG_DFL);
    if (keepalive) fcntl(1, F_SETFL, fcntl(1, F_GETFL) | O_NONBLOCK);
    long heard = now_ms();
    char buf[sizeof(struct input_event) * 64];
    /* EOF or a hangup on stdin also means the PC's channel is gone: dropbear can keep the
       stdout pipe open after a close, which left grabbing streams beside the newer one. */
    struct pollfd p[3] = {{.fd = fd, .events = POLLIN}, {.fd = 1, .events = 0}, {.fd = 0, .events = POLLIN}};
    for (;;) {
        /* Exit as soon as the SSH side is gone, even while the pen is idle: otherwise an
           orphan keeps running and, with a grab, keeps the pen away from xochitl. */
        int n = poll(p, 3, 500);
        if (n < 0) { if (errno == EINTR) continue; break; }
        if (p[1].revents & (POLLERR | POLLHUP | POLLNVAL)) break;
        if (p[2].revents & POLLIN) {
            char junk[256];
            if (read(0, junk, sizeof junk) <= 0) break;
            heard = now_ms();
        } else if (p[2].revents & (POLLHUP | POLLERR)) break;
        if (getppid() == 1) break;
        if (keepalive && now_ms() - heard > KEEPALIVE_MS) {
            fprintf(stderr, "rmpp-pen: no keepalive from the PC for %d ms, releasing the pen\n", KEEPALIVE_MS);
            break;
        }
        if (!(p[0].revents & POLLIN)) continue;
        ssize_t r = read(fd, buf, sizeof buf);
        if (r <= 0) { if (r < 0 && errno == EINTR) continue; break; }
        if (write_all(buf, r, keepalive ? heard + KEEPALIVE_MS : 0) < 0) break; /* ssh gone: drop the grab */
    }
    return 0;
}

static int cmd_watch(const char *dev) {
    int fd = open(dev, O_RDONLY);
    if (fd < 0) { perror(dev); return 1; }
    setvbuf(stdout, NULL, _IOLBF, 0);
    struct input_event ev;
    while (read(fd, &ev, sizeof ev) == sizeof ev) {
        if (ev.type == EV_SYN) { printf("---\n"); continue; }
        const char *nm = NULL;
        if (ev.type == EV_ABS)
            for (size_t k = 0; k < sizeof ABS_NAMES / sizeof *ABS_NAMES; k++)
                if (ABS_NAMES[k].code == ev.code) nm = ABS_NAMES[k].name;
        if (ev.type == EV_KEY)
            for (size_t k = 0; k < sizeof KEY_NAMES / sizeof *KEY_NAMES; k++)
                if (KEY_NAMES[k].code == ev.code) nm = KEY_NAMES[k].name;
        if (nm) printf("%s %s %d\n", ev.type == EV_ABS ? "ABS" : "KEY", nm, ev.value);
        else printf("type=%d code=%d value=%d\n", ev.type, ev.code, ev.value);
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "info")) return cmd_info();
    if (argc >= 3 && !strcmp(argv[1], "stream")) {
        int grab = 0, keepalive = 0;
        for (int i = 3; i < argc; i++) {
            if (!strcmp(argv[i], "grab")) grab = 1;
            else if (!strcmp(argv[i], "keepalive")) keepalive = 1;
        }
        return cmd_stream(argv[2], grab, keepalive);
    }
    if (argc >= 3 && !strcmp(argv[1], "watch")) return cmd_watch(argv[2]);
    fprintf(stderr, "usage: %s info | stream DEV [grab] [keepalive] | watch DEV\n", argv[0]);
    return 2;
}
