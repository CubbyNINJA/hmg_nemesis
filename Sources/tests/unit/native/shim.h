/* Forced into every native source when building the host test binary.
 * Moves the unit's paths under TEST_ROOT and stubs what needs root or Android.
 * Production sources stay unchanged. */
#ifndef NEMESIS_TEST_SHIM_H
#define NEMESIS_TEST_SHIM_H

#include <dirent.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "nemesis.h"

#undef NEMESIS_ROOT
#undef NEMESIS_APPS
#undef NEMESIS_TMP
#undef NEMESIS_LOGS
#undef NEMESIS_LOGFILE
#undef NEMESIS_BINDIR
#undef NEMESIS_PATH
#undef PACKAGES_DB
#undef SERVICES_DB
#undef PIDS_DIR
#undef SESSION_DIR
#undef USB0
#undef USB0_MNT
#undef USB1
#undef USB1_MNT
#undef USB2
#undef USB2_MNT
#undef BIN_PATH
#undef INSTALL_RECOVERY
#undef QB_RUNTIME
#undef MANAGER_APK
#undef WD_PIDFILE
#undef SM_PIDFILE
#undef SU_SOCK_DEV
#undef SU_SOCK_DATA

#define NEMESIS_ROOT TEST_ROOT "/data/nemesis"
#define NEMESIS_APPS TEST_ROOT "/data/nemesis/apps"
#define NEMESIS_TMP TEST_ROOT "/data/local/tmp/nemesis"
#define NEMESIS_LOGS TEST_ROOT "/data/nemesis/logs"
#define NEMESIS_LOGFILE TEST_ROOT "/data/nemesis/nemesis.log"
#define NEMESIS_BINDIR TEST_ROOT "/data/nemesis/bin"
#define NEMESIS_PATH TEST_ROOT "/data/nemesis/bin:" TEST_ROOT "/system/bin:/usr/bin:/bin"
#define PACKAGES_DB TEST_ROOT "/data/nemesis/packages.db"
#define SERVICES_DB TEST_ROOT "/data/nemesis/services.db"
#define PIDS_DIR TEST_ROOT "/data/nemesis/pids"
#define SESSION_DIR TEST_ROOT "/data/nemesis/usb-session"
#define USB0 TEST_ROOT "/storage/usb0"
#define USB0_MNT TEST_ROOT "/mnt/media_rw/usb0"
#define USB1 TEST_ROOT "/storage/usb1"
#define USB1_MNT TEST_ROOT "/mnt/media_rw/usb1"
#define USB2 TEST_ROOT "/storage/usb2"
#define USB2_MNT TEST_ROOT "/mnt/media_rw/usb2"
#define BIN_PATH TEST_ROOT "/system/xbin/nemesis"
#define INSTALL_RECOVERY TEST_ROOT "/system/etc/install-recovery.sh"
#define QB_RUNTIME TEST_ROOT "/system/etc/qb_runtime.sh"
#define MANAGER_APK TEST_ROOT "/system/app/NEMESIS.apk"
#define WD_PIDFILE TEST_ROOT "/data/nemesis/watchdog.pid"
#define SM_PIDFILE TEST_ROOT "/data/nemesis/svcmgr.pid"
#define SU_SOCK_DEV TEST_ROOT "/dev/nemesis.sock"
#define SU_SOCK_DATA TEST_ROOT "/data/nemesis/root.sock"

const char *shim_path(const char *path);
uid_t shim_geteuid(void);
int shim_setid(const char *kind, unsigned int id);
int shim_mount(const char *dev, const char *mnt, const char *fstype, unsigned long flags);
int shim_execl(const char *path, const char *arg0, ...);
int shim_execv(const char *path, char *const argv[]);
void shim_exit(int code) __attribute__((noreturn));

/* Names that take a literal unit path. Glibc's declarations are already included above. */
#define open(path, ...) open(shim_path(path), __VA_ARGS__)
#define fopen(path, mode) fopen(shim_path(path), mode)
#define unlink(path) unlink(shim_path(path))
#define opendir(path) opendir(shim_path(path))
#define geteuid() shim_geteuid()
#define setuid(id) shim_setid("uid", (unsigned int)(id))
#define setgid(id) shim_setid("gid", (unsigned int)(id))
#define mount(dev, mnt, fstype, flags, data) shim_mount(dev, mnt, fstype, flags)
#define execl shim_execl
#define execv shim_execv
#define _exit shim_exit

#endif
