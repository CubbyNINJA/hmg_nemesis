"""The edges of the fixes for G2, G7, G8, G11, G15, G16, G18, D3 and D4, beyond each bug's own scenario."""
from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import time
import zipfile

import pytest

from helpers import SOURCES, builder_cmd
from native_unit import make_zip, wait_for
from test_native_update import installed, update_zip  # noqa: F401

SVC = "my.tool/d"


def service(nem, mode="restart", script="sleep 60\n"):
    sections = f"[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode={mode}\n"
    return nem.run("install", nem.package(log_hooks=False, sections=sections, files={"svc/d.sh": script}))


def status(nem):
    return nem.run("service", "status", SVC).stdout


# --- G2: a failed update puts the old package back


def old_and_broken(nem):
    """An installed package with a service and a logging postinstall, and an update that fails its preinstall."""
    log = f'echo post >> "{nem.root}/old-post"\nexit 0\n'
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=background\n"
    files = {"svc/d.sh": "sleep 60\n", "scripts/post.sh": log, "old.txt": "x"}
    extra = "postinstall=scripts/post.sh\n"
    assert nem.run("install", nem.package(log_hooks=False, extra_meta=extra, sections=sections, files=files)).returncode == 0
    return make_zip(nem.workdir / "bad.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\npreinstall=scripts/p.sh\n", "scripts/p.sh": "exit 9\n"})


def test_a_failed_update_runs_the_old_package_again(nem):
    """Given an update whose preinstall fails, after the old package's uninstall hooks have run
    When the update fails
    Then the old tree is back, its postinstall ran again, and its service is running
    """
    bad = old_and_broken(nem)
    assert (nem.root / "old-post").read_text().count("post") == 1
    res = nem.run("install", "--update", bad)
    assert res.returncode == 9
    assert (nem.app() / "old.txt").exists()
    assert (nem.root / "old-post").read_text().count("post") == 2
    assert "|running" in status(nem)
    assert "my.tool|" in nem.db()
    assert SVC in nem.db("services.db")


def test_a_failed_update_leaves_nothing_set_aside(nem):
    bad = old_and_broken(nem)
    nem.run("install", "--update", bad)
    assert not (nem.path("data/nemesis/update-old") / "my.tool").exists()


def test_a_successful_update_leaves_nothing_set_aside(nem):
    nem.run("install", nem.package(files={"old.txt": "x"}))
    assert nem.run("install", "--update", nem.package(files={"new.txt": "y"})).returncode == 0
    assert not (nem.path("data/nemesis/update-old") / "my.tool").exists()
    assert not (nem.app() / "old.txt").exists()


def test_a_failed_postinstall_on_an_update_keeps_the_new_package(nem):
    """Given an update recorded in packages.db whose postinstall then fails (04-Packages.md section 2)
    When the update finishes
    Then it reports failure and the new package stays, as for a first install
    """
    nem.run("install", nem.package(files={"old.txt": "x"}))
    bad = nem.package(files={"new.txt": "y", "scripts/post.sh": "exit 4\n"}, name="n.zip")
    assert nem.run("install", "--update", bad).returncode == 4
    assert (nem.app() / "new.txt").exists()
    assert not (nem.app() / "old.txt").exists()


def test_the_old_tree_is_gone_before_postinstall_runs(nem):
    """Given an update whose postinstall reboots, as the shipped packages' do, so nothing runs after it
    When that postinstall starts
    Then the old tree has already been deleted, so it cannot be left in /data
    """
    nem.run("install", nem.package(files={"old.txt": "x"}))
    keep = nem.path("data/nemesis/update-old/my.tool")
    seen = nem.root / "seen"
    script = f'[ -e "{keep}" ] && echo left > "{seen}" || echo gone > "{seen}"\nexit 0\n'
    assert nem.run("install", "--update", nem.package(files={"scripts/post.sh": script}, name="n.zip")).returncode == 0
    assert seen.read_text().strip() == "gone"


def died_mid_update(nem):
    """The state a power cut leaves: listed in packages.db, with the tree set aside and no services."""
    log = f'echo post >> "{nem.root}/old-post"\nexit 0\n'
    nem.run("install", nem.package(log_hooks=False, extra_meta="postinstall=scripts/post.sh\n", files={"scripts/post.sh": log, "old.txt": "x"}))
    keep = nem.path("data/nemesis/update-old/my.tool")
    keep.parent.mkdir(parents=True, exist_ok=True)
    nem.app().rename(keep)
    return keep


def test_the_service_manager_recovers_an_update_that_died(nem):
    """Given an update that died before recording the new package
    When the service manager starts at the next boot
    Then the old package is back and its postinstall has run again
    """
    keep = died_mid_update(nem)
    nem.daemon("svcmgr")
    assert wait_for((nem.app() / "old.txt").exists, 10), nem.log()
    assert not keep.exists()
    assert wait_for(lambda: (nem.root / "old-post").read_text().count("post") == 2, 5)
    assert "did not finish" in nem.log()


def test_a_retry_after_a_died_update_keeps_the_old_package_if_it_fails(nem):
    """Given an update that died, and a retry with a bad zip, which once deleted the only good copy"""
    died_mid_update(nem)
    bad = make_zip(nem.workdir / "bad.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\npreinstall=scripts/p.sh\n", "scripts/p.sh": "exit 9\n"})
    assert nem.run("install", "--update", bad).returncode == 9
    assert (nem.app() / "old.txt").exists()


def test_a_second_update_waits_for_the_first(nem):
    """Given an update running, whose old tree may be the only good copy
    When another update starts
    Then it is refused and the set-aside tree is left alone, while a first install still works
    """
    nem.run("install", nem.package(files={"old.txt": "x"}))
    keep = nem.path("data/nemesis/update-old/other")
    keep.mkdir(parents=True)
    with open(nem.path("data/nemesis/update.lock"), "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        res = nem.run("install", "--update", nem.package(files={"new.txt": "y"}, name="n.zip"))
        assert res.returncode == 1
        assert "another update is running" in res.stderr
        assert (nem.app() / "old.txt").exists()
        assert keep.exists()
        fresh = nem.run("install", make_zip(nem.workdir / "f.zip", {"NEMESIS.data": "[nemesis]\nid=fresh.tool\n"}))
        assert fresh.returncode == 0
        assert keep.exists()


@pytest.mark.parametrize("name", ["packages.db", "services.db"])
def test_databases_are_not_world_writable_whatever_the_umask(nem, name):
    """Given a caller with umask 0, as the root channel's workers have"""
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=background\n"
    previous = os.umask(0)
    try:
        nem.run("install", nem.package(log_hooks=False, sections=sections, files={"svc/d.sh": "exit 0\n"}))
    finally:
        os.umask(previous)
    assert stat.S_IMODE(nem.path(f"data/nemesis/{name}").stat().st_mode) == 0o644


def test_files_an_update_places_keep_their_labels(installed):  # noqa: F811
    """Given a unit with SELinux labels, whose placed files would otherwise take the folder's default
    When an update replaces the core files
    Then each new file gets the old file's label before it goes into place
    """
    nem = installed
    nem.path("labels_accepted").write_text("")
    assert nem.run("update", update_zip(nem, files={"core/NEMESIS.apk": "new"})).returncode == 0
    labelled = nem.path("labels.log").read_text()
    assert "system/xbin/nemesis.nemesis-new" in labelled
    assert "system/app/NEMESIS.apk.nemesis-new" in labelled


def test_a_restored_quickboot_file_gets_its_mode_back(installed):  # noqa: F811
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n")
    qb.chmod(0o750)
    assert nem.run("update", update_zip(nem, files={"scripts/migrate.sh": f'rm "{qb}"\nexit 1\n'})).returncode == 10
    assert stat.S_IMODE(qb.stat().st_mode) == 0o750


def test_a_restored_quickboot_file_gets_its_label_back(installed):  # noqa: F811
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n")
    nem.path("labels_accepted").write_text("")
    assert nem.run("update", update_zip(nem, files={"scripts/migrate.sh": f'rm "{qb}"\nexit 1\n'})).returncode == 10
    assert any(line.endswith("system/etc/qb_runtime.sh") for line in nem.path("labels.log").read_text().splitlines())


def test_a_stick_reinstall_stops_when_the_old_package_will_not_uninstall(nem):
    """Given an installed package whose preuninstall fails, and a stick with a new copy (04-Packages.md section 2)
    When the user confirms
    Then the failure is reported and the old package stays, as it did when the stick uninstalled first
    """
    extra = "preuninstall=scripts/u.sh\n"
    nem.run("install", nem.package(log_hooks=False, extra_meta=extra, files={"scripts/u.sh": "exit 1\n", "old.txt": "x"}))
    nem.answer_dialogs("yes")
    new = make_zip(nem.workdir / "n.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\n", "new.txt": "y"})
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": new.read_bytes()})
    nem.daemon("usb-handler")
    assert wait_for(lambda: "nemesis_mode result_fail" in nem.calls(), 25), nem.log()
    assert (nem.app() / "old.txt").exists()


def test_the_store_updates_with_rollback():
    """Given Manager's Store updating an installed package (no host test can run this Java)
    When it installs the new version
    Then it asks for --update, so a bad zip keeps the old package
    """
    source = (SOURCES / "manager/src/com/nemesis/manager/DialogActivity.java").read_text(encoding="utf-8")
    body = source[source.index("private void runStoreInstall") :]
    body = body[: body.index("}).start();")]
    assert '"--update"' in body
    assert '"uninstall"' not in body


# --- G7, G12: a stop lasts


def test_a_stopped_service_stays_stopped_when_the_service_manager_restarts(nem):
    service(nem)
    nem.run("service", "stop", SVC)
    nem.daemon("svcmgr")
    time.sleep(3)
    assert "|stopped" in status(nem)


def test_starting_a_stopped_service_lets_it_restart_again(nem):
    """Given a service stopped by hand and then started by hand
    When it dies
    Then the service manager restarts it again
    """
    service(nem)
    nem.run("service", "stop", SVC)
    assert nem.run("service", "start", SVC).returncode == 0
    nem.daemon("svcmgr")
    pid = status(nem).strip().split("|")[3]
    subprocess.run(["kill", "-9", pid])
    assert wait_for(lambda: "|running" in status(nem) and status(nem).strip().split("|")[3] != pid, 10)


def test_a_new_boot_clears_a_stop(nem):
    """Given a stop marker in tmpfs, which a reboot empties"""
    service(nem)
    nem.run("service", "stop", SVC)
    for marker in nem.path("dev").glob(".nemesis-stopped-*"):
        marker.unlink()
    nem.daemon("svcmgr")
    assert wait_for(lambda: "|running" in status(nem), 10)


def test_the_locked_handler_cannot_be_stopped_for_good(nem):
    # The seeded script execs the unit's literal /system/xbin/nemesis, so it dies at once here. The restarts show it.
    restarts = "restart usb-handler/usb-handler (locked)"
    nem.daemon("svcmgr")
    assert wait_for(lambda: restarts in nem.log(), 10)
    nem.run("service", "stop", "usb-handler/usb-handler")
    assert list(nem.path("dev").glob(".nemesis-stopped-*")) == []
    before = nem.log().count(restarts)
    assert wait_for(lambda: nem.log().count(restarts) > before, 10)


def test_stopping_an_unknown_service_leaves_no_marker(nem):
    nem.run("service", "stop", "no.such/service")
    assert list(nem.path("dev").glob(".nemesis-stopped-*")) == []


def test_uninstall_clears_the_stop(nem):
    service(nem)
    nem.run("service", "stop", SVC)
    assert nem.run("uninstall", "my.tool").returncode == 0
    assert list(nem.path("dev").glob(".nemesis-stopped-*")) == []


def test_a_failed_uninstall_starts_the_service_again_and_it_keeps_restarting(nem):
    """Given a preuninstall that fails, so the package stays and its services start again"""
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=restart\n"
    files = {"svc/d.sh": "sleep 60\n", "scripts/preun.sh": "exit 1\n"}
    nem.run("install", nem.package(sections=sections, files=files))
    assert nem.run("uninstall", "my.tool").returncode == 5
    assert "|running" in status(nem)
    assert list(nem.path("dev").glob(".nemesis-stopped-*")) == []


# --- G11: a deferred service


def test_a_deferred_service_starts_only_once(nem):
    nem.android(up=False)
    sections = "[service.0]\nname=d\nkind=instance\npath=com.a/.Svc\nmode=background\n"
    nem.run("install", nem.package(log_hooks=False, sections=sections))
    nem.daemon("svcmgr")
    time.sleep(1)
    nem.android(up=True)
    assert wait_for(lambda: "com.a/.Svc" in nem.calls(), 8)
    time.sleep(4)
    assert nem.calls().count("com.a/.Svc") == 1


def test_a_deferred_once_service_starts_when_android_comes_up(nem):
    nem.android(up=False)
    sections = "[service.0]\nname=d\nkind=instance\npath=com.a/.Svc\nmode=once\n"
    nem.run("install", nem.package(log_hooks=False, sections=sections))
    assert "com.a/.Svc" not in nem.calls()
    nem.daemon("svcmgr")
    time.sleep(1)
    nem.android(up=True)
    assert wait_for(lambda: "com.a/.Svc" in nem.calls(), 8), nem.log()


def test_a_disabled_deferred_service_does_not_flood_the_log(nem):
    """Given a restart instance service at the restart limit, started by hand with Android down"""
    nem.android(up=False)
    sections = "[service.0]\nname=d\nkind=instance\npath=com.a/.Svc\nmode=restart\n"
    nem.run("install", nem.package(log_hooks=False, sections=sections))
    nem.path("data/nemesis/pids/my.tool+d.fails").write_text("5\n")
    nem.run("service", "start", SVC)
    nem.android(up=True)
    nem.daemon("svcmgr")
    time.sleep(7)
    # Once at startup and once when the deferral is handled. Unfixed, it repeats every 2 seconds.
    assert nem.log().count("disabled after") <= 2


# --- G8: what the remount intercept takes


@pytest.mark.parametrize(
    "command, ro",
    [
        ("mount -o remount,ro /system", True),
        ("mount -o ro,remount /system", True),
        ("mount -oremount,ro /system", True),
        ("/system/bin/mount -o remount,ro /system", True),
        ("mount -t ext4 -o remount,ro /dev/block/sys /system", True),
        ("mount -o remount ro /system", True),
        ("toolbox mount -o remount,ro /system", True),
        ("/system/bin/toolbox mount -o remount,ro /system", True),
        ("busybox mount -o remount,ro /system", True),
        ("mount -o remount,rw /system", False),
        ("mount -o remount /system", False),
        ("mount -o remount,rw /", False),
    ],
)
def test_a_plain_remount_is_intercepted_with_the_right_mode(nem, command, ro):
    """Given a plain remount, with /system read-only so the mount call is really made (02 section 4)"""
    (nem.root / "proc_mounts").write_text("/dev/block/sys /system ext4 rw,nosuid 0 0\n")
    xbin = nem.path("/system/xbin")
    if not ro:
        xbin.chmod(0o555)
    try:
        nem.run("root", "-c", command)
    finally:
        xbin.chmod(0o755)
    flags = int((nem.root / "mounts.log").read_text().splitlines()[-1].split()[-1])
    assert bool(flags & 1) == ro


@pytest.mark.parametrize(
    "command",
    [
        'echo "mount -o remount,rw /system"',
        "mount -o remount,rw /data",
        "mount -o rw /system",
        "umount -o remount /system",
        "notmount -o remount /system",
    ],
)
def test_other_commands_are_not_intercepted(nem, command):
    (nem.root / "proc_mounts").write_text("/dev/block/sys /system ext4 rw,nosuid 0 0\n")
    nem.run("root", "-c", command)
    assert not (nem.root / "mounts.log").exists()


# --- G15, G16, G14: reading a package


def test_a_zip_of_the_folder_still_supplies_its_manifest(nem):
    package = make_zip(nem.workdir / "p.zip", {"top/NEMESIS.data": "[nemesis]\nid=my.tool\n"})
    assert "id=my.tool\n" in nem.run("pkg-info", package).stdout


def test_a_nested_manifest_read_first_does_not_block_the_root_one(nem):
    package = make_zip(nem.workdir / "p.zip", {"A/NEMESIS.data": "[nemesis]\nid=first\n", "B/NEMESIS.data": "[nemesis]\nid=second\n", "NEMESIS.data": "[nemesis]\nid=root\n"})
    assert "id=root\n" in nem.run("pkg-info", package).stdout


def test_two_nested_manifests_keep_the_first(nem):
    package = make_zip(nem.workdir / "p.zip", {"A/NEMESIS.data": "[nemesis]\nid=first\n", "B/NEMESIS.data": "[nemesis]\nid=second\n"})
    assert "id=first\n" in nem.run("pkg-info", package).stdout


@pytest.mark.parametrize("name", ["nemesis.data", "NEMESIS.DATA", "Nemesis.Data"])
def test_a_manifest_in_any_case_is_renamed_on_install(nem, name):
    package = make_zip(nem.workdir / "p.zip", {name: "[nemesis]\nid=my.tool\n"})
    assert nem.run("install", package).returncode == 0
    assert (nem.app() / "NEMESIS.data").exists()
    assert "installed=1" in nem.run("pkg-info", "my.tool").stdout


def test_a_stored_member_with_a_size_mismatch_is_refused(nem):
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\n", "payload": b"A" * 64}, zipfile.ZIP_STORED)
    raw = bytearray(target.read_bytes())
    at = raw.rindex(b"PK\x01\x02")
    raw[at + 24] += 1
    target.write_bytes(raw)
    assert nem.run("install", target).returncode != 0
    assert "my.tool" not in nem.db()


def test_empty_members_pass_the_crc_check(nem):
    package = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\n", "empty": ""})
    assert nem.run("install", package).returncode == 0
    assert (nem.app() / "empty").read_text() == ""


# --- G3, D4: what a descriptor may hold


@pytest.mark.parametrize(
    "body",
    [
        "id=my.tool\nname=a|b\n",
        'id=my.tool\nname="a\nb"\n',
        "version=1.1\nid=my.tool\ntype=a|b\n",
        "id=my.tool\n[service.0]\nname=a|b\nkind=sh\npath=d.sh\n",
        "id=my.tool\n[service.0]\nname=d\nkind=sh\npath=d.sh\nmode=a|b\n",
        "id=my.tool\npreuninstall=a/../../b.sh\n",
        "id=my.tool\npostuninstall=..\n",
        "id=my.tool\n[app.0]\nkind=apk\npath=../a.apk\n",
    ],
)
def test_a_descriptor_that_would_break_a_database_row_or_climb_out_is_refused(nem, body):
    res = nem.run("pkg-info", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\n" + body}))
    assert res.returncode != 0
    assert "invalid package metadata" in res.stderr


@pytest.mark.parametrize("path", ["a..b/c.sh", "..c.sh", "c..sh"])
def test_two_dots_inside_a_name_are_allowed(nem, path):
    assert nem.run("pkg-info", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": f"[nemesis]\nid=my.tool\npostinstall={path}\n"})).returncode == 0


def test_the_app_runs_as_root_when_it_names_no_uid(nem):
    sections = "[app.0]\nkind=sh\npath=bin/a.sh\n"
    nem.run("install", nem.package(sections=sections, files={"bin/a.sh": "exit 0\n"}))
    target = nem.root / "ids.log"
    if target.exists():
        target.unlink()
    nem.run("pkg-launch", "my.tool")
    assert "uid 1000" not in nem.ids()


# --- The builder


def build(folder, tmp_path):
    return subprocess.run(builder_cmd(folder, "-o", tmp_path / "o.zip"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "NEMESIS.data").write_text("[nemesis]\nid=my.tool\n")
    return root


@pytest.mark.parametrize(
    "body, message",
    [
        ("name=a|b\n", "must not hold"),
        ("[service.0]\nname=a|b\nkind=instance\npath=c.a/.S\n", "must not hold"),
        ("postinstall=../x.sh\n", "must not use .."),
        ("[service.0]\nname=d\nkind=sh\npath=a/../../x.sh\n", "must not use .."),
        ("preinstall=scripts/gone.sh\n", "not in the package"),
    ],
)
def test_the_builder_refuses_what_the_unit_would_fail_on(folder, tmp_path, body, message):
    (folder / "NEMESIS.data").write_text("[nemesis]\nid=my.tool\n" + body)
    res = build(folder, tmp_path)
    assert res.returncode != 0
    assert message in res.stderr
    assert not (tmp_path / "o.zip").exists()


@pytest.mark.parametrize(
    "body",
    [
        "postinstall=N/A\n",
        "postinstall=/system/bin/abs.sh\n",
        "[service.0]\nname=d\nkind=instance\npath=com.a/.Svc\n",
    ],
    ids=["not-applicable", "absolute", "instance-component"],
)
def test_the_builder_does_not_look_for_paths_outside_the_package(folder, tmp_path, body):
    (folder / "NEMESIS.data").write_text("[nemesis]\nid=my.tool\n" + body)
    res = build(folder, tmp_path)
    assert res.returncode == 0, res.stderr


def test_the_builder_reads_a_byte_order_mark(folder, tmp_path):
    (folder / "NEMESIS.data").write_bytes(b"\xef\xbb\xbf[nemesis]\nid=my.tool\n")
    res = build(folder, tmp_path)
    assert res.returncode == 0, res.stderr


def test_the_builder_warns_when_it_strips_a_script(folder, tmp_path):
    (folder / "a.sh").write_bytes(b"#!/system/bin/sh\r\nexit 0\r\n")
    res = build(folder, tmp_path)
    assert res.returncode == 0
    assert "a.sh: removed carriage returns" in res.stdout
    assert zipfile.ZipFile(tmp_path / "o.zip").read("a.sh") == b"#!/system/bin/sh\nexit 0\n"


@pytest.mark.parametrize("name, data", [("a.bin", b"#!x\r\n"), ("a.sh", b"\x00\r\n"), ("a.xml", b"<a>\r\n</a>")])
def test_the_builder_leaves_everything_else_as_it_is(folder, tmp_path, name, data):
    (folder / name).write_bytes(data)
    assert build(folder, tmp_path).returncode == 0
    assert zipfile.ZipFile(tmp_path / "o.zip").read(name) == data


# --- G33: what a refused zip says


def test_a_damaged_zip_is_not_reported_as_having_no_manifest(nem):
    """Given a zip whose manifest is fine but whose other member fails its CRC
    When it is read
    Then the message points at the zip, and a zip with truly no manifest still says so
    """
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\n", "payload": b"A" * 64}, zipfile.ZIP_STORED)
    target.write_bytes(target.read_bytes().replace(b"A" * 64, b"A" * 63 + b"B"))
    res = nem.run("install", target)
    assert res.returncode != 0
    assert "zip is damaged" in res.stderr
    assert "no NEMESIS.data" in nem.run("install", make_zip(nem.workdir / "q.zip", {"other": "x"})).stderr


def test_an_unsafe_member_path_is_reported_as_such(nem):
    res = nem.run("install", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\n", "../x": "y"}))
    assert res.returncode != 0
    assert "unsafe" in res.stderr
