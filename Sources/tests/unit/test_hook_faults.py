"""Package hooks when a tool or a write fails (04-Packages.md section 5.1, AGENTS.md section 4.6).

A failed install must stop before changing the unit further and must not reboot. A failed
restore must keep the snapshot. Helpers that can fail must report it to their caller.
"""
from __future__ import annotations

import sys

import pytest

from helpers import PACKAGES
from sandbox import SH, Unit

BP = "/system/build.prop"
HIDE = "/system/etc/permissions/com.hkmc.software.engineermode.adb_hide.xml"
PERM = "/system/etc/permissions"
AA = "com.hkmc.software.connectivity.androidauto_wireless.xml"
CP = "com.hkmc.software.connectivity.carplay_wireless.xml"
WIFI = "android.hardware.wifi.xml"
WPA = "/system/etc/wifi/wpa_supplicant.conf"
OVERLAY = "/system/etc/wifi/wpa_supplicant_overlay.conf"
FEATURE = "/system/onebin/variant1/featurexml.cfg"
BACKUPS = {"adb-tcp": "/data/nemesis/backup/adb.tcp", "wireless-projection": "/data/nemesis/backup/wireless.projection"}
BOTH = list(BACKUPS)
# commit_tmp's last-ditch refusal. A failed write must stop the helper before it gets that far.
NOT_THE_BACKSTOP = "refuse empty rewrite"

pytestmark = pytest.mark.skipif(not SH or sys.platform == "win32", reason="needs a POSIX sh with /dev/full")


def build(tmp_path, pkg):
    unit = Unit(tmp_path, PACKAGES / pkg)
    unit.enable_faults()
    unit.backup = BACKUPS[pkg]
    if pkg == "adb-tcp":
        unit.put(BP, b"ro.product=x\nro.adb.secure=1\nservice.adb.tcp.port=1234\n")
        unit.put(HIDE, b"<permissions/>\n")
    else:
        unit.put(BP, b"ro.product=x\npersist.ium.support=none\nro.other=1\n")
        unit.put(WPA, b"ctrl_interface=wlan0\np2p_disabled=1\n")
        unit.put(OVERLAY, b"a=1\np2p_no_go_freq=1,2\n")
        unit.put(FEATURE, b"# head\n-/a/b.xml\n-/x/carplay_wireless.xml\n")
        unit.put(f"{PERM}/{WIFI}", b"<stock/>\n")
    return unit


@pytest.fixture
def adb(tmp_path):
    return build(tmp_path, "adb-tcp")


@pytest.fixture
def wifi(tmp_path):
    return build(tmp_path, "wireless-projection")


@pytest.fixture(params=BOTH)
def unit(tmp_path, request):
    made = build(tmp_path, request.param)
    made.path(made.backup).mkdir(parents=True, exist_ok=True)
    return made


def rc_of(target, command):
    """Runs one helper from common.sh and returns its exit status and output."""
    res = target.functions(f"{command}\necho rc=$?")
    assert "rc=" in res.stdout, res.stdout
    return int(res.stdout.rsplit("rc=", 1)[1].split()[0]), res.stdout


def full(target, name):
    """Makes a backup file unwritable now."""
    link = target.path(f"{target.backup}/{name}")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("/dev/full")


def assert_snapshot_failure_stopped_the_install(target, before, res):
    assert res.returncode != 0, res.stdout
    assert "nemesis remount rw" not in target.calls(), "went on to change the unit"
    assert "reboot" not in target.calls()
    assert target.read(BP) == before


# --- A failed snapshot stops the install before anything changes ---


@pytest.mark.parametrize(
    "pkg,tool,arg",
    [
        ("adb-tcp", "full", "bp.ro.adb.secure.line"),
        ("adb-tcp", "full", "bp.persist.service.adb.tcp.enable.absent"),
        ("adb-tcp", "full", "bp.service.adb.tcp.port.line"),
        ("adb-tcp", "full", "gp.persist.service.adb.tcp.enable.unset"),
        ("adb-tcp", "full", "gp.service.adb.tcp.port.unset"),
        ("adb-tcp", "full", "saved"),
        ("adb-tcp", "cp", "backup/adb.tcp/adb_hide.xml"),
        ("adb-tcp", "cp", "backup/adb.tcp/build.prop"),
        ("adb-tcp", "mkdir", "backup/adb.tcp"),
        ("adb-tcp", "rm", "backup/adb.tcp"),
        ("wireless-projection", "full", "bp.persist.ium.support.line"),
        ("wireless-projection", "full", "bp.persist.wifi.state.absent"),
        ("wireless-projection", "full", "gp.persist.ium.support.unset"),
        ("wireless-projection", "full", "gp.persist.wifi.state.unset"),
        ("wireless-projection", "full", "wifi_on.unset"),
        ("wireless-projection", "full", "featurexml.list"),
        ("wireless-projection", "full", f"perm.{AA}.absent"),
        ("wireless-projection", "full", f"perm.{CP}.absent"),
        ("wireless-projection", "full", "saved"),
        ("wireless-projection", "cp", "backup/wireless.projection/wpa_supplicant.conf"),
        ("wireless-projection", "cp", "backup/wireless.projection/wpa_supplicant_overlay.conf"),
        ("wireless-projection", "cp", f"backup/wireless.projection/perm.{WIFI}"),
        ("wireless-projection", "cp", "backup/wireless.projection/build.prop"),
        ("wireless-projection", "mkdir", "backup/wireless.projection"),
        ("wireless-projection", "rm", "backup/wireless.projection"),
    ],
)
def test_a_failed_snapshot_step_stops_the_install(tmp_path, pkg, tool, arg):
    """Given one snapshot step that cannot complete
    When postinstall runs
    Then it fails before remounting, and the unit is unchanged and not rebooted
    """
    target = build(tmp_path, pkg)
    before = target.read(BP)
    if tool == "full":
        target.plant("full", f"{target.backup}/{arg}")
    else:
        target.fail(tool, arg)
    res = target.hook("post")
    assert_snapshot_failure_stopped_the_install(target, before, res)
    # The copy's own failure stops it, not the later check that the copy is non-empty.
    assert "snapshot is empty" not in res.stdout


@pytest.mark.parametrize("pkg", BOTH)
def test_an_empty_build_prop_copy_stops_the_install(tmp_path, pkg):
    """Given a copy of build.prop that comes out empty
    When postinstall runs
    Then it reports the empty snapshot and changes nothing
    """
    target = build(tmp_path, pkg)
    before = target.read(BP)
    target.fail_empty("/build.prop")
    res = target.hook("post")
    assert "full build.prop snapshot is empty" in res.stdout
    assert_snapshot_failure_stopped_the_install(target, before, res)


@pytest.mark.parametrize("pkg", BOTH)
def test_an_empty_live_build_prop_stops_before_the_remount(tmp_path, pkg):
    """Given an empty build.prop on the unit
    When postinstall runs
    Then it stops at the snapshot and does not remount
    """
    target = build(tmp_path, pkg)
    target.put(BP, b"")
    assert_snapshot_failure_stopped_the_install(target, b"", target.hook("post"))


@pytest.mark.parametrize("pkg", BOTH)
def test_a_stale_partial_snapshot_is_replaced(tmp_path, pkg):
    """Given a backup directory left by an interrupted install, with no `saved` marker
    When postinstall runs
    Then the stale files are gone and the snapshot holds the live build.prop
    """
    target = build(tmp_path, pkg)
    stale = target.put(f"{target.backup}/stale", b"old")
    target.put(f"{target.backup}/build.prop", b"not the unit's\n")
    before = target.read(BP)
    target.hook("post")
    assert not stale.exists()
    assert target.read(f"{target.backup}/build.prop") == before


@pytest.mark.parametrize("pkg", BOTH)
def test_apply_without_a_snapshot_fails_and_changes_nothing(tmp_path, pkg):
    """Given no snapshot
    When apply_all runs
    Then it fails and leaves build.prop alone
    """
    target = build(tmp_path, pkg)
    before = target.read(BP)
    rc, out = rc_of(target, "apply_all")
    assert rc == 1 and "FAIL: no snapshot" in out
    assert target.read(BP) == before


# --- A failed apply stops, goes read-only, and does not reboot ---


@pytest.mark.parametrize(
    "pkg,tool,pattern,skip",
    [
        ("adb-tcp", "cp", "system/build.prop", 1),
        ("adb-tcp", "cp", "system/build.prop", 2),
        ("adb-tcp", "cp", "system/build.prop", 3),
        ("adb-tcp", "rm", "engineermode.adb_hide.xml", 0),
        ("wireless-projection", "cp", "system/build.prop", 1),
        ("wireless-projection", "cp", "system/build.prop", 2),
        ("wireless-projection", "cp", "onebin/variant1/featurexml.cfg", 1),
        ("wireless-projection", "cp", "etc/wifi/wpa_supplicant.conf", 1),
        ("wireless-projection", "cp", "etc/wifi/wpa_supplicant_overlay.conf", 1),
        ("wireless-projection", "cp", f"system/etc/permissions/{WIFI}", 1),
        ("wireless-projection", "cp", f"system/etc/permissions/{AA}", 0),
        ("wireless-projection", "cp", f"system/etc/permissions/{CP}", 0),
    ],
)
def test_a_failed_apply_step_stops_goes_read_only_and_does_not_reboot(tmp_path, pkg, tool, pattern, skip):
    """Given one change that cannot be written
    When postinstall reaches it
    Then the hook fails, /system goes back to read-only, and the unit does not reboot
    """
    target = build(tmp_path, pkg)
    target.fail(tool, pattern, skip, once=True)
    res = target.hook("post")
    assert res.returncode != 0, res.stdout
    assert "nemesis remount ro" in target.calls()
    assert "reboot" not in target.calls()


def test_wireless_install_stops_without_a_reboot_when_system_stays_read_only(wifi):
    """Given a /system that will not remount
    When wireless postinstall runs
    Then it fails, changes nothing, and does not reboot
    """
    before = wifi.read(BP)
    wifi.set_remount(False)
    res = wifi.hook("post")
    assert res.returncode != 0
    assert wifi.read(BP) == before
    assert "reboot" not in wifi.calls()


@pytest.mark.parametrize("pkg", BOTH)
def test_a_successful_install_goes_read_only_before_the_reboot(tmp_path, pkg):
    """Given a healthy unit
    When postinstall succeeds
    Then /system is read-only again before the reboot
    """
    target = build(tmp_path, pkg)
    assert target.hook("post").returncode == 0
    calls = target.calls()
    assert calls.index("nemesis remount ro") < calls.index("reboot")


# --- A failed restore keeps the snapshot ---


@pytest.mark.parametrize(
    "pkg,name",
    [
        ("adb-tcp", "bp.ro.adb.secure.line"),
        ("adb-tcp", "bp.persist.service.adb.tcp.enable.absent"),
        ("adb-tcp", "bp.service.adb.tcp.port.line"),
        ("adb-tcp", "adb_hide.xml"),
        ("adb-tcp", "gp.persist.service.adb.tcp.enable.unset"),
        ("adb-tcp", "gp.service.adb.tcp.port.unset"),
        ("wireless-projection", "bp.persist.ium.support.line"),
        ("wireless-projection", "bp.persist.wifi.state.absent"),
        ("wireless-projection", "featurexml.variant1.cfg"),
        ("wireless-projection", "wpa_supplicant.conf"),
        ("wireless-projection", "wpa_supplicant_overlay.conf"),
        ("wireless-projection", f"perm.{WIFI}"),
        ("wireless-projection", f"perm.{AA}.absent"),
        ("wireless-projection", f"perm.{CP}.absent"),
        ("wireless-projection", "gp.persist.ium.support.unset"),
        ("wireless-projection", "gp.persist.wifi.state.unset"),
        ("wireless-projection", "wifi_on.unset"),
    ],
)
def test_one_missing_snapshot_file_fails_the_uninstall_and_keeps_the_backup(tmp_path, pkg, name):
    """Given a snapshot missing one file (04-Packages.md 5.1)
    When preuninstall runs
    Then it fails, says the snapshot was kept, and keeps the marker
    """
    target = build(tmp_path, pkg)
    assert target.hook("post").returncode == 0
    target.path(f"{target.backup}/{name}").unlink()
    target.path("calls").write_text("")
    res = target.hook("preun")
    assert res.returncode != 0, res.stdout
    assert "snapshot kept" in res.stdout
    assert "nemesis remount ro" in target.calls()
    assert target.exists(f"{target.backup}/saved")
    assert not target.exists(f"{target.backup}/restored")


@pytest.mark.parametrize("pkg", BOTH)
def test_a_failed_restore_write_keeps_the_backup_and_clears_the_temp_file(tmp_path, pkg):
    """Given a build.prop that cannot be written back
    When preuninstall runs
    Then it fails, keeps the snapshot, and leaves no rewrite.tmp
    """
    target = build(tmp_path, pkg)
    target.hook("post")
    target.fail("cp", "system/build.prop")
    res = target.hook("preun")
    assert res.returncode != 0
    assert target.exists(f"{target.backup}/saved")
    assert not target.exists(f"{target.backup}/rewrite.tmp")


@pytest.mark.parametrize("pkg", BOTH)
def test_a_failed_backup_removal_fails_the_uninstall(tmp_path, pkg):
    """Given a restore that succeeded but a backup directory that cannot be removed
    When preuninstall finishes
    Then it reports failure
    """
    target = build(tmp_path, pkg)
    target.hook("post")
    target.fail("rm", f"{target.root}{target.backup}")
    assert target.hook("preun").returncode != 0


@pytest.mark.parametrize("pkg", BOTH)
def test_restore_recreates_a_missing_permissions_directory(tmp_path, pkg):
    """Given a unit whose permissions directory disappeared after install
    When preuninstall runs
    Then the directory comes back and the restore succeeds
    """
    target = build(tmp_path, pkg)
    target.hook("post")
    for item in target.path(PERM).iterdir():
        item.unlink()
    target.path(PERM).rmdir()
    assert target.hook("preun").returncode == 0, "restore failed"
    assert target.path(PERM).is_dir()


@pytest.mark.parametrize("pkg", BOTH)
def test_a_permissions_directory_that_cannot_be_created_fails_the_uninstall(tmp_path, pkg):
    """Given a permissions directory that cannot be created
    When preuninstall runs
    Then it fails and keeps the snapshot
    """
    target = build(tmp_path, pkg)
    target.hook("post")
    target.fail("mkdir", "system/etc/permissions")
    assert target.hook("preun").returncode != 0
    assert target.exists(f"{target.backup}/saved")


@pytest.mark.parametrize("pkg", BOTH)
def test_a_successful_uninstall_goes_read_only(tmp_path, pkg):
    """Given an installed package
    When preuninstall succeeds
    Then /system ends read-only
    """
    target = build(tmp_path, pkg)
    target.hook("post")
    target.path("calls").write_text("")
    assert target.hook("preun").returncode == 0
    assert "nemesis remount ro" in target.calls()


def test_adb_uninstall_restarts_adbd_and_clears_an_unset_runtime_port(adb):
    """Given a unit with no runtime adb port at install
    When adb preuninstall runs
    Then the port is cleared and adbd restarts
    """
    adb.hook("post")
    adb.path("calls").write_text("")
    adb.hook("preun")
    assert "setprop service.adb.tcp.port=" in adb.calls()
    assert "setprop ctl.restart=adbd" in adb.calls()


def test_wireless_uninstall_removes_a_persist_prop_that_was_unset(wifi):
    """Given a persist property that was unset at install (common.sh restore_getprop)
    When wireless preuninstall runs
    Then its persist file is removed
    """
    wifi.hook("post")
    marker = wifi.put("/data/property/persist.wifi.state", b"3")
    wifi.hook("preun")
    assert not marker.exists()


def test_wireless_restores_a_prop_in_its_original_position(wifi):
    """Given a build.prop with a saved key in the middle
    When install then uninstall run
    Then the line returns to where it was
    """
    before = wifi.read(BP)
    wifi.hook("post")
    assert wifi.hook("preun").returncode == 0
    assert wifi.read(BP) == before


# --- Runtime changes that are allowed to fail, with a warning ---


def test_wireless_install_sets_the_runtime_values(wifi):
    """Given a healthy unit
    When wireless postinstall runs
    Then the documented runtime values are set
    """
    assert wifi.hook("post").returncode == 0
    assert wifi.read("props/persist.ium.support").strip() == b"carplay"
    assert wifi.read("props/persist.wifi.state").strip() == b"3"


@pytest.mark.parametrize(
    "pkg,tool,warnings",
    [
        ("adb-tcp", "setprop", ["WARN: setprop persist.service.adb.tcp.enable failed", "WARN: setprop service.adb.tcp.port failed", "WARN: could not restart adbd"]),
        ("wireless-projection", "setprop", ["WARN: setprop persist.ium.support failed", "WARN: setprop persist.wifi.state failed"]),
        ("wireless-projection", "settings", ["WARN: settings put global wifi_on 1 failed"]),
    ],
)
def test_a_failed_runtime_change_warns_and_the_install_continues(tmp_path, pkg, tool, warnings):
    """Given a framework tool that fails
    When postinstall runs
    Then each failure is warned about and the install still succeeds
    """
    target = build(tmp_path, pkg)
    target.break_tool(tool)
    res = target.hook("post")
    assert res.returncode == 0, res.stdout
    for text in warnings:
        assert text in res.stdout


def test_a_failed_setting_restore_warns_and_the_uninstall_continues(wifi):
    """Given a wifi_on value saved at install and a settings tool that now fails
    When wireless preuninstall runs
    Then it warns and still succeeds
    """
    wifi.put("wifi_on", b"0\n")
    wifi.hook("post")
    wifi.break_tool("settings")
    res = wifi.hook("preun")
    assert res.returncode == 0, res.stdout
    assert "WARN: settings put wifi_on failed" in res.stdout


# --- Shared helpers report failure to their caller ---


@pytest.mark.parametrize("args,ok", [("-o remount,rw /system", 0), ("-o rw,remount /system", 0), ("-o rw,remount /", 0), ("-o nothing", 1)])
def test_remount_falls_back_to_toolbox_mount(unit, args, ok):
    """Given a CLI remount that fails
    When sys_rw tries the toolbox forms
    Then any one that works is success, and none working is failure
    """
    unit.set_remount(False)
    unit.set_mount(args)
    assert rc_of(unit, "sys_rw")[0] == ok


def test_log_creates_its_directory(unit):
    """Given no /data/nemesis directory
    When a message is logged
    Then the log file is created
    """
    target = unit.path("/data/nemesis")
    import shutil

    shutil.rmtree(target)
    unit.functions('log "hello"')
    assert any(path.suffix == ".log" and b"hello" in path.read_bytes() for path in target.iterdir())


def test_commit_reports_a_failed_copy_and_removes_its_temp_file_on_success(unit):
    """Given a temp file to commit
    When the copy fails, and then when it works
    Then the failure is reported, and success removes the temp file
    """
    live = unit.put("/system/live", b"old\n")
    tmp = unit.put("/data/t", b"new\n")
    unit.fail("cp", "system/live")
    assert rc_of(unit, f'commit_tmp "{unit.root}/system/live" "{unit.root}/data/t"')[0] == 1
    assert live.read_bytes() == b"old\n"
    unit.path("faults/cp").unlink()
    assert rc_of(unit, f'commit_tmp "{unit.root}/system/live" "{unit.root}/data/t"')[0] == 0
    assert live.read_bytes() == b"new\n" and not tmp.exists()


def test_snap_file_reports_failures_and_keeps_an_existing_snapshot(unit):
    """Given snap_file (common.sh)
    When the copy or the absent marker cannot be written, and when a snapshot already exists
    Then failures are reported, and an existing snapshot is kept with success
    """
    src = f"{unit.root}/system/src"
    dst = f"{unit.root}{unit.backup}/dst"
    abs_ = f"{unit.root}{unit.backup}/dst.absent"
    unit.put("/system/src", b"live\n")
    unit.fail("cp", "system/src")
    assert rc_of(unit, f'snap_file "{src}" "{dst}" "{abs_}"')[0] == 1
    unit.path("faults/cp").unlink()
    unit.path("/system/src").unlink()
    full(unit, "dst.absent")
    assert rc_of(unit, f'snap_file "{src}" "{dst}" "{abs_}"')[0] == 1
    unit.path(f"{unit.backup}/dst.absent").unlink()
    unit.put(f"{unit.backup}/dst", b"original\n")
    unit.put("/system/src", b"changed\n")
    assert rc_of(unit, f'snap_file "{src}" "{dst}" "{abs_}"')[0] == 0
    assert unit.read(f"{unit.backup}/dst") == b"original\n"


def test_restore_owned_file_reports_failures_and_removes_what_was_absent(unit):
    """Given restore_owned_file (common.sh)
    When its copy or removal fails, and when the file was absent at install
    Then failures are reported, and an absent file is removed
    """
    live = f"{unit.root}/system/o"
    bak = f"{unit.root}{unit.backup}/o"
    abs_ = f"{unit.root}{unit.backup}/o.absent"
    unit.put("/system/o", b"payload\n")
    unit.put(f"{unit.backup}/o", b"saved\n")
    unit.fail("cp", "system/o")
    assert rc_of(unit, f'restore_owned_file "{live}" "{bak}" "{abs_}"')[0] == 1
    unit.path("faults/cp").unlink()
    unit.path(f"{unit.backup}/o").unlink()
    unit.put(f"{unit.backup}/o.absent", b"absent\n")
    unit.fail("rm", "system/o")
    assert rc_of(unit, f'restore_owned_file "{live}" "{bak}" "{abs_}"')[0] == 1
    unit.path("faults/rm").unlink()
    assert rc_of(unit, f'restore_owned_file "{live}" "{bak}" "{abs_}"')[0] == 0
    assert not unit.exists("/system/o")


def test_snap_prop_line_reports_failures_keeps_the_first_duplicate_and_any_existing(unit):
    """Given snap_prop_line (common.sh)
    When its marker cannot be written, when a key appears twice, and when a snapshot exists
    Then failures are reported, the first value is kept, and an existing snapshot wins
    """
    prop = f"{unit.root}/system/p"
    base = f"{unit.root}{unit.backup}/k"
    unit.put("/system/p", b"k=first\nk=second\n")
    full(unit, "k.line")
    assert rc_of(unit, f'snap_prop_line "{prop}" k "{base}"')[0] == 1
    unit.path(f"{unit.backup}/k.line").unlink()
    assert rc_of(unit, f'snap_prop_line "{prop}" k "{base}"')[0] == 0
    assert unit.read(f"{unit.backup}/k.line") == b"k=first\n"
    unit.put("/system/p", b"k=changed\n")
    assert rc_of(unit, f'snap_prop_line "{prop}" k "{base}"')[0] == 0
    assert unit.read(f"{unit.backup}/k.line") == b"k=first\n"
    unit.path(f"{unit.backup}/k.line").unlink()
    full(unit, "k.absent")
    unit.put("/system/p", b"other=1\n")
    assert rc_of(unit, f'snap_prop_line "{prop}" k "{base}"')[0] == 1


@pytest.mark.parametrize("content", [b"k=old\n", b"a=1\n", b""], ids=["key-line", "other-line", "append"])
def test_set_prop_line_reports_a_failed_write_and_leaves_the_file(unit, content):
    """Given a temp file that cannot be written, at each place set_prop_line writes
    When it runs
    Then it reports failure and the live file is unchanged
    """
    target = unit.put("/system/p", content)
    full(unit, "rewrite.tmp")
    rc, out = rc_of(unit, f'set_prop_line "{unit.root}/system/p" k new')
    assert rc == 1 and NOT_THE_BACKSTOP not in out
    assert target.read_bytes() == content


@pytest.mark.parametrize("function", ["set_prop_line {p} k new", "restore_prop_line {p} k {b}"])
def test_prop_helpers_report_a_temp_file_that_cannot_be_created(unit, function):
    """Given a temp path that cannot be opened
    When a property helper runs
    Then it reports failure and the live file is unchanged
    """
    target = unit.put("/system/p", b"k=old\n")
    unit.put(f"{unit.backup}/k.line", b"k=saved\n")
    unit.path(f"{unit.backup}/rewrite.tmp").mkdir()
    command = function.format(p=f'"{unit.root}/system/p"', b=f'"{unit.root}{unit.backup}/k"')
    assert rc_of(unit, command)[0] == 1
    assert target.read_bytes() == b"k=old\n"


@pytest.mark.parametrize("function", ["set_prop_line {p} k new", "restore_prop_line {p} k {b}"])
def test_prop_helpers_report_a_failed_commit(unit, function):
    """Given a live file that cannot be written back
    When a property helper runs
    Then it reports failure
    """
    unit.put("/system/p", b"k=old\n")
    unit.put(f"{unit.backup}/k.line", b"k=saved\n")
    unit.fail("cp", "system/p")
    command = function.format(p=f'"{unit.root}/system/p"', b=f'"{unit.root}{unit.backup}/k"')
    assert rc_of(unit, command)[0] == 1


@pytest.mark.parametrize("content", [b"k=old\n", b"a=1\n", b""], ids=["key-line", "other-line", "append"])
def test_restore_prop_line_reports_a_failed_write_and_leaves_the_file(unit, content):
    """Given a temp file that cannot be written, at each place restore_prop_line writes
    When it runs
    Then it reports failure and the live file is unchanged
    """
    target = unit.put("/system/p", content)
    unit.put(f"{unit.backup}/k.line", b"k=saved\n")
    full(unit, "rewrite.tmp")
    rc, out = rc_of(unit, f'restore_prop_line "{unit.root}/system/p" k "{unit.root}{unit.backup}/k"')
    assert rc == 1 and NOT_THE_BACKSTOP not in out
    assert target.read_bytes() == content


def test_snap_getprop_reports_failures_and_keeps_an_existing_snapshot(unit):
    """Given snap_getprop (common.sh)
    When its file cannot be written, set or unset, and when a snapshot exists
    Then failures are reported, and an existing snapshot wins
    """
    base = f"{unit.root}{unit.backup}/g"
    unit.put("props/k", b"value\n")
    full(unit, "g")
    assert rc_of(unit, f'snap_getprop k "{base}"')[0] == 1
    unit.path(f"{unit.backup}/g").unlink()
    unit.path("props/k").unlink()
    full(unit, "g.unset")
    assert rc_of(unit, f'snap_getprop k "{base}"')[0] == 1
    unit.path(f"{unit.backup}/g.unset").unlink()
    unit.put(f"{unit.backup}/g", b"saved\n")
    unit.put("props/k", b"newer\n")
    assert rc_of(unit, f'snap_getprop k "{base}"')[0] == 0
    assert unit.read(f"{unit.backup}/g") == b"saved\n"


def test_restore_getprop_succeeds_and_warns_when_setprop_fails(unit):
    """Given a saved property value (common.sh restore_getprop)
    When it restores, with setprop working and then failing
    Then both succeed, and the failure is warned about
    """
    base = f"{unit.root}{unit.backup}/g"
    unit.put(f"{unit.backup}/g", b"saved\n")
    assert rc_of(unit, f'restore_getprop k "{base}"')[0] == 0
    assert unit.read("props/k").strip() == b"saved"
    unit.break_tool("setprop")
    rc, out = rc_of(unit, f'restore_getprop k "{base}"')
    assert rc == 0 and "WARN: setprop k failed" in out


# --- Wireless-only helpers ---


@pytest.mark.parametrize(
    "line,target_rel",
    [
        (b"#comment\n", FEATURE),
        (b"-/x/carplay_wireless.xml\n", FEATURE),
        (b"-/a/b.xml\n", FEATURE),
    ],
    ids=["comment", "wireless", "other"],
)
def test_rewrite_featurexml_reports_a_failed_write(wifi, line, target_rel):
    """Given a temp file that cannot be written, at each kind of line
    When rewrite_featurexml runs
    Then it reports failure and the live file is unchanged
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    live = wifi.put(target_rel, line)
    full(wifi, "rewrite.tmp")
    rc, out = rc_of(wifi, f'rewrite_featurexml "{wifi.root}{target_rel}"')
    assert rc == 1 and NOT_THE_BACKSTOP not in out
    assert live.read_bytes() == line


@pytest.mark.parametrize(
    "rel,content,command",
    [
        (WPA, b"p2p_disabled=1\n", "rewrite_wpa"),
        (WPA, b"other=1\n", "rewrite_wpa"),
        (WPA, b"", "rewrite_wpa"),
        (OVERLAY, b"other=1\n", "rewrite_overlay"),
    ],
    ids=["wpa-key", "wpa-other", "wpa-append", "overlay-other"],
)
def test_wifi_rewrites_report_a_failed_write(wifi, rel, content, command):
    """Given a temp file that cannot be written, at each place a rewrite writes
    When the rewrite runs
    Then it reports failure and the live file is unchanged
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    live = wifi.put(rel, content)
    full(wifi, "rewrite.tmp")
    rc, out = rc_of(wifi, command)
    assert rc == 1 and NOT_THE_BACKSTOP not in out
    assert live.read_bytes() == content


@pytest.mark.parametrize(
    "rel,command",
    [(FEATURE, f'rewrite_featurexml "{{root}}{FEATURE}"'), (WPA, "rewrite_wpa"), (OVERLAY, "rewrite_overlay")],
    ids=["feature", "wpa", "overlay"],
)
def test_wifi_rewrites_report_a_temp_file_that_cannot_be_created_or_a_failed_commit(wifi, rel, command):
    """Given a temp path that cannot be opened, then a live file that cannot be written back
    When a rewrite runs
    Then each is reported and the live file is unchanged
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    before = wifi.read(rel)
    command = command.format(root=wifi.root)
    tmp = wifi.path(f"{wifi.backup}/rewrite.tmp")
    tmp.mkdir()
    assert rc_of(wifi, command)[0] == 1
    tmp.rmdir()
    wifi.fail("cp", rel.lstrip("/"))
    assert rc_of(wifi, command)[0] == 1
    assert wifi.read(rel) == before


def test_wpa_gains_the_line_when_it_is_absent(wifi):
    """Given a wpa_supplicant.conf with no p2p_disabled line
    When wireless postinstall runs
    Then the line is added as 0, once, and the log says so
    """
    wifi.put(WPA, b"ctrl_interface=wlan0\n")
    res = wifi.hook("post")
    assert wifi.read(WPA) == b"ctrl_interface=wlan0\np2p_disabled=0\n"
    assert "(p2p_disabled=0)" in res.stdout


def test_snap_setting_reports_failures(wifi):
    """Given snap_setting (common.sh)
    When its file cannot be written, with the setting set and unset
    Then each failure is reported
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    wifi.put("wifi_on", b"0\n")
    full(wifi, "wifi_on")
    assert rc_of(wifi, "snap_setting")[0] == 1
    wifi.path(f"{wifi.backup}/wifi_on").unlink()
    wifi.path("wifi_on").unlink()
    full(wifi, "wifi_on.unset")
    assert rc_of(wifi, "snap_setting")[0] == 1


def test_snap_featurexmls_reports_failures(wifi):
    """Given snap_featurexmls (common.sh)
    When the file copy or the absent marker cannot be written
    Then each failure is reported
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    wifi.fail("cp", "onebin/variant1/featurexml.cfg")
    assert rc_of(wifi, "snap_featurexmls")[0] == 1
    wifi.path("faults/cp").unlink()
    wifi.path(f"{wifi.backup}/featurexml.list").unlink()
    wifi.put(FEATURE, b"-/a/b.xml\n")
    full(wifi, "featurexml.absent")
    assert rc_of(wifi, "snap_featurexmls")[0] == 1


def test_apply_perm_creates_the_directory_and_reports_failures(wifi):
    """Given apply_perm (common.sh)
    When the directory is missing, cannot be created, or the copy fails
    Then it creates the directory, or reports the failure
    """
    wifi.path(f"{PERM}/{WIFI}").unlink()
    wifi.path(PERM).rmdir()
    wifi.fail("mkdir", "system/etc/permissions")
    assert rc_of(wifi, f'apply_perm "{WIFI}"')[0] == 1
    wifi.path("faults/mkdir").unlink()
    wifi.path("faults/mkdir.count").unlink()
    assert rc_of(wifi, f'apply_perm "{WIFI}"')[0] == 0
    assert wifi.exists(f"{PERM}/{WIFI}")
    wifi.fail("cp", f"system/etc/permissions/{AA}")
    assert rc_of(wifi, f'apply_perm "{AA}"')[0] == 1


@pytest.mark.skipif("mksh" not in str(SH), reason="the unit's shell; other shells may exit on this redirect")
@pytest.mark.parametrize("pkg", BOTH)
def test_a_temp_file_that_cannot_be_opened_during_install_still_goes_read_only(tmp_path, pkg):
    """Given a temp path that cannot be opened, which aborts the helper in mksh
    When postinstall reaches its first rewrite
    Then the hook fails, /system goes read-only, and the unit does not reboot
    """
    target = build(tmp_path, pkg)
    target.plant("dir", f"{target.backup}/rewrite.tmp")
    res = target.hook("post")
    assert res.returncode != 0, res.stdout
    assert "nemesis remount ro" in target.calls()
    assert "reboot" not in target.calls()


@pytest.mark.skipif("mksh" not in str(SH), reason="the unit's shell; other shells may exit on this redirect")
@pytest.mark.parametrize("pkg", BOTH)
def test_a_temp_file_that_cannot_be_opened_during_uninstall_keeps_the_backup(tmp_path, pkg):
    """Given a temp path that cannot be opened, which aborts the helper in mksh
    When preuninstall runs
    Then the rest of the restore still runs, it fails, goes read-only, and keeps the snapshot
    """
    target = build(tmp_path, pkg)
    target.hook("post")
    target.path(f"{target.backup}/rewrite.tmp").mkdir()
    target.path("calls").write_text("")
    res = target.hook("preun")
    assert res.returncode != 0, res.stdout
    assert "snapshot kept" in res.stdout
    assert "nemesis remount ro" in target.calls()
    assert target.exists(f"{target.backup}/saved")


def test_restore_edited_file_reports_a_failed_copy(wifi):
    """Given restore_edited_file (common.sh)
    When its copy fails
    Then it reports failure
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    live = f"{wifi.root}{WPA}"
    bak = f"{wifi.root}{wifi.backup}/wpa"
    wifi.put(f"{wifi.backup}/wpa", b"saved\n")
    wifi.fail("cp", "etc/wifi/wpa_supplicant.conf")
    assert rc_of(wifi, f'restore_edited_file "{live}" "{bak}" "{bak}.absent"')[0] == 1


def test_restore_getprop_clears_an_unset_ordinary_property(unit):
    """Given a non-persist property that was unset at install (common.sh restore_getprop)
    When it restores
    Then setprop clears it
    """
    unit.put(f"{unit.backup}/g.unset", b"unset\n")
    assert rc_of(unit, f'restore_getprop k "{unit.root}{unit.backup}/g"')[0] == 0
    assert "setprop k=" in unit.calls()


def test_snap_setting_keeps_an_existing_snapshot(wifi):
    """Given a saved wifi_on value
    When snap_setting runs again
    Then it succeeds and keeps the saved value
    """
    wifi.put(f"{wifi.backup}/wifi_on", b"0\n")
    wifi.put("wifi_on", b"1\n")
    assert rc_of(wifi, "snap_setting")[0] == 0
    assert wifi.read(f"{wifi.backup}/wifi_on") == b"0\n"


def test_snap_featurexmls_refuses_an_unsafe_variant_name(wifi):
    """Given a variant directory whose name has a space
    When snap_featurexmls runs
    Then it fails and names the path
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    wifi.put("/system/onebin/a b/featurexml.cfg", b"-/x/carplay_wireless.xml\n")
    rc, out = rc_of(wifi, "snap_featurexmls")
    assert rc == 1 and "FAIL: featurexml path" in out


def test_snap_featurexmls_clears_a_stale_list_when_nothing_matches(wifi):
    """Given a stale list and no feature file with a wireless entry
    When snap_featurexmls runs
    Then the list is gone and the absent marker is written
    """
    wifi.put(f"{wifi.backup}/featurexml.list", b"old /system/onebin/old/featurexml.cfg\n")
    wifi.put(FEATURE, b"-/a/b.xml\n")
    assert rc_of(wifi, "snap_featurexmls")[0] == 0
    assert not wifi.exists(f"{wifi.backup}/featurexml.list")
    assert wifi.exists(f"{wifi.backup}/featurexml.absent")


def test_walk_featurexml_fails_with_no_snapshot(wifi):
    """Given neither a list nor an absent marker
    When walk_featurexml runs
    Then it fails
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    rc, out = rc_of(wifi, "walk_featurexml apply")
    assert rc == 1 and "FAIL: no featurexml snapshot" in out


def test_walk_featurexml_rejects_a_tampered_key(wifi):
    """Given a snapshot list whose key has unsafe characters
    When walk_featurexml runs
    Then it fails before touching any file
    """
    listing = wifi.put(f"{wifi.backup}/featurexml.list", b"a/b /system/onebin/a/b/featurexml.cfg\n")
    rc, out = rc_of(wifi, "walk_featurexml restore")
    assert rc == 1 and "FAIL: bad featurexml key" in out
    assert listing.exists()


@pytest.mark.parametrize("command,rel,text", [("rewrite_wpa", WPA, "SKIP missing"), ("rewrite_overlay", OVERLAY, "SKIP missing")])
def test_wifi_rewrites_skip_an_absent_file(wifi, command, rel, text):
    """Given a wifi file missing from the unit
    When its rewrite runs
    Then it succeeds, says it skipped, and creates nothing
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    wifi.path(rel).unlink()
    rc, out = rc_of(wifi, command)
    assert rc == 0 and text in out
    assert not wifi.exists(rel)


def test_wpa_line_is_replaced_and_not_duplicated(wifi):
    """Given a wpa_supplicant.conf that has a p2p_disabled line
    When wireless postinstall runs
    Then that line is rewritten in place, with no second copy
    """
    wifi.hook("post")
    assert wifi.read(WPA) == b"ctrl_interface=wlan0\np2p_disabled=0\n"


def test_apply_perm_reports_a_missing_payload(wifi):
    """Given a payload file missing from the package
    When apply_perm runs
    Then it fails and names the file
    """
    (wifi.pkg / "files" / "permissions" / AA).unlink()
    rc, out = rc_of(wifi, f'apply_perm "{AA}"')
    assert rc == 1 and "FAIL: missing" in out


def test_wireless_reinstall_keeps_the_original_snapshot(wifi):
    """Given a wireless package already installed
    When postinstall runs again
    Then it succeeds and the snapshot still holds the unit's own build.prop
    """
    before = wifi.read(BP)
    assert wifi.hook("post").returncode == 0
    assert wifi.hook("post").returncode == 0
    assert wifi.read(f"{wifi.backup}/build.prop") == before


def test_wireless_uninstall_without_a_snapshot_fails(wifi):
    """Given no snapshot
    When wireless preuninstall runs
    Then it fails, says why, and does not remount
    """
    res = wifi.hook("preun")
    assert res.returncode != 0
    assert "FAIL: no snapshot to restore" in res.stdout
    assert "nemesis remount rw" not in wifi.calls()


def test_wireless_uninstall_stops_when_system_stays_read_only(wifi):
    """Given a /system that will not remount
    When wireless preuninstall runs
    Then it fails, changes nothing, and keeps the snapshot
    """
    wifi.hook("post")
    changed = wifi.read(BP)
    wifi.set_remount(False)
    res = wifi.hook("preun")
    assert res.returncode != 0
    assert wifi.read(BP) == changed
    assert wifi.exists(f"{wifi.backup}/saved")


@pytest.mark.parametrize("pkg,skip", [("adb-tcp", 2), ("wireless-projection", 4)])
def test_install_clears_a_temp_file_its_last_commit_could_not_remove(tmp_path, pkg, skip):
    """Given a final commit whose temp file could not be removed
    When postinstall finishes applying
    Then the temp file is still cleared
    """
    target = build(tmp_path, pkg)
    target.fail("rm", "rewrite.tmp", skip, once=True)
    assert target.hook("post").returncode == 0
    assert not target.exists(f"{target.backup}/rewrite.tmp")


FAIL_FIRST_WRITE = 'n=0\nputline() {\n  n=$((n + 1))\n  [ "$n" -eq 1 ] && return 1\n  print -r -- "$1"\n}\n'


@pytest.mark.skipif("mksh" not in str(SH), reason="the override uses mksh's print")
@pytest.mark.parametrize(
    "command",
    ['set_prop_line "{live}" k new', 'restore_prop_line "{live}" k "{root}{backup}/k"'],
    ids=["set", "restore"],
)
def test_one_failed_line_write_stops_the_property_rewrite(unit, command):
    """Given a write that fails once on an ordinary line, then works, as a brief full disk would
    When a property helper rewrites the file
    Then it fails and commits nothing, rather than dropping that line
    """
    live = unit.put("/system/p", b"a=1\nk=old\n")
    unit.put(f"{unit.backup}/k.line", b"k=saved\n")
    body = command.format(live=f"{unit.root}/system/p", root=unit.root, backup=unit.backup)
    assert rc_of(unit, FAIL_FIRST_WRITE + body)[0] == 1
    assert live.read_bytes() == b"a=1\nk=old\n"


@pytest.mark.skipif("mksh" not in str(SH), reason="the override uses mksh's print")
def test_one_failed_line_write_stops_the_wpa_rewrite(wifi):
    """Given a write that fails once on an ordinary line, then works
    When rewrite_wpa runs
    Then it fails and commits nothing, rather than dropping that line
    """
    wifi.path(wifi.backup).mkdir(parents=True)
    wifi.put(WPA, b"other=1\np2p_disabled=1\n")
    assert rc_of(wifi, FAIL_FIRST_WRITE + "rewrite_wpa")[0] == 1
    assert wifi.read(WPA) == b"other=1\np2p_disabled=1\n"


@pytest.mark.parametrize("pkg", BOTH)
def test_a_reboot_that_cannot_start_fails_the_install(tmp_path, pkg):
    """Given no reboot binary
    When postinstall reaches the reboot
    Then the hook fails
    """
    target = build(tmp_path, pkg)
    target.path("/system/bin/reboot").unlink()
    assert target.hook("post").returncode != 0


def test_walk_featurexml_reports_a_failed_restore(wifi):
    """Given a listed variant with no saved copy
    When walk_featurexml restores
    Then it reports failure
    """
    wifi.hook("post")
    wifi.path(f"{wifi.backup}/featurexml.variant1.cfg").unlink()
    assert rc_of(wifi, "walk_featurexml restore")[0] == 1
