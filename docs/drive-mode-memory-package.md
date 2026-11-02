# Guide: Drive-Mode Memory Package

Goal: a NEMESIS package that remembers the last-used custom driving profile and re-applies it
after a power cycle, instead of the car defaulting back to Eco/Normal.

> **Scope gate — read before any implementation.** `SCOPE.md` bars a package from actuating
> powertrain control, and Strategy A in this guide commands a drive-mode change. This document is
> a discovery and design record only: nothing in Phase 2A may be built or shipped until the
> maintainer has ruled on whether a drive-mode request made through the same interface the stock
> UI uses falls inside `SCOPE.md`. The same gate covers anything else that changes the mode: key
> injection in Phase 2B, and the `setprop` and `input keyevent` tests in Phase 1.2. Observing the
> mode, logging it, and showing the driver a reminder are unaffected.

> **Read first:** the drive-mode selector on Gen5W is a *vehicle* function. The head unit (HU)
> is a passenger on the CAN bus — it may only *observe* the mode, or it may be able to
> *request* a change. Which strategy below is viable depends on facts that must be gathered on
> a real unit in Phase 1. Do not skip Phase 1; the package design branches on its results.

---

## Phase 0 — Prerequisites

- [ ] A Gen5W unit with NEMESIS installed (`nemesis pkg-list` works over adb or USB run script).
- [ ] adb access (install the `adb-tcp` catalog package if needed) or a USB stick for
      `type=execute` run scripts.
- [ ] Root shell on the unit: `adb shell` → `nemesis root` (or `nemesis root -c '<cmd>'`).
- [ ] The car itself available for power-cycle testing (IG off → wait for sleep → IG on).
- [ ] Read `docs/creating-a-package.md`, `04-Packages.md`, and `AGENTS.md` §3 (toolbox-only
      shell, no busybox/awk, `/data/local/tmp` wiped on reboot).

---

## Phase 1 — Discovery (the critical phase)

Every question must be answered with evidence from the unit. Record answers in a notes file
(e.g. `docs/notes/drive-mode-discovery.md`); the package design depends on them.

`Sources/examples/usb-stick-drive-mode-discovery/` answers 1.1 without a shell. It only reads and
lists, and it saves to the stick.

1. Copy `NEMESIS.data` and `NEMESIS.sh` to the top level of a blank FAT32 or exFAT stick.
2. Park the car with the parking brake on, and turn it on so the drive-mode button works.
3. Insert the stick and tap **Yes**. The capture runs for about two minutes.
4. Press the drive-mode button once every 15 seconds or so, through every mode, including a custom
   profile if the car has one. Write down the order and roughly when you started.
5. A dialog appears when it finishes. Then remove the stick.
6. On your PC, run `python3 analyze.py <drive-mode-discovery-folder>`, then read `summary.txt`.

It masks serial numbers, MAC addresses and VINs in `summary.txt`. The raw files are not masked,
and the log can hold phone and network names, so share the summary rather than the folder.

### 1.1 How does the HU learn the current drive mode?

With the car running, cycle the drive-mode button and watch what changes on the HU:

```sh
nemesis root -c 'getprop'                       # dump all props; diff before/after a press
nemesis root -c 'logcat -c'                     # clear, then press the button, then:
nemesis root -c 'logcat -d'                     # look for broadcast/intent lines
```

Look specifically for:
- **A system property** that flips when the mode changes (often under `sys.*` or a vendor
  prefix, mirrored from MICOM). Test: `getprop <candidate>` before/after a button press.
- **An Android broadcast** — `logcat` lines containing `DRIVE`, `MODE`, `MDPS`, `am_broadcast`.
  Capture the exact action string and extras.
- **A file** that changes — compare `ls -l` timestamps in vendor dirs under `/data/` and
  `/efs/` (look only; do not edit yet) before/after a mode change.

Record: *mechanism (prop / broadcast / file), exact name, value per mode, latency after press.*

### 1.2 Can the HU change the mode?

This determines feasibility. Check in order:

1. **Stock "remember mode" setting?** If any trim/settings menu already offers it, find what it
   writes — replicate that.
2. **Writable prop/command?** Does `setprop <candidate> <value>` change the cluster display?
   (Usually not — MICOM owns the bus — but verify.)
3. **Vendor bridge daemon?** `nemesis root -c 'ps'` and look for `micom`, `can`, `vehicle`
   processes; inspect their config/socket dirs for a request interface.
4. **Input injection fallback:** if a UI screen or the cluster reacts to a key event, find the
   keycode: `input keyevent <code>` while watching the cluster.

Record exactly one outcome:
- **A — writable**: something on the HU can command a mode change → Strategy A (Phase 2A).
- **B — read-only**: the HU can only observe → Strategy B (Phase 2B).
- **C — unknown**: keep digging. Do not build the package yet.

### 1.3 Boot behavior

- How long after IG-on does the mode signal first appear (prop set / broadcast fired)?
- Does the stock default (Eco/Normal) get applied by the vehicle at every boot, or is the
  last mode sometimes already retained? (Some firmware retains it until full sleep; note the
  sleep timeout — this defines how long the power-cycle test must wait.)
- Is there a window where the HU is up but MICOM/CAN traffic is not? This decides service
  start strategy: `kind=sh` services run without zygote; broadcast receivers and
  `getprop`-of-mirrored-values may need Android up.

---

## Phase 2 — Design the package

Both strategies share the same skeleton: a **watcher** that records the last-used mode to
persistent storage, and a **restorer** that re-applies it at boot. Only the restore mechanism
differs.

Persistent state lives at `/data/nemesis/apps/drive.mode/memory/last_mode` (plain text, one
value). It survives power cycles.

### Phase 2A — Strategy A: HU can command the mode

Package `drive.mode` with one service, `kind=sh`, `mode=restart`:

```text
Sources/packages/drive-mode/
  NEMESIS.data
  svc/keeper.sh        # watch loop + boot restore in one script
  scripts/post.sh      # ensure memory dir exists; capture initial state
  scripts/preun.sh     # optional cleanup (services already stopped by CLI)
```

`svc/keeper.sh` logic (toolbox-only):

```sh
#!/system/bin/sh
export PATH=/sbin:/vendor/bin:/system/sbin:/system/bin:/system/xbin
export LD_LIBRARY_PATH=/vendor/lib:/system/lib

MEM=/data/nemesis/apps/drive.mode/memory
mkdir -p "$MEM"

# 1. Restore: wait until the mode signal is alive, then apply the saved mode.
while [ -z "$(getprop <MODE_PROP>)" ]; do sleep 2; done
LAST=$(cat "$MEM/last_mode" 2>/dev/null)
if [ -n "$LAST" ]; then
    <APPLY_COMMAND> "$LAST"   # setprop / am broadcast / vendor binary from Phase 1.2
fi

# 2. Watch: persist every change.
PREV=$(getprop <MODE_PROP>)
while true; do
    CUR=$(getprop <MODE_PROP>)
    if [ "$CUR" != "$PREV" ]; then
        echo "$CUR" > "$MEM/last_mode"
        PREV="$CUR"
    fi
    sleep 2
done
```

Manifest:

```ini
[nemesis]
version=1.0
id=drive.mode
name=Drive Mode Memory
types=service
postinstall=scripts/post.sh

[service.0]
name=keeper
kind=sh
path=svc/keeper.sh
uid=root
mode=restart
```

Design notes:
- Use `kind=sh` — it starts before/independent of zygote, so restore happens as early as the
  mode signal allows.
- **Debounce the restore**: if the vehicle applies its stock Eco/Normal default *after* your
  restore runs, you will fight it. If Phase 1.3 shows a late stock default, delay the restore
  or apply twice (immediately + once after the default lands). Record what worked.
- A `restart` service that crash-loops is disabled after 5 rapid deaths — every command must
  be defensive (`2>/dev/null`, existence checks); the loop body must never exit.
- If the observed signal is a **broadcast** rather than a property, the watcher must be a
  small receiver. Simplest toolbox-friendly route: a `kind=instance` helper only if Android
  is guaranteed up; otherwise poll whatever mirror (prop/file) the broadcast writes to.

### Phase 2B — Strategy B: HU can only observe

The watcher half is identical (record last mode). The restore half must go through whatever
actuation exists:
- **Key-event injection** (if Phase 1.2 found a keycode): at boot, read last mode, compute
  presses needed from the default, `input keyevent <code>` with delays between presses.
  Fragile — verify each press landed by re-reading the observed signal before the next.
  Note: `input` needs Android up, so gate the restore on `sys.boot_completed=1`.
- **No actuation at all**: the package degenerates to mode logging only. The honest outcome
  is then: not implementable from the HU on this firmware. Say so in the package README
  rather than shipping something that silently does nothing.

### If the mode is stored in an editable file owned by a stock app

That is a live-file edit: follow the backup/restore pattern from `Sources/packages/adb-tcp/`
(snapshot to `/data/nemesis/backup/drive.mode/` in `postinstall`, restore in `preuninstall`).
Prefer Strategy A/B over file patching unless the file *is* the stock persistence mechanism.

---

## Phase 3 — Build, install, verify

### 3.1 Build

```bash
python3 Sources/tools/make_nemesis_package.py Sources/packages/drive-mode -o drive.mode.zip
```

(The builder does no validation — double-check every `path=` in `NEMESIS.data` points at a
real file, scripts are LF line endings, and shebangs are `#!/system/bin/sh`.)

### 3.2 Install

USB stick layout:

```text
NEMESIS.data          # [nemesis] version=1.0 / type=install / path=packages/drive.mode.zip
packages/drive.mode.zip
```

Or via CLI: `nemesis install /storage/usb0/packages/drive.mode.zip`
(`--update` for reinstalls).

### 3.3 Verify — in this order

1. `nemesis pkg-list` shows `drive.mode|Drive Mode Memory|0|service`.
2. `nemesis service status drive.mode/keeper` is running.
3. `nemesis log service drive.mode/keeper` shows the watcher alive (add an `echo` per loop
   iteration during development, remove for release).
4. **Functional test:** change mode by hand → confirm `memory/last_mode` updated:
   `nemesis root -c 'cat /data/nemesis/apps/drive.mode/memory/last_mode'`.
5. **Power-cycle test:** IG off → wait past the sleep timeout found in Phase 1.3 → IG on.
   Confirm the car comes up in the remembered mode, not Eco/Normal. Repeat 3–5 times;
   timing-dependent restores often pass once and fail intermittently.
6. **Uninstall test:** `nemesis uninstall drive.mode` → power cycle → car behaves stock
   (defaults to Eco/Normal, nothing errors). No leftovers in `/data/nemesis/backup/`.

---

## Phase 4 — Risks and open questions

| Risk | Mitigation |
|------|-----------|
| Restore fights the stock default (race at boot) | Debounce / double-apply per Phase 2A notes; measure actual timings in Phase 1.3 |
| Watcher crash-loops → disabled after 5 deaths | Defensive scripting; test with the service log after several power cycles |
| Mode command touches safety-relevant vehicle systems | Only ever use the *same* interface the stock UI uses; never write CAN directly; get explicit sign-off before shipping any actuation you did not fully verify |
| Firmware-specific signals | Record firmware/model in the package README and the catalog Compatibility column (like `projection-tbt` does for 260507) |
| Unknown: whether "custom profile" is even visible to the HU | Phase 1.1 must confirm the HU sees custom-profile selection, not just Sport/Eco/Normal |

---

## Checklist

1. [ ] Phase 0 prerequisites met (unit, adb/USB, root shell, car for power-cycle tests)
2. [ ] Phase 1.1: mode observation mechanism identified and recorded
3. [ ] Phase 1.2: actuation outcome recorded (A writable / B read-only / C unknown)
4. [ ] Phase 1.3: boot timing + sleep timeout recorded
5. [ ] Phase 2: strategy chosen; keeper script written against recorded names/commands
6. [ ] Phase 3.1–3.2: zip built and installed
7. [ ] Phase 3.3: all six verification steps pass, power-cycle test repeated ≥3×
8. [ ] Uninstall leaves the car stock
9. [ ] README + catalog Compatibility entry note the tested firmware/model

