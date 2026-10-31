# NEMESIS

A diagnostics and package-management interface for Android-based Gen5W AVNT head units
(Kia EV6 / Hyundai Ioniq 5 generation).

The package catalog is moving to the [NEMESIS website](https://xwtk.cloud/services/nemesis),
which also carries the Discord invite.

## Scope and authorization

This work is performed by a vehicle owner on a head unit they own and possess, for personal
diagnostics, repair, and customization of the infotainment system. It is not intended for use on
vehicles the operator does not own, and it is not a means of accessing another person's device.
See [SCOPE.md](SCOPE.md).

NEMESIS deliberately does not modify the stock launcher or stock applications, vehicle safety
systems (braking, steering, powertrain control), anti-theft / immobilizer / key-pairing systems,
or CAN bus messages directly. Package authors are bound by the same boundaries.

## Safety warning

You bear complete responsibility for any procedure you perform on your car and its components.
There is always risk associated with the procedures described in this repository: your car's
functionality may be heavily impacted if this software is not compatible with your components.
Back up anything you modify. `nemesis wipe --confirm` is a full removal — use it only when you
intend that.

NEMESIS is subject to the GNU AGPL 3.0 License. See [LICENSE](LICENSE).

## Packages

Pre-made packages for installation on your own head unit. Browse the catalog in
[Packages/README.md](Packages/README.md).

## Compatibility

For your head unit to be compatible with NEMESIS, it must:

1. Be an Android-based Gen5W HU whose version string contains `S5W_M` (General Settings > SW
   Info). `S5W_L` is not supported — it is a fundamentally different hardware and software
   combination.

2. Have OTA update support (an active OTA subscription is not required), because installation is
   delivered through the firmware's existing DSP/OTA update path, which is the only supported way
   to write to `/system` on this unit. Some cars may still not be compatible even in that case;
   this can only be determined by attempting the installation.

### Cars known not to work with NEMESIS

| Name | Description |
| ---- | ----------- |
| Any car with no OTA update capability | NEMESIS is delivered through the DSP/OTA update path, so a car without OTA updates has no way to take it. |
| Any Canadian HMG car | No Canadian HMG vehicle supports OTA updates, even where Update OTA appears in the Engineering Mode menu. |
| Any car with an S5W_L HU | Fundamentally different hardware and software architecture. It does not run Android, so NEMESIS cannot be ported to it. |
| Hyundai Elantra / Kia Forte / Hyundai Ioniq 6 | These support OTA updates, but use different schemes that the installation path does not cover. |

### Will incompatible cars be supported later?

Not guaranteed. Other members are working on ports to cars such as the Ioniq 6 and Canadian
vehicles; see the Discord for the current state.

### What happens if I try to install on an incompatible car?

Most likely nothing: the installation does not proceed.

## **Quick Installation Guidance**

| Step | Guidance |
| ---- | -------- |
| 1 | Download [NEMESIS Installation Package](ota_package.tar) onto your computer. |
| 2 | Prepare an exFAT or FAT32 formatted blank USB drive. |
| 3 | Copy the downloaded tar file onto the USB, do not extract or repack the archive file. |
| 4 | Insert into the car's HU USB port, messages such as "No Supported Files" are expected. |
| 5 | Navigate to Engineering Mode (use web to search on how to do so), open USB Copy, select Update DSP |
| 6 | Tap on Update OTA (existence of this doesn't mean your car has OTA support), if you see a loading animation come up on the status bar in the top right, everything is going as planned. Wait for installation to complete and the HU to restart. |

## Documentation

What NEMESIS provides on the unit, its features, and how to use it.

| Document | Contents |
|----------|----------|
| [02 - Debugging Execution Interface](02-Debugging-Execution-Interface.md) | What NEMESIS is on the unit after it is present: features, how you open it, CLI and Manager usage. |
| [04 - Packages](04-Packages.md) | Installable packages: metadata, layout, builder, lifecycle. |
| [05 - Run scripts](05-Run-Scripts.md) | USB `NEMESIS.sh` / `type=execute`: when they run and how to write them. |
| [Creating a package](docs/creating-a-package.md) | Step-by-step package authoring guide. |
| [OEM source release](docs/oem-source-release.md) | What is in the Mobis open-source download, how to search it, and what it can tell you. |
| [Running the tests](docs/testing.md) | How to run the test suite, coverage, memory checks, and mutation testing. |
| [Scope and authorization](SCOPE.md) | Ownership, permitted use, and the safety boundaries this project observes. |

The install-path and uninstall-path write-ups (documents 01 and 03) are not part of this checkout.

Developed by Gor Mirzoyan (xwtk.cloud).

---

### Supporting NEMESIS development

Pledging is entirely optional and is treated as gratitude for the work already done. It covers
research costs for Gen5W systems and ongoing NEMESIS development.

- [Sponsor via GitHub](https://github.com/sponsors/xwtk)
- [Donate via xwtk.Portal](https://xwtk.cloud/services/nemesis#donate)
