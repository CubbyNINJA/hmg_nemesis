# Notes on the Mobis R6 source release

Oddities, aging components and licensing traps found while reading
`AVNT+STD+G50+WIDE+R6.zip`. Companion to [the reference guide](oem-source-release.md), which
covers what the release is *for*. Everything here was checked against release R6 with `unzip -Z1`
and `unzip -p`; re-check before trusting any of it for a different release.

None of this is a defect in NEMESIS. It is what the vendor shipped, and it matters to a package
author in exactly two ways: it tells you how old the platform's tooling is, and it tells you what
licence you inherit if you build against it.

## Naming

- `AVNT5W_TCC803XKK_platform_chromuim_orgthirdpartyWebKit` — "chromuim". The typo is in the
  directory name, so it is in every path under it. Grep for `chromuim`, not `chromium`, or you
  will conclude the folder does not exist.
- The two kernel folders for the same platform are `AVNT5W_DaudioKK_kerneltcc` and
  `AVNT5W_DaudioKK_kernellk`. `lk` is the Little Kernel bootloader, not a second kernel; the
  naming invites you to treat them as interchangeable. They are not.
- `AVNT5W_TCC803XKK_bootloadernew` — "new" as a permanent qualifier. There is no
  `bootloaderold` in the release, so the word carries no information and will outlive whatever
  it was distinguishing.
- `Open+Source+S_W+License+Notice_AVNT5W_R6.zip` — the `+` are URL-encoded spaces from the
  download, and `S_W` is presumably "Software". Both filenames keep the encoding, so any script
  that globs them needs the literal `+`.

## Duplication

`AVNT5W_EngineerMode_base.txt` and `AVNT5W_EngineerMode_Settings.txt` are byte-identical, same
MD5 (`a2531f72…`), same two file paths, same modification dates. Two notices for one component,
differing only in which build target they were attached to. If you are counting components by
counting notices, Engineering Mode is double-counted.

This is probably not unique to Engineering Mode. With 113 top-level `.txt` notices and several
`*_base` / `*_listview` pairs, assume some others are duplicates too and check the MD5 before
treating a pair as two components.

## Aging components

The release is a snapshot of a KitKat-era platform, and the open-source component versions show
it. If you ship a binary built from any of these, you are shipping something far older than the
upstream project's current release, and you inherit its known issues along with its licence.

| Component | Version in R6 | Note |
|---|---|---|
| `dnsmasq` | 2.51 | Per its `CHANGELOG`. 2.51 is from 2009; upstream has had many DNS- and DHCP-facing fixes since. Also the oldest thing in the release that faces a network |
| `mosquitto` | 2.0.18 | Under `AVNT5W_DaudioKK_vendormobis/external/mosquitto`. This one is *newer* than the platform it sits on — an MQTT broker from the 2.0 series on Android 4.4, pulled in by the update manager |
| `e2fsprogs` | 1.42.6 | Shipped twice, under both platform prefixes |
| `iproute2` | Snapshot 120521 | A date-staged snapshot rather than a release, so there is no version to look fixes up against |
| `elfutils` | 0.138 | |
| WebKit | Under `platform_chromuim_orgthirdpartyWebKit` | Version not established. A WebKit of this vintage is the thing in the release I would be least willing to expose to network input |

The `mosquitto` mismatch is the interesting one: the platform is 2013-era Android, but a
component the update manager depends on is roughly contemporary. Whatever process pulled it in
was not constrained by the platform's age.

## Things that look worse than they are

`AVNT5W_DaudioKK_vendormobis/external/mosquitto/test/ssl/` ships 11 private keys —
`client.key`, `server.key`, `test-root-ca.key`, `test-fake-root-ca.key`, `test-bad-root-ca.key`
and friends, plus matching certificates and a `gen.sh` that regenerates them.

These are upstream mosquitto's own TLS test fixtures, present in the public mosquitto
repository, and every key in the archive is under that one `test/ssl/` directory. They are
deliberately throwaway and sign nothing that exists. Seeing private keys in a vendor source drop
is alarming at a glance and means nothing here — but it is worth knowing they are there so that
the next person does not spend an afternoon on them, and so that nobody copies them into a
package as convenient fixtures.

## Licensing traps for package authors

- Components with an `Android.mk` were built inside a full Android tree that is **not** in the
  release. Expect real work to build any of them standalone; they are not self-contained.
- `dnsmasq`, `e2fsprogs`, `iproute2`, `elfutils` and the kernel are GPL. Shipping a binary built
  from them in a NEMESIS package carries source-sharing obligations. NEMESIS itself is AGPL 3.0,
  which is compatible in spirit but does not discharge the obligation for you.
- `libusb` is present with only 12 files and no `Android.mk`, so it is a fragment rather than a
  buildable component. Do not plan around it.
- The licence notices list file paths and modification dates, never the modification itself. They
  are evidence that a file was touched, not evidence of what it does. Several places in the
  reference guide lean on this, and it is the single easiest thing to over-read.

## What these notes deliberately omit

The release also contains CAN bus drivers, key and cipher handling, raw non-volatile storage
drivers, and code referring to the vehicle microcontroller, security and variant coding. The
update manager's own daemon source is in there too, under
`AVNT5W_DaudioKK_vendormobis/updateManager/`.

None of that is catalogued here. [SCOPE.md](../SCOPE.md) bars packages from actuating safety
systems, writing CAN directly, and touching anti-theft or key systems, and a curated list of
weaknesses in those components would be a starting point for exactly the work SCOPE.md excludes —
regardless of what anyone's intent was in writing it. The reference guide's
[Scope](oem-source-release.md#scope) section says the same thing about the release as a whole.

Read those areas if you need to understand why the unit behaves as it does. Do not build on them,
and do not turn this file into an index of them.
