# Creating a NEMESIS package

A **package** is a zip you install through NEMESIS (the head unit's Debugging
Execution Interface). Its contents unpack to `/data/nemesis/apps/<id>/`, where
tools, daemons, and optional APKs can be started, launched, or removed without
reflashing the system image.

This guide assumes NEMESIS is already present on the unit. For the INI
descriptor reference see [`04-Packages.md`](../04-Packages.md); for one-shot USB
run scripts (`type=execute`) see [`05-Run-Scripts.md`](../05-Run-Scripts.md).

---

## 1. The packages that ship in this repo

Every package is a directory under [`Sources/packages`](../Sources/packages) (or
[`Sources/examples`](../Sources/examples)) whose root holds a `NEMESIS.data`
file. Looking at the real manifests is the fastest way to learn the format.

| Package | `id` | Types | What the manifest declares |
|---------|------|-------|----------------------------|
| [`usb-handler`](../Sources/packages/usb-handler) | `usb-handler` | service | Locked core service. One `[service.0]`, `kind=sh`, `mode=restart` — the loop that reacts to `NEMESIS.data` on inserted USB drives. Seeded by NEMESIS; cannot be updated or removed except by wiping. |
| [`adb-tcp`](../Sources/packages/adb-tcp) | `adb.tcp` | application | One `[app.0]` status script, plus `pre`/`post`/`preun` hooks that snapshot and edit a live Engineering Mode file and restore it on uninstall. |
| [`wireless-projection`](../Sources/packages/wireless-projection) | `wireless.projection` | application | Status app plus hooks that drop feature-permission XMLs (shipped under `files/permissions/`) and restore the unit's own files on uninstall. |
| [`projection-tbt`](../Sources/packages/projection-tbt) | `projection.tbt` | application | Status app plus hooks that stage the APKs/`.so` shipped under `files/`. Built for firmware 260507. |
| [`examples/package-hello`](../Sources/examples/package-hello) | `hello.tool` | application, service | Minimal template: one `sh` app, one `sh` service with `mode=restart`, and all four lifecycle hooks. |
| [`examples/package-hello-v2`](../Sources/examples/package-hello-v2) | `hello.tool` | application, service | Same as above but the service uses `mode=background` (start once, no restart). |

The prebuilt catalog zips for end users live in
[`Packages/`](../Packages/README.md).

---

## 2. Anatomy of a package directory

Start from `examples/package-hello` and adapt. A package directory looks like:

```text
my-tool/
  NEMESIS.data         # required, at the root
  bin/tool.sh          # optional launchable app script
  svc/daemon.sh        # optional long-running service
  scripts/pre.sh       # optional lifecycle hooks
  scripts/post.sh
  scripts/preun.sh
  scripts/postun.sh
  files/...            # optional bundled files (APKs, XMLs, patched files)
```

The builder copies the **entire tree** into the zip, so keep everything the
package needs inside this one directory, referenced by relative paths.

---

## 3. Writing `NEMESIS.data`

It is an INI file. A minimal manifest with one app and one service:

```ini
[nemesis]
version=1.0
id=my.tool
name=My Tool
types=application,service
preinstall=scripts/pre.sh
postinstall=scripts/post.sh
preuninstall=scripts/preun.sh
postuninstall=scripts/postun.sh

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

### `[nemesis]` — package metadata

| Key | Required | Notes |
|-----|----------|-------|
| `id` | yes | Install id, unique per unit. Max 64 chars of letters, digits, `.`, `_`, `-`. No leading `.`, no `..`. Reinstalling the same id **updates** in place — there is never a second copy. |
| `name` | recommended | Display name in Manager (defaults to `id`). |
| `version` | recommended | Free-form version string. |
| `types` | recommended | Comma list shown in `pkg-list`, e.g. `application,service`. |
| `preinstall` | no | Runs **before** the tree is copied; cwd = staging dir. |
| `postinstall` | no | Runs **after** copy, APK install, and service registration; cwd = `/data/nemesis/apps/<id>`. |
| `preuninstall` | no | Runs after services stop, before APKs are removed and the tree is deleted. |
| `postuninstall` | no | Copied to tmp first so it survives the tree deletion, then run. |

All hooks run as **root**.

### `[app.N]` — launchables (`[app.0]`, `[app.1]`, …)

`nemesis pkg-launch <id>` walks these in order.

| Key | Notes |
|-----|-------|
| `kind` | `sh` = a launchable script; `apk` = a file installed with `pm install -r`. |
| `path` | Relative path inside the package (`bin/tool.sh`, `files/Something.apk`). |
| `package` | Android package name — used to launch, and to `pm uninstall` on removal. |
| `component` | Activity `package/class`; `pkg-launch` prefers this. |
| `uid` | `root` (default) or `system`. |

### `[service.N]` — background services (`[service.0]`, `[service.1]`, …)

Registered as `<package-id>/<name>`, so two packages can both use `daemon`.

| Key | Notes |
|-----|-------|
| `name` | Local service name (required). |
| `kind` | `sh` (default) runs `path` under `/system/bin/sh`; `instance` uses `am start`/`am startservice` (needs Android up). |
| `path` | Script relative to the package, or a component for `instance`. |
| `uid` | `root` (default) or `system`. |
| `mode` | `restart` (restart on death), `background` (start once), or `once` (wait for exit). |

`sh` service output goes to `/data/nemesis/logs/<id>/<name>.log` and survives reboot.

---

## 4. Writing the scripts

Hooks and app/service scripts run under the head unit's KitKat toolbox. Keep
them portable:

```sh
#!/system/bin/sh
export PATH=/sbin:/vendor/bin:/system/sbin:/system/bin:/system/xbin
export LD_LIBRARY_PATH=/vendor/lib:/system/lib

# your commands
exit 0
```

Guidelines:

- **Toolbox only.** Do not rely on `busybox`, `awk`, `unzip`, etc. unless you
  ship them inside the package.
- **Use the NEMESIS CLI** at `/system/xbin/nemesis` for privileged work
  (`pkg-list`, `install`, `log dump`, `remount`, …).
- **Changing `/system`** — prefer `nemesis remount rw` / `nemesis remount ro`.
- **If your package edits a live file on the unit**, snapshot it into
  `/data/nemesis/backup/<id>/` in `postinstall` before changing it, and restore
  it in `preuninstall` so the package uninstalls cleanly. `adb-tcp` and
  `wireless-projection` are the reference implementations of this pattern.
- Exit `0` on success. A non-zero `preinstall` stops the install. A non-zero
  `postinstall` makes `nemesis install` return failure, but the package stays
  installed and its services start.

---

## 5. Build the zip

Use the builder, which zips every file under the package directory with paths
relative to it:

```bash
python3 Sources/tools/make_nemesis_package.py Sources/packages/my-tool -o my.tool.zip
```

Inspect the result on the unit with `nemesis pkg-info my.tool.zip`.

---

## 6. Install, update, uninstall

**USB** — put the zip under a relative path and point a `type=install`
descriptor at it:

```text
NEMESIS.data
packages/my.tool.zip
```

```ini
[nemesis]
version=1.0
type=install
path=packages/my.tool.zip
```

`path` must be relative — no leading `/`, no `..`, no shell metacharacters.
Insert the stick; confirm the prompt if Android is up. If the id already exists
and Android is up, Manager tells you to uninstall it first; if Android is down,
it updates without a dialog.

**CLI:**

```sh
nemesis install /storage/usb0/packages/my.tool.zip
nemesis install --update /storage/usb0/packages/my.tool.zip
```

**Uninstall:**

```sh
nemesis uninstall my.tool
```

Uninstall runs `preuninstall`, removes the package's APKs and services, runs
`postuninstall` if listed, then deletes the package directory. Locked packages
(e.g. `usb-handler`) refuse to uninstall.

---

## 7. Checklist for a new package

1. Copy `Sources/examples/package-hello` to a new directory.
2. Set a unique `id`, a `name`, `version`, and the right `types`.
3. Declare your `[app.N]` / `[service.N]` sections and point `path=` at real files.
4. Write the hook and app/service scripts (toolbox-only, `exit 0` on success).
5. If you touch live `/system` files, add the backup/restore pattern.
6. Build with `make_nemesis_package.py` and inspect with `nemesis pkg-info`.
7. Install over USB (`type=install`) or `nemesis install`, then verify with
   `nemesis pkg-list` and `nemesis log dump`.
