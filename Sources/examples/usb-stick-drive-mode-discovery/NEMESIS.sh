#!/system/bin/sh
# Phase 1 of docs/drive-mode-memory-package.md: watch what the head unit sees while the
# driver presses the drive-mode button. Read-only. It only reads and lists, and it writes
# only to a new folder on this stick. It never sets a property, sends a key or an intent,
# or touches the vehicle.
#
# Runs for about two minutes. The dialog at the end means it has finished.

export PATH=/sbin:/vendor/bin:/system/sbin:/system/bin:/system/xbin
export LD_LIBRARY_PATH=/vendor/lib:/system/lib

SAMPLES=60
INTERVAL=2

OUT="drive-mode-discovery-$(date +%Y%m%d-%H%M%S 2>/dev/null)-$$"
mkdir "$OUT" 2>/dev/null || exit 1
mkdir "$OUT/props" 2>/dev/null || exit 1

# File names and times only, never contents. A changed time points at where a mode is stored.
snapshot() {
    _name="$1"
    ls -l /data > "$OUT/$_name-data.txt" 2>&1
    ls -lR /data/property > "$OUT/$_name-property.txt" 2>&1
    ls -lR /efs > "$OUT/$_name-efs.txt" 2>&1
    ls -l /dev/socket > "$OUT/$_name-sockets.txt" 2>&1
    ps > "$OUT/$_name-ps.txt" 2>&1
}

snapshot before

# No -c: reading the log must not clear it. The radio buffer is left out, as it holds phone traffic.
logcat -v time -b main -b system -b events > "$OUT/logcat.txt" 2>&1 &
_logcat=$!

_i=0
while [ "$_i" -lt "$SAMPLES" ]; do
    echo "$_i $(date +%H:%M:%S 2>/dev/null)" >> "$OUT/times.txt"
    getprop > "$OUT/props/$_i.txt" 2>&1
    sleep "$INTERVAL"
    _i=$((_i + 1))
done

kill "$_logcat" 2>/dev/null
snapshot after
sync
exit 0
