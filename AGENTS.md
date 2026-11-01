# AGENTS.md — Working in hmg_nemesis

Everything an AI agent needs to work within this repository and to **create NEMESIS packages**.

---

## 1. What this project is

**NEMESIS** is a diagnostics and custom package-manager interface for Android-based **Gen5W AVNT
head units** (Kia EV6 / Hyundai Ioniq 5 generation, KitKat-era Android, `S5W_M` firmware only).

Once present on a head unit (HU), NEMESIS acts as a **Debugging Execution Interface**:

- a root CLI at `/system/xbin/nemesis`
- a Manager app at `/system/app/NEMESIS.apk` (`com.nemesis.manager/.MainActivity`)
- a watchdog / service manager / USB handler daemon stack
- a **package system** (zips installed under `/data/nemesis/apps/<id>/`)
- a **USB run-script system** (`NEMESIS.data` descriptors + `NEMESIS.sh` one-shot scripts)

This repo contains: the native CLI (C), the Manager APK (Java), the shipped packages, the package
builder, examples, host-side tests, and the documentation set. License: GNU AGPL 3.0.

### Scope for agent work

`SCOPE.md` states ownership, permitted use, and the safety boundaries this project observes. Read
it first; the out-of-scope list there (no safety-system actuation, no direct CAN writes, no
anti-theft/key systems, no data exfiltration) applies to every package and script written here.

Documentation scope:

- The canonical docs for work in this repo are `02-Debugging-Execution-Interface.md`,
  `04-Packages.md`, `05-Run-Scripts.md`, `06-Cloud-Store-API.md`, `07-Submit-a-Package.md`, `README.md`, `SCOPE.md`,
  `docs/creating-a-package.md`, `docs/testing.md`, `docs/oem-source-release.md`,
  `docs/source-release-notes.md`, and `Sources/usb_bundle/README.md` + `DEVICE_TEST.md`.
- `01-DSP-OTA-troubleShooter.md` and `03-Install-and-Uninstall.md` cover the install path and are
  not part of this checkout. They are out of scope for package work — do not link to them in new
  docs. Nothing in package authoring depends on them.
- Use standard Android system-administration terminology. The working set for nearly all tasks is
  package structure, runtime automation scripts, and UI layouts under `Sources/`.

---

## 2. Repository layout

```text
hmg_nemesis/
├── AGENTS.md                    ← this file
├── SCOPE.md                     ← ownership, permitted use, safety boundaries
├── README.md                    ← project overview, install guidance, doc index
├── LICENSE                      ← AGPL 3.0
├── ota_package.tar              ← prebuilt installer artifact (do not hand-edit)
├── 02-Debugging-Execution-Interface.md   ← runtime: CLI, Manager, sockets, boot
├── 04-Packages.md                        ← package format reference (INI, hooks, apps, services)
├── 05-Run-Scripts.md                     ← USB NEMESIS.data / NEMESIS.sh run-script reference
├── docs/
│   ├── creating-a-package.md            ← step-by-step package authoring guide
│   ├── testing.md                       ← how to run tests, coverage, and mutation testing
│   ├── oem-source-release.md            ← what the Mobis source download holds and how to search it
│   ├── source-release-notes.md           ← oddities, aging components and licensing traps in that download
│   └── jev-hooks-replication-prompt.md  ← reusable prompt for Claude Code hook systems
└── Sources/
    ├── Makefile                    ← top-level dev build (expects a Linux ext4 tree)
    ├── make_troubleshooter_tar.py  ← builds usb_bundle/ota_package_troubleshooter.tar
    ├── native/                     ← C source of the `nemesis` CLI
    │   ├── Makefile                ← Android NDK r21e, armv7a, API 19, -pie, links -lz
    │   └── main.c util.c ini.c zip.c pkg.c svc.c usb.c su.c nemesis.h
    ├── manager/                    ← Java source of NEMESIS.apk
    │   ├── Makefile                ← aapt/javac/d8/jarsigner/zipalign, android.jar API 30
    │   ├── AndroidManifest.xml, allow-sha1.security
    │   ├── src/com/nemesis/manager/*.java
    │   └── res/ (layout, values, drawable, color)
    ├── packages/                   ← shipped package sources
    │   ├── usb-handler/            ← locked core service (USB mount watcher)
    │   ├── adb-tcp/                ← id `adb.tcp`
    │   ├── wireless-projection/    ← id `wireless.projection`
    │   └── projection-tbt/         ← id `projection.tbt` (firmware 260507)
    ├── examples/                   ← worked examples (see §5)
    ├── boot/                       ← install-recovery.sh, qb_hook.sh (watchdog boot hooks)
    ├── tools/make_nemesis_package.py ← the package builder (stdlib Python 3)
    ├── tests/                      ← host unit tests (unit/), host checks, QEMU console-flow tests
    └── usb_bundle/                 ← installer bundle scripts, verify_tars.sh, DEVICE_TEST.md
```

**Known inconsistency:** `Sources/Makefile`'s `packages` target references
`packages/voicehandler-vr-gates` and `packages/busybox`, which are not in this checkout (only
their `usb-stick-*` examples exist). The Makefile also hardcodes developer paths
(`/home/xwtk/...`, `/mnt/c/Users/gmirz/...`). Treat it as reference; run the builder directly.

---

## 3. Target runtime environment (write scripts for THIS)

Every script that runs on the head unit (package hooks, app/service scripts, USB run scripts)
executes in a **KitKat-era Android shell** with hard constraints:

| Constraint | Detail |
|------------|--------|
| Shell | `/system/bin/sh` (mksh). Shebang: `#!/system/bin/sh` |
| Tools | **Toolbox only**: `sh cp mv rm mount logcat am pm dd getprop setprop sleep mkdir chmod ls cat echo id kill ps date`. Do **not** assume `busybox`, `awk`, `sed`, `grep -P`, `unzip`, `curl`, `bash` unless the package ships them. |
| PATH | Privileged commands get `/data/nemesis/bin:/sbin:/vendor/bin:/system/sbin:/system/bin:/system/xbin`. Scripts typically `export PATH=/sbin:/vendor/bin:/system/sbin:/system/bin:/system/xbin` and `export LD_LIBRARY_PATH=/vendor/lib:/system/lib`. |
| Remounting `/system` | Use `nemesis remount rw` / `nemesis remount ro`. Toolbox `mount -o remount,rw /system` **fails even as root** (kernel refuses to drop `nosuid`). `/data/nemesis/bin/mount` is a wrapper that is first on PATH inside hooks and `nemesis root`, so a bare `mount -o remount,rw /system` there is intercepted and works. |
| Storage that survives reboot | `/data/nemesis/` (packages, DBs, logs, backups). **`/data/local/tmp/` is cleared on reboot** — use it only for staging. |
| USB mounts | `/storage/usb0` (also `usb1`/`usb2`, `/mnt/media_rw/usb*`). Manager's "Run USB NEMESIS.sh" is usb0-only. |
| Android may be **down** | `kind=sh` services and USB `execute`/`install` work without zygote. `kind=instance` services, `type=open`, and `pm install` need Android up. |
| APK signing gate | While `sys.boot_completed` is `1`, `pm` rejects non-allowlisted signers with `INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES`. The NEMESIS installer works around this itself (temporarily sets `sys.boot_completed=0`); package authors do not need to. |
| Privilege | Hooks and services run as **root** by default, or `system` (uid/gid 1000) with `uid=system`. `/system` is `nosuid`, so privilege is provided over a local UNIX domain socket IPC channel (`/dev/nemesis.sock` / `/data/nemesis/root.sock`); there is no network listener. See `SCOPE.md` for the trust implications. |

### CLI reference (`/system/xbin/nemesis`)

```text
nemesis install [--update] <zip>
nemesis uninstall <package-id>
nemesis pkg-list                      # lines: id|name|locked|types
nemesis pkg-info <zip>
nemesis pkg-launch <package-id>
nemesis root [uid] [-c command]       # any UID may use; no -c = interactive root shell
nemesis remount [rw|ro]
nemesis service start|status|stop|list <package-id>/<name>
nemesis log dump|logcat|dmesg|service <package-id>/<name>
nemesis wipe --confirm                # removes NEMESIS and everything; guarded
nemesis watchdog | svcmgr | usb-handler   # long-running daemons, started at boot only
```

Key on-unit paths:

```text
/data/nemesis/apps/<id>/              unpacked package tree
/data/nemesis/packages.db             id|name|locked|types
/data/nemesis/services.db             registered services
/data/nemesis/backup/<id>/            snapshots of live files a package modifies
/data/nemesis/logs/<id>/<name>.log    sh-service stdout/stderr (survives reboot)
/data/nemesis/nemesis.log             main log
/data/nemesis/logs/usb-session.log    USB handler decisions
/data/local/tmp/nemesis/stage/<id>/   install staging (cleared on reboot)
```

---

## 4. Creating a package (the core workflow)

Canonical references: `04-Packages.md` and `docs/creating-a-package.md`.

### 4.1 Directory

A package is a directory whose **root** contains `NEMESIS.data` (INI). The builder zips the
whole tree with paths relative to the package folder.

```text
my-tool/
  NEMESIS.data         # required, at the root
  bin/tool.sh          # optional launchable app script
  svc/daemon.sh        # optional long-running service
  scripts/pre.sh       # optional lifecycle hooks (pre/post/preun/postun)
  scripts/post.sh
  scripts/preun.sh
  scripts/postun.sh
  files/...            # optional bundled files (APKs, XMLs, patched files)
```

### 4.2 `NEMESIS.data` — `[nemesis]`

| Key | Required | Meaning |
|-----|----------|---------|
| `id` | yes | Install id, unique per unit. Max 64 chars: letters, digits, `.`, `_`, `-`. No leading `.`, no `..`. Same id installed again = **update** (needs `--update`; USB passes it only while Android is down). |
| `name` | recommended | Display name (defaults to `id`). |
| `version` | recommended | Free-form version string. |
| `types` | recommended | Comma list shown in `pkg-list`, e.g. `application,service`. |
| `preinstall` | no | Runs **before** the tree is copied; cwd = staging dir. |
| `postinstall` | no | Runs **after** copy, APK install, service registration; cwd = `/data/nemesis/apps/<id>`. |
| `preuninstall` | no | Runs after services stop, before APKs removed / tree deleted. |
| `postuninstall` | no | Copied to tmp first so it survives tree deletion, then run. |

All hooks run as **root**. A non-zero `preinstall` stops the install. A non-zero `postinstall` makes `nemesis install` return failure, but the package stays installed and its services start.

### 4.3 `[app.N]` — launchables (`[app.0]`, `[app.1]`, …)

`nemesis pkg-launch <id>` walks them in order: `component` activity first, else `package`
launcher activity, else `kind=sh` script. A `kind=instance` service of that package also starts.

| Key | Meaning |
|-----|---------|
| `kind` | `sh` = launchable script; `apk` = file installed with `pm install -r` |
| `path` | Relative path inside the package |
| `package` | Android package name (used to launch and to `pm uninstall`) |
| `component` | Activity `package/class`; preferred by `pkg-launch` |
| `uid` | `root` (default) or `system` |

`kind=apk` notes: KitKat `pm` prints `Success`/`Failure [...]` but always exits 0, so NEMESIS
parses the text. On `INCONSISTENT_CERTIFICATES` for an already-present package, NEMESIS
uninstalls and retries once. When Android is down the APK stays in the tree and `pkg-launch`
tries `pm install` later.

### 4.4 `[service.N]` — services (`[service.0]`, `[service.1]`, …)

Registered as `<package-id>/<name>` (names can't collide across packages).

| Key | Meaning |
|-----|---------|
| `name` | Local name (required) |
| `kind` | `sh` (default) — `/system/bin/sh` on `path`, no zygote needed; `instance` — `am start`/`am startservice`, needs Android up, deferred while down |
| `path` | Script relative to package, or component for `instance` |
| `uid` | `root` (default) or `system` (uid/gid 1000) |
| `mode` | `restart` (restart on death; ordinary packages give up after 5 rapid deaths) / `background` (start once) / `once` (wait for exit) |

`sh` service stdout/stderr → `/data/nemesis/logs/<id>/<name>.log` (survives reboot).

### 4.5 Install order (what the CLI does)

1. Read `NEMESIS.data` from zip; reject bad `id`.
2. Refuse if locked and already installed.
3. Require `--update` if the id exists. USB passes it only while Android is down.
4. Extract to `/data/local/tmp/nemesis/stage/<id>`. A member whose path contains `..` rejects the whole
   package, so extraction cannot escape the staging directory.
5. `preinstall` → copy tree to `/data/nemesis/apps/<id>/` → `pm install` APKs → register
   services → write `packages.db` → `postinstall` → start services.

Uninstall: stop services → `preuninstall` → `pm uninstall` APKs → `postuninstall` → delete tree.
Locked packages refuse; `nemesis wipe --confirm` removes everything including locked.

### 4.6 Build, deploy, verify

```bash
# Build (stdlib Python 3 only)
python3 Sources/tools/make_nemesis_package.py Sources/packages/my-tool -o my.tool.zip

# Verify on the unit
nemesis pkg-info my.tool.zip
nemesis install /storage/usb0/packages/my.tool.zip     # --update if reinstalling
nemesis pkg-list
nemesis log dump
```

**Backup/restore rule:** any package that modifies live files on the unit must snapshot them
into `/data/nemesis/backup/<id>/` during `postinstall` **before** changing them, and restore
from that snapshot in `preuninstall` (restore the unit's *own* files, never shipped stock
copies). Delete the backup only after a successful restore. `adb-tcp` and `wireless-projection`
are the reference implementations.

---

## 5. USB descriptors and run scripts

Reference: `05-Run-Scripts.md`. The USB Handler (locked `usb-handler` service) reacts **only**
to a `NEMESIS.data` INI at the USB root. A bare `NEMESIS.sh` does nothing on insert (Manager
can still run it manually via "Run USB NEMESIS.sh", usb0 only).

| `type` | `path` | Android up | Android down |
|--------|--------|------------|--------------|
| `execute` | required, relative | Yes/No dialog, then run as root, cwd = USB root | runs without dialog (recovery) |
| `install` | required, relative zip | Yes/No dialog, then `nemesis install` | installs without dialog |
| `open` | optional | starts Manager | skipped |

Descriptor example (`type=install`):

```ini
[nemesis]
version=1.0
type=install
path=packages/my.tool.zip
```

`path` rules: non-empty, no leading `/`, no `..`, no shell metacharacters
(`; | & \ ` $ ' " ( ) < >`, newlines). Dialog timeout: 60 s. The handler re-arms when the
stick unmounts or `NEMESIS.data`'s identity (inode/size/mtime) changes.

Run-script template (`NEMESIS.sh`):

```sh
#!/system/bin/sh
export PATH=/sbin:/vendor/bin:/system/sbin:/system/bin:/system/xbin
export LD_LIBRARY_PATH=/vendor/lib:/system/lib
# commands; keep artifacts on USB or /data/nemesis (not /data/local/tmp)
exit 0
```

Exit 0 → success dialog; non-zero → failure dialog.

---

## 6. Shipped packages and examples

### `Sources/packages/` (real packages)

| Dir | `id` | Types | Notes |
|-----|------|-------|-------|
| `usb-handler` | `usb-handler` | service | **Locked** core service; one `[service.0]`, `kind=sh`, `mode=restart`. Seeded by NEMESIS; never editable/removable except by wipe. |
| `adb-tcp` | `adb.tcp` | application | Status app + hooks that snapshot/edit a live Engineering Mode file, restore on uninstall. |
| `wireless-projection` | `wireless.projection` | application | Status app + hooks that drop feature-permission XMLs (`files/permissions/`), restore unit's own files on uninstall. |
| `projection-tbt` | `projection.tbt` | application | Stages APKs/`.so` from `files/`. Built for firmware **260507**. |

### `Sources/examples/` (templates — start here for new packages)

| Dir | What it shows |
|-----|---------------|
| `package-hello` | Minimal package: one `sh` app, one `sh` service (`mode=restart`), all four hooks. **Copy this as the template for new packages.** |
| `package-hello-v2` | Same, service uses `mode=background`. |
| `execute-hello` | Minimal `NEMESIS.data` + `NEMESIS.sh` run-script pair. |
| `open-manager` | `type=open` descriptor. |
| `usb-stick-execute` / `usb-stick-install` / `usb-stick-open` | Complete USB stick roots (descriptor + payload) for each type. |
| `usb-stick-adb` / `usb-stick-wireless` / `usb-stick-carplay` / `usb-stick-voicehandler-vr-gates` / `usb-stick-recover-buildprop` | Real deployment sticks for specific packages/procedures. |

---

## 7. Building the platform itself (CLI + Manager APK)

Most package work never needs this; it is only for changes to `Sources/native/` or
`Sources/manager/`. Both builds expect a **Linux** toolchain and hardcoded developer paths,
so adjust or override variables when building elsewhere.

### Native CLI (`Sources/native/`)

```bash
make -C Sources/native          # NDK ?= /home/xwtk/ndkdl/android-ndk-r21e
```

- Cross-compiler: `armv7a-linux-androideabi19-clang` (API 19), `-O2 -fPIE -pie -Wall -Wextra
  -D_FILE_OFFSET_BITS=64`, links `-lz`. Output: `native/nemesis` (stripped).
- Sources: `main.c util.c ini.c zip.c pkg.c svc.c usb.c su.c`, header `nemesis.h`.

### Manager APK (`Sources/manager/`)

```bash
make -C Sources/manager         # ANDROID_JAR ?= android-30; aapt/javac/d8/jarsigner/zipalign
make -C Sources/manager keystore   # one-time self-signed key (alias/pass: nemesis)
```

- Java under `src/com/nemesis/manager/`, resources under `res/`, custom package
  `com.nemesis.manager`. javac+d8 run in one recipe on purpose (directory-timestamp gotcha).
- All Apps launcher grid is hardcoded in `Launcher_WP3`, so `MAIN`/`LAUNCHER` on Manager is
  ignored — USB Handler / button broadcast are the entry points. Do not patch the launcher.

### Top-level (`Sources/Makefile`)

Targets: `all native manager packages examples troubleshooter test-emu test-hooks test-meta clean`. `test-emu` needs a QEMU lab that is not published.
Note the known inconsistencies from §2 (missing `voicehandler-vr-gates`/`busybox` sources,
hardcoded paths). Prefer invoking sub-makes and the builder script directly.

---

## 8. Testing

[docs/testing.md](docs/testing.md) has the commands, requirements, and settings for everything below.

| Test | What it does |
|------|--------------|
| `Sources/tests/unit/` | Host pytest suite: builder, manifests, shipped scripts, package hooks, mount wrapper, native CLI, docs against source. Run with `python -m pytest Sources/tests/unit`. See §10. |
| `Sources/tests/check_manager_apk.py` | Host-only checks of `manager/NEMESIS.apk` (zip contents, dex, manifest via `aapt`). No guest needed. |
| `Sources/tests/setup_press_test.py` | Faceplate/steering-button (triple-press) logic tests. |
| `Sources/tests/pkg_live_snapshot.sh` | Backup/restore (live-snapshot) pattern checks. |
| `meta_host_test.c`, `store_logic_test.py` | Upstream's descriptor parser and Package Store logic checks. See `Sources/tests/README.md`. |
| `make -C Sources test-meta` / `test-hooks` | Aggregate runners. |

The host suite covers shipped packages, but a new package has no harness of its own. Validation is: build the zip,
`nemesis pkg-info` it, install on a unit (or QEMU), then `nemesis pkg-list`,
`nemesis service status`, `nemesis log dump`, and `nemesis log service <id>/<name>`.

---

## 9. Conventions, pitfalls, and new-package checklist

### Conventions

- Package `id`s use dot notation (`adb.tcp`, `wireless.projection`, `hello.tool`); directory
  names use dashes (`adb-tcp`, `wireless-projection`).
- Scripts: `#!/system/bin/sh`, explicit `PATH`/`LD_LIBRARY_PATH` exports, `exit 0` on success.
- Hooks live in `scripts/` (`pre.sh`, `post.sh`, `preun.sh`, `postun.sh`); bundled files in
  `files/`; apps in `bin/`; services in `svc/`. Match the shipped packages.
- New docs go in `docs/` and may only link to the allowed documents (§1).
- Line endings in scripts/zips destined for the unit: keep LF; USB sticks are exFAT/FAT32
  (no `chmod` needed — everything runs via `/system/bin/sh <path>`).

### Pitfalls

- Never `mount -o remount,rw /system` via toolbox directly — use `nemesis remount rw` (§3).
- Never persist anything in `/data/local/tmp` — wiped on reboot.
- Don't ship host tools (`awk`, `unzip`, `busybox`) unless included in the package itself.
- USB descriptor `path` values must be relative and free of shell metacharacters; a leading `/`
  or `..` is rejected. Keep `NEMESIS.data` paths relative: absolute paths are accepted on purpose, `..` must not be used.
- `kind=instance` and `pm install` do nothing while Android is down; `kind=sh` does.
- A `restart` service that crash-loops is disabled after 5 rapid deaths (except locked
  `usb-handler`) — make services exit cleanly on bad state instead of crash-looping.
- `nemesis wipe` without `--confirm` only prints usage; never script a wipe.
- An archive member with `..` in its path rejects the whole package at install, so a package cannot write
  outside `/data/nemesis/apps/<id>/`. Do not attempt to reach absolute or parent paths from a
  package payload; do it in a hook, where the path is explicit and reviewable.

### Checklist for a new package

1. Copy `Sources/examples/package-hello` to `Sources/packages/<my-tool>/`.
2. Set unique `id`, `name`, `version`, `types`.
3. Declare `[app.N]` / `[service.N]`; point every `path=` at a real file in the tree.
4. Write toolbox-only scripts; hooks `exit 0` on success.
5. If touching live unit files: backup to `/data/nemesis/backup/<id>/` in `postinstall`,
   restore in `preuninstall`, delete backup only after verified restore.
6. `python3 Sources/tools/make_nemesis_package.py Sources/packages/<my-tool> -o <id>.zip`.
7. Verify: `nemesis pkg-info <zip>` → install → `pkg-list` / `service status` / `log dump`.
8. To list it in the Package Store, follow `07-Submit-a-Package.md`.

### Quick reference — minimal working package

```ini
[nemesis]
version=1.0
id=my.tool
name=My Tool
types=application,service

[app.0]
kind=sh
path=bin/tool.sh
uid=root

[service.0]
name=my-daemon
kind=sh
path=svc/daemon.sh
uid=root
mode=restart
```

---

## 10. Contributing back (behaviour first)

Every change starts from a behaviour someone can observe, and that behaviour is written down before
the code changes. Scenarios are plain pytest tests with a Given/When/Then docstring. Do not add a
Gherkin layer or a BDD framework: the suite must keep running on stdlib Python plus pytest.

### The loop

1. **Check scope.** Read `SCOPE.md`. If the behaviour falls under its out-of-scope list, stop.
2. **State the behaviour.** Find it in the canonical docs (§1). If it is new, add it to the doc that
   owns that area in the same change. The doc says what users get; the test proves it.
3. **Write the scenario and watch it fail.** A scenario that passes before the change proves nothing.
4. **Make the smallest change that passes it**, then tidy up with the test still green.
5. **Run the checks** for the area you touched (see the table below) and open a pull request against
   `main` at `github.com/xwtk/hmg_nemesis`. List the scenarios it adds in the description.

### Writing a scenario

```python
def test_missing_manifest_is_refused(tmp_path):
    """Given a directory with no NEMESIS.data (04-Packages.md section 1)
    When the builder runs on it
    Then it exits non-zero, names the problem, and writes no zip
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    res = build(empty, "-o", tmp_path / "o.zip")
    assert res.returncode != 0
    assert "missing" in res.stderr
    assert not (tmp_path / "o.zip").exists()
```

- **Name the behaviour, not the function:** `test_missing_manifest_is_refused`, not `test_main_3`.
- **Cite the source:** a doc section, or `file:line` if only the code defines it.
- **Assert what can be observed:** exit code, output, files on disk, `packages.db` and `services.db`
  lines. Do not assert on private helpers when a command shows the same thing.
- **One behaviour per scenario.** Use `pytest.mark.parametrize` for more inputs to the same behaviour.
- **Include the failure path.** Most defects here are in failed updates, failed hooks, and recovery
  with Android down, so a change to those paths needs a scenario for the failure too.

### Where a scenario goes

| Behaviour | Location | Run with | Needs |
|-----------|----------|----------|-------|
| Builder, manifests, shipped scripts, package hooks, mount wrapper, docs against source | `Sources/tests/unit/test_*.py` | `python -m pytest Sources/tests/unit` | Python 3.8+, pytest, a POSIX `sh` |
| Manager button logic | `Sources/tests/src/.../SetupPressRun.java` | `python3 Sources/tests/setup_press_test.py` | JDK that accepts `-source 1.7` |
| Package hooks in a private root | `Sources/tests/pkg_live_snapshot.sh` | `bash Sources/tests/pkg_live_snapshot.sh` | Linux with user namespaces |
| CLI and USB Handler | `Sources/tests/unit/test_native_*.py` | `python -m pytest Sources/tests/unit` | Linux, `gcc`, zlib headers |
| Only provable on hardware | `Sources/usb_bundle/DEVICE_TEST.md` | By hand | A head unit you own |

Prefer the cheapest row that can prove the behaviour. If you cannot run the row that applies, say so
in the pull request.

### When the docs and the code disagree

Do not change a test to match the code, or the docs to match the code, on your own judgement. Raise
it in the issue or pull request and get the maintainer's ruling on which is correct, then pin that
ruling with a scenario.

### Known bugs

If a correct scenario fails and the fix is a separate change, keep the scenario and mark it:

```python
from helpers import known_bug

@known_bug("G99", "what is wrong, in one line")
def test_the_correct_behaviour(nem):
```

`known_bug` (`Sources/tests/unit/helpers.py`) is a strict expected failure. The suite stays green
while the bug exists and goes red once it is fixed, so the fix must remove the marker. For a new
bug, take the next number after the highest `known_bug` in the tests.

### Before opening the pull request

- [ ] Every new behaviour has a scenario that failed before the change.
- [ ] `python -m pytest Sources/tests/unit` passes with no unexpected results.
- [ ] Docs describe the new behaviour, and only link to allowed documents (§1).
- [ ] Scripts and manifests are LF (§9). On Windows, `core.autocrlf` can turn them into CRLF, and the
      builder ships them as they are.
- [ ] Behaviour that needs hardware is listed as tested on a real unit, or plainly as not tested.

