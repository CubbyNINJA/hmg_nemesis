# Scope and authorization

This file states who this project is for, what it is authorized to do, and what is out of scope.
It follows the convention used by authorized security-research and right-to-repair repositories.

## Who operates this

The operator is the owner of the vehicle and the owner of the head unit (HU) the software runs on.
The unit is in their physical possession. There is no third-party device, network, account, or
vehicle involved at any point.

## What this software does

NEMESIS is a local package manager and diagnostics interface for the infotainment head unit of
Gen5W-generation vehicles (Kia EV6 / Hyundai Ioniq 5 platform, `S5W_M` firmware). Once installed
it provides:

- a CLI and on-screen Manager for installing, listing, launching, and removing packages
- a service manager for long-running diagnostic and convenience daemons
- log collection from the unit
- a documented removal path (`nemesis wipe --confirm`)

Its purpose is owner customization of the infotainment system: enabling ADB for debugging,
wireless phone projection, turn-by-turn guidance display, and similar features that the owner
wants on their own unit.

## Legal basis

Modifying software on hardware you own for personal use, including diagnosis and repair, is a
recognized category of authorized activity in most jurisdictions. In the United States the Library
of Congress has repeatedly granted exemptions under 17 U.S.C. 1201 for vehicle owners performing
diagnosis, repair, and customization of their own vehicles. This project is exercised only in that
capacity, by the owner, on the owner's unit.

## Explicitly out of scope

This project does not, and packages for it must not:

- **Target vehicles the operator does not own.** There is no remote-deployment capability and none
  is intended.
- **Touch vehicle safety systems.** No package may actuate braking, steering, throttle,
  transmission, powertrain control, airbags, lighting, or driver-assistance functions.
- **Write CAN bus messages directly.** Where a feature needs a vehicle signal, it must use the same
  interface the stock UI uses. See `docs/drive-mode-memory-package.md`.
- **Touch anti-theft, immobilizer, or key/pairing systems.**
- **Modify the stock launcher or stock applications.** NEMESIS adds alongside them; it does not
  patch OEM apps.
- **Exfiltrate data.** All logging goes to local storage on the unit (`/data/nemesis/logs/`). No
  package may open outbound network connections for telemetry.
- **Persist against the owner's intent.** Every package is removable with `nemesis uninstall <id>`,
  and the whole installation with `nemesis wipe --confirm`. The one locked component
  (`usb-handler`) is removable by wipe and exists so the owner always retains a USB recovery path.

## Known trust implications

NEMESIS is a privileged local tool. Stating its capabilities plainly is part of scoping it
correctly; these are properties a reader should assume, not anomalies to explain away.

- **It provides root on the unit.** `nemesis root` gives an interactive root shell, and
  `/dev/nemesis.sock` and `/data/nemesis/root.sock` expose privileged operations to any local UID.
  Access is local-only (UNIX domain sockets, no network listener), on hardware the owner already
  controls physically via Engineering Mode, recovery, and USB.
- **It installs owner-authored packages that are not OEM-signed.** To do so, the installer sets
  `sys.boot_completed` to `0` around a single `pm install` call, because this platform's package
  installer accepts non-OEM signers only in that state. The previous value is restored immediately
  and `BOOT_COMPLETED` is not rebroadcast. The consequence is real: once NEMESIS is present,
  package signature provenance on this unit is the owner's responsibility, not the platform's.
  Package authors never perform this step themselves. See `04-Packages.md`.
- **A USB stick can run code as root on insertion.** With Android up, the owner must physically
  insert the stick and confirm a dialog. With Android not up, no dialog can be shown, so the
  descriptor is acted on automatically; this is the recovery path for a unit that will not boot
  into Android, and it still requires physical insertion. Anyone with physical access to the unit
  and a prepared stick can therefore run code on it. Treat physical access as root access.

These are documented so that anyone extending the project understands what they are building on.
They are not a defect list, and they are not an argument that the capabilities are harmless.

## If you are not the owner

Do not install this on a vehicle or head unit you do not own. The installation modifies `/system`
and can render the unit unusable. There is no supported remote or fleet deployment path.
