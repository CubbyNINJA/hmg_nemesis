# OEM source release reference

Hyundai Mobis publishes the open-source code used in this head unit. This guide says what is in that release, how to search it without unpacking it, and what it can tell you when you work on NEMESIS packages.

The release is reference material only. It holds no firmware and cannot boot, and most of what a package touches is not in it. Start with [Creating a package](creating-a-package.md), then come here with a specific question.

For the release's oddities, component versions and licensing traps, see [the notes](source-release-notes.md).

## Get the files

Download them by hand from the [Mobis open source page](https://www.mobis.com/en/tech/rnd.do#open_alert):

```text
https://www.mobis.com/en/tech/rnd.do#open_alert
```

This guide describes release R6:

| File | Size | Contents |
|---|---|---|
| `AVNT+STD+G50+WIDE+R6.zip` | 0.56 GB, 1.77 GB unpacked | Source code and license text, 106,072 entries |
| `Open+Source+S_W+License+Notice_AVNT5W_R6.zip` | Under 1 MB | 4 HTML license notices |

Newer releases may differ. Check any path here against the release you have.

Save them in the repo root or anywhere else. `.gitignore` covers the two names above, for any release number, and also `Sources/firmware/`. **Do not commit these files, or any firmware**, under another name or path.

## Search without unpacking

You do not need to extract anything. Run these on your computer, not the unit, which has no `unzip`. They use `unzip`, with the archive path in a variable:

```sh
Z="AVNT+STD+G50+WIDE+R6.zip"

# Find files by name
unzip -Z1 "$Z" | grep mobis_sysfs

# Read one file
unzip -p "$Z" AVNT5W_DaudioKK_platform_frameworksbase/data/etc/platform.xml

# Check a kernel option
unzip -p "$Z" AVNT5W_TCC803XKK_kernel/arch/arm/configs/tcc803x_wide_pe_common_defconfig | grep CONFIG_VFAT_FS

# Did the vendor change this Android file?
unzip -p "$Z" AVNT5W_frameworks_base_base.txt | grep PackageManagerService

# Search the vendor's kernel change notes
unzip -p "$Z" AVNT5W_TCC803X_kernel_mobis.txt | grep -i -m5 usb
```

## Two platforms

Most components appear twice, because the release covers two platforms:

| Prefix | Chip family | Kernel folder |
|---|---|---|
| `AVNT5W_DaudioKK_` | Telechips `tcc897x` | `AVNT5W_DaudioKK_kerneltcc` |
| `AVNT5W_TCC803X_`, `AVNT5W_TCC803XKK_` | Telechips `tcc803x` | `AVNT5W_TCC803XKK_kernel` |

The release does not say which vehicle uses which, so ask the unit:

```sh
nemesis root -c "cat /proc/cpuinfo"
nemesis root -c "getprop ro.board.platform"
nemesis root -c "cat /proc/version"
```

The kernel version is not stored in the release, because the kernel folders have no root `Makefile`. The vendor's notes mention several versions, so use `/proc/version`. Files without either prefix, such as `AVNT5W_frameworks_base_base.txt`, are not tied to one platform by name.

## Where to look

| Question | Look at |
|---|---|
| Might the vendor have changed this Android file? | [The file lists](#android-what-the-vendor-changed) |
| Which vendor app owns a package name my hook touches? | [Vendor app notices](#vendor-app-notices-and-the-shipped-packages) |
| What permissions and groups does Android map? | `AVNT5W_DaudioKK_platform_frameworksbase/data/etc/platform.xml` |
| Can the kernel do this? | [Kernel options](#kernel-options) |
| Why did the kernel log or refuse something? | The kernel source, then the [change notes](#kernel-change-notes) |
| How does USB gadget mode or Apple accessory mode work? | `drivers/usb/gadget/function/` in a kernel folder, such as `f_iap2.c`, `f_accessory.c`, `f_mtp.c` and `f_rndis.c` |
| How does quick boot work? | `drivers/char/qb_common.c` and `include/linux/qb_manager.h` in a kernel folder, and `app/aboot/lz4/` in `AVNT5W_DaudioKK_kernellk` |
| Which tool version should I match if I ship one? | [Open-source components](#open-source-components) |

Quick boot matters for `Sources/boot/qb_hook.sh`, which NEMESIS uses to start at boot.

## What the archive holds

| Folder | Files | Contents |
|---|---|---|
| `AVNT5W_TCC803XKK_kernel` | 29,931 | Kernel source for `tcc803x`. Also holds `tcc570x`, `tcc802x`, `tcc897x` and `tcc898x` configs |
| `AVNT5W_DaudioKK_kerneltcc` | 30,080 | Kernel source for `tcc897x` |
| `AVNT5W_TCC803XKK_bootloadernew` | 15,538 | Bootloader and U-Boot for `tcc803x` |
| `AVNT5W_DaudioKK_kernellk` | 9,287 | Bootloader and U-Boot for `tcc897x` |
| `AVNT5W_*_platform_frameworksbase` | 504 in total | Only `data/`: fonts, keyboard layouts and, for `DaudioKK`, `platform.xml` |
| `AVNT5W_DaudioKK_vendormobis` | 796 | `mosquitto` and `cpulimit`, from the update manager |
| `AVNT5W_*_platform_telechipsomx` | 198 in total | OpenMAX media code |
| `AVNT5W_DaudioKK_platform_telechipsipod` | 77 | Apple accessory library and tools |
| Other `AVNT5W_TCC803XKK_*` and `*_external*` folders | About 13,000 | [Open-source components](#open-source-components) |
| 113 `AVNT5W_*.txt` files | Not applicable | License text, vendor file lists, and kernel change notes |

The 504 are 145 fonts, 358 keyboard layouts and that one `platform.xml`. None of it is vendor app code.

`platform.xml` is the exception to the vendor-modification pattern, and worth knowing before you rely on it. It is stock AOSP under a Broadcom copyright with no Mobis additions: 23 permissions mapping to 18 groups, all of them AOSP names such as `inet`, `log`, `sdcard_rw`, `net_bt`, `media_rw` and `diag`. Only `DaudioKK` ships it; the `TCC803XKK` frameworksbase folder has no `data/etc` at all. So it documents the permission-to-GID mapping a `uid=system` service runs under, but it is not evidence of anything the vendor changed, and a permission missing from it may still exist on the unit. Check the unit's own copy before relying on it; AOSP installs this file to `/system/etc/permissions/platform.xml`, but the vendor may have moved it:

```sh
nemesis root -c "ls /system/etc/permissions/platform.xml"
```

## Android: what the vendor changed

The source of the vendor's Android is **not** included. The `.txt` files are license notices that list file paths, with one shared line such as `Mobis implementation`. They suggest vendor changes but do not say what changed in any file. That still tells you where stock Android documentation may not apply.

| File | Files listed | Worth noting |
|---|---|---|
| `AVNT5W_frameworks_base_base.txt` | 766 | `PackageManagerService.java`, `PackageParser.java`, `Settings.java`, `ActivityManagerService.java`, `MountService.java`, `UsbDeviceManager.java`, `UsbSettingsManager.java`, `WifiService.java` |
| `AVNT5W_TCC803X_platform_system_core_core.txt` | 44 | `adb/adb.c`, `init/init.c`, `init/builtins.c`, `init/property_service.c`, `rootdir/init.rc`, `rootdir/init.usb.rc`, `fs_mgr/fs_mgr.c`, `sdcard/sdcard.c`, `reboot/reboot.c`, `toolbox/Android.mk` |
| `AVNT5W_DaudioKK_platform_system_core_core.txt` | 41 | The same area for `DaudioKK` |
| `AVNT5W_platform_system_vold_vold.txt` | 19 | `Volume.cpp`, `Fat.cpp`, `ExFat.cpp`, `Ntfs.cpp`, `HfsPlus.cpp`, `cdfs.cpp`, `NetworkVolume.cpp` |
| `AVNT5W_EngineerMode_Settings.txt` | 2 | Engineering Mode classes |

So, for example, the package manager service, `adb`, `init` and `vold` may differ from stock. Test against the unit, not stock KitKat behaviour.

`AVNT5W_EngineerMode_Settings.txt` and `AVNT5W_EngineerMode_base.txt` hold the same two entries. They are the only place the release names Engineering Mode code:

```text
src/com/hkmc/system/app/engineering/module/WifiClientEnablerForEngineering.java
src/com/hkmc/system/app/engineering/version/SystemVersionInfo.java
```

Both are listed as modified from AOSP, `WifiClientEnablerForEngineering.java` in 2017 and `SystemVersionInfo.java` in 2022 for version info. The source is not included, so this tells you the classes exist and were changed, not what they do. `SystemVersionInfo` is the area behind the `S5W_M` string that General Settings > SW Info shows and that the compatibility check in [README.md](../README.md) depends on.

`AVNT5W_frameworks_base_base.txt` also shows classes that stock Android does not have, by name only:

- `core/java/com/hkmc/`, including `Feature.java`, `gpm/GpmManager.java`, `internal/mode/ModeManager.java` and `internal/server/HkmcSystemServer.java`
- `mobis/java/`, including `android/app/ExtendedActivityManager.java` and `com/mobis/view/ExtendedWindowManager.java`
- `mobis/native/cmds/`, including `dumpmobis`, `export_log` and `fastdumpstate`

Only the names are known. To learn how one of these behaves, look at the unit itself.

## Vendor app notices and the shipped packages

Most `AVNT5W_*_base.txt` files name one or two vendor classes. That is enough to tie a package name your hook already uses to the vendor component that owns it, which is what you need before you `am force-stop` something or snapshot one of its files.

| Package | What it touches on the unit | Notice in the release |
|---|---|---|
| `wireless.projection` | `am force-stop com.daudio.app.phoneprojection`, `com.daudio.app.dipo` | `AVNT5W_PhoneProjection_base.txt` names `com.daudio.app.phoneprojection.popup.BluetoothProgressDialog`, modified 2019. Confirms the `com.daudio.app.phoneprojection` package name is the vendor projection app. Nothing in the release covers `com.daudio.app.dipo` |
| `adb.tcp` | `/system/etc/permissions/com.hkmc.software.engineermode.adb_hide.xml` | `AVNT5W_EngineerMode_*.txt`, which name `com.hkmc.system.app.engineering` classes. The `com.hkmc.software.engineermode` permission namespace is not in the release |
| `projection.tbt` | Ships `HKMC_DiPO.apk`, `HKMC_GPM.apk`, `HKMC_Navi_ExSL.apk`, `Iap2ServerApp.apk` | Not in the release. `AVNT5W_DaudioKK_platform_telechipsipod` holds the Apple accessory library that `Iap2ServerApp` relates to, but no APK source |
| `Sources/boot/qb_hook.sh` | Quick boot | `drivers/char/qb_common.c`, `include/linux/qb_manager.h` |

Other vendor app notices exist with no shipped package behind them yet: `RearCareMode`, `Sports`, `NModeAppJSN`, `DMB`, `ModemFoTAService`, and four regional `Radio*_listview` files. Read them the same way, as a pointer to a class name, not as documentation of behaviour.

A notice tells you a file was modified and when. It never tells you what the modification was, so it cannot replace reading the unit. Its use is narrower: it confirms you have the right package name, and it warns you that stock AOSP documentation for that area may not apply.

## Kernel options

These come from the shipped config files. They show intent, not what is on your unit, because the vendor may build with other settings or overlays.

| Option | `daudiokk_common_defconfig` | `tcc803x_android_avn_defconfig` | `tcc803x_wide_pe_common_defconfig` |
|---|---|---|---|
| `USB_G_ANDROID` (USB gadget) | Yes | Yes | Yes |
| `USB_STORAGE`, `VFAT_FS`, `EXT4_FS`, `FUSE_FS` | Yes | Yes | Yes |
| `USB_USBNET`, `TUN`, `BRIDGE` | Yes | Yes | Yes |
| `NETFILTER`, `IP_NF_IPTABLES`, `IP_NF_NAT` | Yes | Yes | Yes |
| `SECURITY_SELINUX` | Yes | Yes | Yes |
| `MODULES`, `MODULE_UNLOAD` | Yes | Yes | Yes |
| `SND_USB_AUDIO` | Yes | Yes | Yes |
| `CFG80211` | Yes | Yes | Yes |
| `BT` | Yes | Not listed | Yes |
| `ARM_LPAE` | Not listed | Yes | Yes |
| `IKCONFIG_PROC` | Off | Yes | Yes |
| `DEVMEM` | Not listed | Off | Yes |
| `SWAP` | Not listed | Off | Off |

"Not listed" means the option is not in that file. It can still be on by default.

If your kernel has `IKCONFIG_PROC`, read the real config from the unit, which beats any of these files. Toolbox cannot unpack it, so copy it to a USB stick and read it on a computer:

```sh
nemesis root -c "cat /proc/config.gz > /storage/usb0/config.gz"
```

The vendor's own drivers are in `drivers/misc/mobis/` and `drivers/char/daudio*`. `mobis_sysfs_driver.c` registers `mobis_sysfs` under the kernel kobject when `CONFIG_MOBIS_SYSFS` is set, as in two of the three configs above. Check for `/sys/kernel/mobis_sysfs` on your unit.

## Kernel change notes

These four files are logs of vendor kernel changes, unlike the Android lists:

| File | Dates covered |
|---|---|
| `AVNT5W_TCC803X_kernel_mobis.txt` | 2019-04-09 to 2023-05-24 |
| `AVNT5W_DaudioKK_kernel_mobis.txt` | 2016-04-18 to 2023-06-02 |
| `AVNT5W_TCC803X_kernel_telechips.txt` | 2015-12-08 to 2019-07-04 |
| `AVNT5W_DaudioKK_kernel_telechips.txt` | 2015-12-08 to 2016-03-31 |

Each entry has a description, a date and the files it changed:

```text
Modification: [WIDE][BSP][EXAMPLE-12345] Short description of the change.
Date: 2023-01-01
fs/fat/example.c
```

Searching by a file or word shows when and why the vendor touched that area.

## Open-source components

Components with an `Android.mk` were built inside a full Android tree, which is not included, so expect work to build one by itself.

| Component | Folder under `AVNT5W_TCC803XKK_` | Version |
|---|---|---|
| `dnsmasq` | `platform_externaldnsmasq` | 2.51 |
| `e2fsprogs` | `platform_externale2fsprogs`, also under `DaudioKK` | 1.42.6 |
| `iproute2` | `platform_externaliproute2` | Snapshot 120521 |
| `elfutils` | `platform_externalelfutils` | 0.138 |
| `iptables` | `platform_externaliptables` | Not checked |
| `libnl` | `platform_externallibnl` | Not checked |
| `libusb` | `platform_externallibusb` | Not checked. Only 12 files, with no `Android.mk` |
| `glib`, `aac`, `oprofile`, `proguard`, `junit`, `libsepol` | `externalglib` and `platform_external*` | Not checked |
| WebKit | `platform_chromuim_orgthirdpartyWebKit` | Not checked |

If you ship a binary built from one of these, its license applies to your package. Many are GPL, which comes with source-sharing obligations.

## What it cannot do

- **Boot or run.** There are no system images, prebuilt kernels, APKs, or `build.prop`, so it cannot run the emulator test.
- **Build the kernel as shipped.** The kernel folders have no root `Makefile`, `Kbuild`, or `scripts/`.
- **Show how the vendor apps work.** Projection, navigation, cluster, and Engineering Mode source is not here, only license notices.
- **Answer questions about your unit's files.** `featurexml.cfg`, `build.prop`, `init.rc` and the feature XMLs are not in the release. Read them on a unit you own, using `nemesis root -c`.

## Scope

Read [SCOPE.md](../SCOPE.md) before you build on anything here. Reading the code to understand the unit is fine, but packages must stay within those boundaries. In particular, the release also contains these, which are not a starting point for packages:

- CAN bus drivers
- Key, cipher and raw non-volatile storage drivers
- Code that refers to the vehicle microcontroller, security or variant coding
