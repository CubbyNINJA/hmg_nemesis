/* Runtime half of shim.h, built without the forced include. */
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define MAX_ARGS 64

/* Present only in a --coverage build. Daemons die by signal and exec, so exit never flushes. */
extern void __gcov_dump(void) __attribute__((weak));

static void flush_coverage(void) {
    if (__gcov_dump) __gcov_dump();
}

static void on_term(int sig) {
    (void)sig;
    flush_coverage();
    _exit(0);
}

__attribute__((constructor)) static void shim_init(void) {
    signal(SIGTERM, on_term);
}

const char *shim_path(const char *path) {
    /* A ring, because one call can hold several translated paths at once. */
    static char ring[8][1024];
    static int next;
    char *out;
    if (!path) return path;
    if (!strcmp(path, "/proc/mounts")) return TEST_ROOT "/proc_mounts";
    if (strncmp(path, "/system/", 8) && strncmp(path, "/data/", 6) && strncmp(path, "/cache/", 7))
        return path;
    out = ring[next];
    next = (next + 1) % 8;
    snprintf(out, sizeof(ring[0]), "%s%s", TEST_ROOT, path);
    return out;
}

uid_t shim_geteuid(void) {
    const char *value = getenv("NEMESIS_TEST_EUID");
    return value ? (uid_t)atoi(value) : 0;
}

static void append_line(const char *name, const char *text) {
    char path[1024];
    int fd;
    snprintf(path, sizeof(path), "%s/%s", TEST_ROOT, name);
    fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) return;
    if (write(fd, text, strlen(text)) < 0) {
        /* A test that needs this line will fail on its absence. */
    }
    close(fd);
}

int shim_setid(const char *kind, unsigned int id) {
    char line[64];
    snprintf(line, sizeof(line), "%s %u\n", kind, id);
    append_line("ids.log", line);
    return 0;
}

int shim_mount(const char *dev, const char *mnt, const char *fstype, unsigned long flags) {
    char line[512];
    snprintf(line, sizeof(line), "%s %s %s %lu\n", dev ? dev : "", mnt ? mnt : "", fstype ? fstype : "", flags);
    append_line("mounts.log", line);
    return 0;
}

/* Tests do not run as root. A file named chown_fails makes it fail as it would on a broken unit. */
int shim_chown(const char *path, uid_t owner, gid_t group) {
    (void)path;
    (void)owner;
    (void)group;
    return access(TEST_ROOT "/chown_fails", F_OK) == 0 ? -1 : 0;
}

/* Command strings hold absolute unit paths, so those move under TEST_ROOT too. */
static char *rewrite(const char *text) {
    static const char *const from[] = { "/system/bin/", "/system/xbin/" };
    size_t cap = strlen(text) * 2 + strlen(TEST_ROOT) * 8 + 64;
    char *out = malloc(cap);
    size_t used = 0;
    const char *p = text;
    while (*p && used + strlen(TEST_ROOT) + 2 < cap) {
        size_t i;
        int hit = 0;
        for (i = 0; i < 2; i++) {
            if (!strncmp(p, from[i], strlen(from[i])) &&
                (p == text || strncmp(p - strlen(TEST_ROOT), TEST_ROOT, strlen(TEST_ROOT)))) {
                hit = 1;
                break;
            }
        }
        if (hit) {
            memcpy(out + used, TEST_ROOT, strlen(TEST_ROOT));
            used += strlen(TEST_ROOT);
        }
        out[used++] = *p++;
    }
    out[used] = 0;
    return out;
}

static const char *exec_target(const char *path) {
    if (!strcmp(path, "/system/bin/sh")) return "/bin/sh";
    if (!strncmp(path, "/system/", 8)) return shim_path(path);
    return path;
}

int shim_execv(const char *path, char *const argv[]) {
    flush_coverage();
    return execv(exec_target(path), argv);
}

int shim_execl(const char *path, const char *arg0, ...) {
    char *argv[MAX_ARGS];
    va_list ap;
    int n = 0;
    const char *arg = arg0;
    va_start(ap, arg0);
    while (arg && n < MAX_ARGS - 1) {
        argv[n++] = rewrite(arg);
        arg = va_arg(ap, const char *);
    }
    va_end(ap);
    argv[n] = NULL;
    return shim_execv(path, argv);
}

void shim_exit(int code) {
    flush_coverage();
    _exit(code);
}

/* Stands in for bionic's property store: one file per property. */
int __system_property_get(const char *name, char *value) {
    char path[1024];
    FILE *f;
    size_t n;
    value[0] = 0;
    snprintf(path, sizeof(path), "%s/props/%s", TEST_ROOT, name);
    f = fopen(path, "r");
    if (!f) return 0;
    n = fread(value, 1, 91, f);
    fclose(f);
    while (n && (value[n - 1] == '\n' || value[n - 1] == '\r')) n--;
    value[n] = 0;
    return (int)n;
}

int __system_property_set(const char *name, const char *value) {
    char path[1024];
    FILE *f;
    snprintf(path, sizeof(path), "%s/props/%s", TEST_ROOT, name);
    f = fopen(path, "w");
    if (!f) return -1;
    fputs(value, f);
    fclose(f);
    return 0;
}
