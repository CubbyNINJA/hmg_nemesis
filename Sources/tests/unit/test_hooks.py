"""Package hooks against AGENTS.md section 4.6 and 04-Packages.md section 5.1.

Install changes the unit, uninstall puts the unit's own bytes back, and a failed
restore keeps the snapshot.
"""
from __future__ import annotations

import pytest

from helpers import PACKAGES
from sandbox import SH, Unit

BP = "/system/build.prop"
HIDE = "/system/etc/permissions/com.hkmc.software.engineermode.adb_hide.xml"
ADB_BACKUP = "/data/nemesis/backup/adb.tcp"
WIFI_BACKUP = "/data/nemesis/backup/wireless.projection"
PERM = "/system/etc/permissions"
WIFI_XMLS = [
    "android.hardware.wifi.xml",
    "com.hkmc.software.connectivity.androidauto_wireless.xml",
    "com.hkmc.software.connectivity.carplay_wireless.xml",
]
WPA = "/system/etc/wifi/wpa_supplicant.conf"
OVERLAY = "/system/etc/wifi/wpa_supplicant_overlay.conf"
FEATURE = "/system/onebin/variant1/featurexml.cfg"
STOCK_BP = b"ro.product=x\nro.adb.secure=1\nservice.adb.tcp.port=1234\n# comment\n"

pytestmark = pytest.mark.skipif(not SH, reason="needs a POSIX sh")


@pytest.fixture
def adb(tmp_path):
    unit = Unit(tmp_path, PACKAGES / "adb-tcp")
    unit.put(BP, STOCK_BP)
    unit.put(HIDE, b"<permissions/>\n")
    return unit


@pytest.fixture
def wifi(tmp_path):
    unit = Unit(tmp_path, PACKAGES / "wireless-projection")
    unit.put(BP, b"ro.product=x\npersist.ium.support=none\n")
    unit.put(WPA, b"ctrl_interface=wlan0\np2p_disabled=1\n")
    unit.put(OVERLAY, b"a=1\np2p_no_go_freq=1,2\n")
    unit.put(FEATURE, b"# head\n-/a/b.xml\n-/x/androidauto_wireless.xml\n#-/x/carplay_wireless.xml\n-/x/carplay_wireless.xml\n")
    unit.put(f"{PERM}/{WIFI_XMLS[0]}", b"<stock/>\n")
    return unit


def test_adb_install_changes_the_documented_properties_and_reboots(adb):
    assert adb.hook("post").returncode == 0
    lines = adb.read(BP).decode().splitlines()
    assert "ro.adb.secure=0" in lines
    assert "persist.service.adb.tcp.enable=1" in lines
    assert "service.adb.tcp.port=5555" in lines
    assert "ro.product=x" in lines and "# comment" in lines
    assert not adb.exists(HIDE)
    assert adb.read("props/persist.service.adb.tcp.enable").strip() == b"1"
    assert adb.read("props/service.adb.tcp.port").strip() == b"5555"
    assert "setprop ctl.restart=adbd" in adb.calls()
    assert "reboot" in adb.calls()


def test_adb_install_remounts_rw_then_ro_and_snapshots_first(adb):
    adb.hook("post")
    calls = adb.calls()
    assert calls.index("nemesis remount rw") < calls.index("setprop") < calls.index("nemesis remount ro")
    assert adb.read(f"{ADB_BACKUP}/build.prop") == STOCK_BP
    assert adb.read(f"{ADB_BACKUP}/adb_hide.xml") == b"<permissions/>\n"


def test_adb_uninstall_restores_the_units_own_bytes(adb):
    adb.hook("post")
    assert adb.hook("preun").returncode == 0
    assert adb.read(BP) == STOCK_BP
    assert adb.read(HIDE) == b"<permissions/>\n"
    assert not adb.exists(ADB_BACKUP)


def test_adb_uninstall_removes_what_was_absent_at_install(adb):
    adb.put(BP, b"ro.product=x\n")
    adb.path(HIDE).unlink()
    adb.hook("post")
    assert adb.hook("preun").returncode == 0
    assert adb.read(BP) == b"ro.product=x\n"
    assert not adb.exists(HIDE)


def test_adb_uninstall_restores_runtime_properties(adb):
    adb.put("props/service.adb.tcp.port", b"7777\n")
    adb.put("props/persist.service.adb.tcp.enable", b"0\n")
    adb.hook("post")
    adb.hook("preun")
    assert adb.read("props/service.adb.tcp.port").strip() == b"7777"
    assert adb.read("props/persist.service.adb.tcp.enable").strip() == b"0"


def test_adb_uninstall_removes_a_persist_prop_that_was_unset(adb):
    # common.sh: an empty setprop would override build.prop, so the persist file goes.
    adb.hook("post")
    adb.put("/data/property/persist.service.adb.tcp.enable", b"1")
    adb.hook("preun")
    assert not adb.exists("/data/property/persist.service.adb.tcp.enable")


def test_reinstall_keeps_the_original_snapshot(adb):
    # An update must not snapshot the already-changed unit as if it were stock.
    adb.hook("post")
    assert adb.hook("post").returncode == 0
    assert adb.read(f"{ADB_BACKUP}/build.prop") == STOCK_BP
    adb.hook("preun")
    assert adb.read(BP) == STOCK_BP


def test_uninstall_without_a_snapshot_fails_and_changes_nothing(adb):
    before = adb.snapshot("system")
    res = adb.hook("preun")
    assert res.returncode != 0
    assert "no snapshot" in res.stdout
    assert adb.snapshot("system") == before


def test_failed_restore_keeps_the_backup(adb):
    # 04-Packages.md 5.1: "If the restore fails, the backup stays."
    adb.hook("post")
    adb.path(f"{ADB_BACKUP}/adb_hide.xml").unlink()
    res = adb.hook("preun")
    assert res.returncode != 0
    assert "snapshot kept" in res.stdout
    assert adb.exists(f"{ADB_BACKUP}/saved")
    assert adb.exists(f"{ADB_BACKUP}/build.prop")
    assert not adb.exists(f"{ADB_BACKUP}/restored")


def test_failed_restore_still_restores_the_rest_and_goes_read_only(adb):
    adb.hook("post")
    adb.path(f"{ADB_BACKUP}/adb_hide.xml").unlink()
    open(adb.path("calls"), "w").close()
    adb.hook("preun")
    assert adb.read(BP) == STOCK_BP
    assert "nemesis remount ro" in adb.calls()


def test_uninstall_stops_when_system_stays_read_only(adb):
    adb.hook("post")
    changed = adb.read(BP)
    adb.set_remount(False)
    res = adb.hook("preun")
    assert res.returncode != 0
    assert adb.read(BP) == changed
    assert adb.exists(f"{ADB_BACKUP}/saved")


def test_install_stops_without_a_reboot_when_system_stays_read_only(adb):
    adb.set_remount(False)
    res = adb.hook("post")
    assert res.returncode != 0
    assert adb.read(BP) == STOCK_BP
    assert adb.exists(HIDE)
    assert "reboot" not in adb.calls()


def test_install_refuses_an_empty_build_prop(adb):
    adb.put(BP, b"")
    res = adb.hook("post")
    assert res.returncode != 0
    assert "missing or empty" in res.stdout
    assert "reboot" not in adb.calls()


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection"])
def test_preinstall_passes_on_a_complete_unit(tmp_path, pkg):
    unit = Unit(tmp_path, PACKAGES / pkg)
    unit.put(BP, STOCK_BP)
    assert unit.hook("pre").returncode == 0


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection"])
def test_preinstall_refuses_a_unit_without_build_prop(tmp_path, pkg):
    unit = Unit(tmp_path, PACKAGES / pkg)
    res = unit.hook("pre")
    assert res.returncode != 0
    assert "build.prop missing" in res.stdout


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection"])
def test_preinstall_refuses_a_unit_without_the_cli(tmp_path, pkg):
    unit = Unit(tmp_path, PACKAGES / pkg)
    unit.put(BP, STOCK_BP)
    unit.path("/system/xbin/nemesis").unlink()
    res = unit.hook("pre")
    assert res.returncode != 0
    assert "nemesis missing" in res.stdout


def test_wireless_preinstall_refuses_a_missing_payload(wifi):
    (wifi.pkg / "files" / "permissions" / WIFI_XMLS[1]).unlink()
    res = wifi.hook("pre")
    assert res.returncode != 0
    assert WIFI_XMLS[1] in res.stdout


def test_wireless_install_applies_every_change_and_reboots(wifi):
    assert wifi.hook("post").returncode == 0
    prop = wifi.read(BP).decode().splitlines()
    assert "persist.ium.support=carplay" in prop and "persist.wifi.state=3" in prop
    assert b"p2p_disabled=0\n" in wifi.read(WPA) and b"p2p_disabled=1" not in wifi.read(WPA)
    assert wifi.read(OVERLAY) == b"a=1\n"
    for name in WIFI_XMLS:
        assert wifi.read(f"{PERM}/{name}") == (wifi.pkg / "files" / "permissions" / name).read_bytes()
    assert wifi.read("wifi_on").strip() == b"1"
    assert "am force-stop com.daudio.app.phoneprojection" in wifi.calls()
    assert "am force-stop com.daudio.app.dipo" in wifi.calls()
    assert "reboot" in wifi.calls()


def test_wireless_install_comments_only_live_wireless_feature_lines(wifi):
    wifi.hook("post")
    assert wifi.read(FEATURE).decode().splitlines() == [
        "# head",
        "-/a/b.xml",
        "#-/x/androidauto_wireless.xml",
        "#-/x/carplay_wireless.xml",
        "#-/x/carplay_wireless.xml",
    ]


def test_wireless_install_patches_a_crlf_feature_file_and_keeps_its_line_endings(wifi):
    """Given a featurexml.cfg whose lines end in CRLF, as nq5_us variants ship
    (Sources/packages/wireless-projection/scripts/common.sh, rewrite_featurexml)
    When the postinstall hook runs
    Then the wireless lines are still matched, and every line keeps its CRLF ending
    """
    wifi.put(FEATURE, b"# head\r\n-/a/b.xml\r\n-/x/carplay_wireless.xml\r\n")
    assert wifi.hook("post").returncode == 0
    assert wifi.read(FEATURE) == b"# head\r\n-/a/b.xml\r\n#-/x/carplay_wireless.xml\r\n"


def test_wireless_uninstall_restores_a_crlf_feature_file_byte_for_byte(wifi):
    """Given a CRLF featurexml.cfg that the postinstall hook patched
    When the preuninstall hook restores it
    Then the unit's own bytes, CRLF endings included, are back
    """
    stock = b"# head\r\n-/x/androidauto_wireless.xml\r\n"
    wifi.put(FEATURE, stock)
    wifi.hook("post")
    assert wifi.hook("preun").returncode == 0
    assert wifi.read(FEATURE) == stock


def test_wireless_install_leaves_a_feature_file_with_no_wireless_entry(wifi):
    wifi.put(FEATURE, b"-/a/b.xml\n")
    assert wifi.hook("post").returncode == 0
    assert wifi.read(FEATURE) == b"-/a/b.xml\n"
    assert wifi.exists(f"{WIFI_BACKUP}/featurexml.absent")


def test_wireless_install_patches_every_variant(wifi):
    second = "/system/onebin/variant2/featurexml.cfg"
    wifi.put(second, b"-/x/carplay_wireless.xml\n")
    wifi.hook("post")
    assert wifi.read(second) == b"#-/x/carplay_wireless.xml\n"
    wifi.hook("preun")
    assert wifi.read(second) == b"-/x/carplay_wireless.xml\n"


def test_wireless_uninstall_restores_the_units_own_files(wifi):
    before = wifi.snapshot("system")
    wifi.put("wifi_on", b"0\n")
    wifi.put("props/persist.wifi.state", b"1\n")
    wifi.hook("post")
    assert wifi.hook("preun").returncode == 0
    assert wifi.snapshot("system") == before
    assert wifi.read("wifi_on").strip() == b"0"
    assert wifi.read("props/persist.wifi.state").strip() == b"1"
    assert not wifi.exists(WIFI_BACKUP)


def test_wireless_uninstall_removes_feature_xmls_absent_at_install(wifi):
    wifi.hook("post")
    assert wifi.exists(f"{PERM}/{WIFI_XMLS[2]}")
    wifi.hook("preun")
    assert not wifi.exists(f"{PERM}/{WIFI_XMLS[2]}")
    assert wifi.read(f"{PERM}/{WIFI_XMLS[0]}") == b"<stock/>\n"


def test_wireless_uninstall_clears_a_setting_that_was_unset(wifi):
    wifi.hook("post")
    wifi.hook("preun")
    assert not wifi.exists("wifi_on")


def test_wireless_skips_wpa_files_that_are_absent(wifi):
    wifi.path(WPA).unlink()
    wifi.path(OVERLAY).unlink()
    assert wifi.hook("post").returncode == 0
    assert not wifi.exists(WPA) and not wifi.exists(OVERLAY)
    assert wifi.hook("preun").returncode == 0
    assert not wifi.exists(WPA) and not wifi.exists(OVERLAY)


def test_wireless_failed_restore_keeps_the_backup(wifi):
    wifi.hook("post")
    wifi.path(f"{WIFI_BACKUP}/wpa_supplicant.conf").unlink()
    res = wifi.hook("preun")
    assert res.returncode != 0
    assert wifi.exists(f"{WIFI_BACKUP}/saved")


def test_wireless_rejects_a_tampered_feature_path_in_the_snapshot_list(wifi):
    wifi.hook("post")
    victim = wifi.put("/system/victim.cfg", b"keep\n")
    listing = wifi.path(f"{WIFI_BACKUP}/featurexml.list")
    listing.write_bytes(f"variant1 {wifi.root}/system/victim.cfg\n".encode())
    res = wifi.hook("preun")
    assert res.returncode != 0
    assert "bad featurexml path" in res.stdout
    assert victim.read_bytes() == b"keep\n"


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection"])
def test_backup_removal_refuses_any_other_directory(tmp_path, pkg):
    unit = Unit(tmp_path, PACKAGES / pkg)
    keep = unit.put("/data/keep/file", b"x")
    res = unit.functions(f'BACKUP_DIR="{unit.root}/data/keep"\nremove_backup_dir\necho rc=$?')
    assert "rc=1" in res.stdout
    assert "refusing to remove" in res.stdout
    assert keep.read_bytes() == b"x"


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection"])
def test_snapshot_refuses_any_other_directory(tmp_path, pkg):
    unit = Unit(tmp_path, PACKAGES / pkg)
    unit.put(BP, STOCK_BP)
    res = unit.functions(f'BACKUP_DIR="{unit.root}/data/keep"\nsnapshot_all\necho rc=$?')
    assert "rc=1" in res.stdout
    assert "bad backup dir" in res.stdout


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection"])
def test_commit_refuses_to_replace_a_file_with_nothing(tmp_path, pkg):
    unit = Unit(tmp_path, PACKAGES / pkg)
    target = unit.put("/system/file", b"keep\n")
    unit.put("/data/empty", b"")
    res = unit.functions(f'commit_tmp "{unit.root}/system/file" "{unit.root}/data/empty"\necho rc=$?')
    assert "rc=1" in res.stdout
    assert target.read_bytes() == b"keep\n"
    assert not unit.exists("/data/empty")


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection"])
def test_set_prop_line_replaces_in_place_and_appends_when_absent(tmp_path, pkg):
    unit = Unit(tmp_path, PACKAGES / pkg)
    target = unit.put("/system/p", b"a=1\nk=old\nb=2\n")
    unit.put("/data/b/keep", b"")
    body = (
        f'BACKUP_DIR="{unit.root}/data/b"\n'
        f'set_prop_line "{unit.root}/system/p" k new\n'
        f'set_prop_line "{unit.root}/system/p" z 9\n'
    )
    unit.functions(body)
    assert target.read_bytes() == b"a=1\nk=new\nb=2\nz=9\n"


@pytest.mark.parametrize("pkg", ["adb-tcp", "wireless-projection"])
def test_prop_key_match_is_exact(tmp_path, pkg):
    # ro.a must not match ro.ab=1.
    unit = Unit(tmp_path, PACKAGES / pkg)
    target = unit.put("/system/p", b"ro.ab=1\n")
    unit.put("/data/b/keep", b"")
    unit.functions(f'BACKUP_DIR="{unit.root}/data/b"\nset_prop_line "{unit.root}/system/p" ro.a 2\n')
    assert target.read_bytes() == b"ro.ab=1\nro.a=2\n"
