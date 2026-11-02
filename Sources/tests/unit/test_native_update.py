"""`nemesis update`, root certificates and make_update_zip.py.

Sources: 02-Debugging-Execution-Interface.md, 05-Run-Scripts.md section 2, and the exit codes at the
top of Sources/native/update.c, which no document lists.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
import zipfile

import pytest

from helpers import NATIVE, SOURCES, known_bug
from native_unit import make_zip, wait_for

VERSION = re.search(r'#define NEMESIS_VERSION "([^"]+)"', (NATIVE / "nemesis.h").read_text(encoding="utf-8")).group(1)
NEWER = "999.0"
OLDER = "0.1"
TOOL = SOURCES / "tools" / "make_update_zip.py"
CA = "aabbccdd.0"


def md5(data) -> str:
    return hashlib.md5(data if isinstance(data, bytes) else data.encode()).hexdigest()


def new_binary(nem) -> bytes:
    """Still runs, and its bytes differ from the installed copy."""
    return nem.binary.read_bytes() + b"\nNEW"


def update_zip(nem, version=NEWER, *, meta="", files=None, binary=None, sums=None, extra_sums="", identity="id=nemesis.core\ntype=update\n"):
    """A valid update package. `sums` replaces MD5SUMS, or removes it when False."""
    members = {"core/nemesis": new_binary(nem) if binary is None else binary, **(files or {})}
    version_line = f"package-version={version}\n" if version else ""
    members["NEMESIS.data"] = f"[nemesis]\nversion=1.1\n{identity}name=NEMESIS\n{version_line}{meta}"
    if sums is None:
        members["MD5SUMS"] = "".join(f"{md5(data)}  {name}\n" for name, data in members.items()) + extra_sums
    elif sums is not False:
        members["MD5SUMS"] = sums(members) if callable(sums) else sums
    return make_zip(nem.workdir / "u.zip", members)


@pytest.fixture
def locked_dirs(nem):
    """Read-only directories for a test, made writable again so the next reset can delete them."""
    dirs = []

    def lock(rel):
        target = nem.path(rel)
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(0o555)
        dirs.append(target)
        return target

    yield lock
    for target in dirs:
        target.chmod(0o755)


@pytest.fixture
def installed(nem):
    """The core files an installed unit has, apart from the binary the harness already placed."""
    nem.path("system/app/NEMESIS.apk").write_text("old apk")
    nem.path("system/etc/install-recovery.sh").write_text("old recovery")
    return nem


def unchanged(nem) -> bool:
    return nem.cli.read_bytes() == nem.binary.read_bytes()


def last_result(nem) -> str:
    target = nem.path("data/nemesis/update/last-result")
    return target.read_text() if target.exists() else ""


# --- The update itself


def test_update_replaces_the_core_files_and_reboots(installed):
    """Given a newer update package with a binary, APK and recovery script
    When `nemesis update` runs
    Then all three are replaced with the documented modes, the result is recorded, and the unit reboots
    """
    nem = installed
    files = {"core/NEMESIS.apk": "new apk", "core/install-recovery.sh": "new recovery"}
    res = nem.run("update", update_zip(nem, files=files))
    assert res.returncode == 0, res.stderr
    assert f"ok updated {NEWER}" in res.stdout
    assert nem.cli.read_bytes() == new_binary(nem)
    assert nem.path("system/app/NEMESIS.apk").read_text() == "new apk"
    assert nem.path("system/etc/install-recovery.sh").read_text() == "new recovery"
    assert stat.S_IMODE(nem.cli.stat().st_mode) == 0o4755
    assert stat.S_IMODE(nem.path("system/app/NEMESIS.apk").stat().st_mode) == 0o644
    assert stat.S_IMODE(nem.path("system/etc/install-recovery.sh").stat().st_mode) == 0o755
    assert last_result(nem) == f"ok {VERSION} -> {NEWER}\n"
    assert not nem.path("data/nemesis/update/stage").exists()
    assert "reboot" in nem.calls()


def test_no_reboot_restarts_the_watchdog_instead(installed):
    nem = installed
    res = nem.run("update", "--no-reboot", update_zip(nem))
    assert res.returncode == 0, res.stderr
    assert "reboot" not in nem.calls()
    assert wait_for(lambda: nem.path("data/nemesis/watchdog.pid").exists())


def test_core_files_the_package_leaves_out_stay_as_they_are(installed):
    nem = installed
    assert nem.run("update", update_zip(nem)).returncode == 0
    assert nem.path("system/app/NEMESIS.apk").read_text() == "old apk"


def test_the_replaced_files_are_kept_in_the_backup(installed):
    nem = installed
    assert nem.run("update", update_zip(nem, files={"core/NEMESIS.apk": "new"})).returncode == 0
    backup = nem.path("data/nemesis/update/backup")
    assert (backup / "NEMESIS.apk").read_text() == "old apk"
    assert (backup / "nemesis").read_bytes() == nem.binary.read_bytes()
    assert (backup / "version").read_text() == f"{VERSION}\n"


@pytest.mark.parametrize("version, code, text", [(OLDER, 7, "older"), (VERSION, 9, "already up to date")])
def test_a_version_that_is_not_newer_is_refused(installed, version, code, text):
    nem = installed
    res = nem.run("update", update_zip(nem, version))
    assert res.returncode == code
    assert text in res.stdout + res.stderr
    assert unchanged(nem)


@pytest.mark.parametrize("version", [OLDER, VERSION])
def test_force_installs_a_version_that_is_not_newer(installed, version):
    nem = installed
    assert nem.run("update", "--force", update_zip(nem, version)).returncode == 0
    assert nem.cli.read_bytes() == new_binary(nem)


def test_an_incompatible_update_needs_the_risk_accepted(installed):
    """Given an update marked for another OS and model
    When it runs without and then with --accept-risk
    Then it exits 20 and changes nothing, and then it installs
    """
    nem = installed
    package = update_zip(nem, meta="os=none\nmodels=none\n")
    res = nem.run("update", package)
    assert res.returncode == 20
    assert "compat=incompatible" in res.stdout
    assert unchanged(nem)
    assert nem.run("update", "--accept-risk", package).returncode == 0


def test_update_needs_a_zip(nem):
    assert nem.run("update").returncode == 1
    assert nem.run("update", nem.workdir / "absent.zip").returncode == 1


@pytest.mark.parametrize(
    "identity, version",
    [
        ("id=other.tool\ntype=update\n", NEWER),
        ("id=nemesis.core\ntype=application\n", NEWER),
        ("id=nemesis.core\ntype=update\n", ""),
    ],
    ids=["wrong-id", "wrong-type", "no-version"],
)
def test_a_package_that_is_not_an_update_is_refused(installed, identity, version):
    nem = installed
    assert nem.run("update", update_zip(nem, version, identity=identity)).returncode == 1
    assert unchanged(nem)


def test_a_zip_that_is_not_a_zip_is_refused(installed):
    nem = installed
    bad = nem.workdir / "bad.zip"
    bad.write_text("not a zip")
    assert nem.run("update", bad).returncode == 1
    assert unchanged(nem)


# --- MD5SUMS


def replace_line(old, new):
    def build(members):
        return "".join(f"{md5(data)}  {name}\n" for name, data in members.items()).replace(old, new)

    return build


@pytest.mark.parametrize(
    "sums, extra, message",
    [
        (False, "", "MD5SUMS missing"),
        ("zzz  core/nemesis\n", "", "bad MD5SUMS line"),
        ("z" * 32 + "  core/nemesis\n", "", "bad MD5SUMS line"),
        ("G" * 32 + "  core/nemesis\n", "", "bad MD5SUMS line"),
        ("/" * 32 + "  core/nemesis\n", "", "bad MD5SUMS line"),
        (replace_line("  core/nemesis", "core/nemesis"), "", "bad MD5SUMS line"),
        (None, f"{md5('x')}  ../outside\n", "bad path"),
        (None, f"{md5('x')}  /etc/outside\n", "bad path"),
        (None, f"{md5('x')}  listed-but-absent\n", "md5 mismatch"),
        (lambda m: f"{md5('other')}  core/nemesis\n{md5(m['NEMESIS.data'])}  NEMESIS.data\n", "", "md5 mismatch"),
        (lambda m: f"{md5(m['NEMESIS.data'])}  NEMESIS.data\n", "", "core/nemesis missing"),
        (lambda m: f"{md5(m['core/nemesis'])}  core/nemesis\n", "", "listed in MD5SUMS"),
    ],
    ids=["missing", "bad-hash", "letter-hash", "upper-letter-hash", "symbol-hash", "no-separator", "parent-path", "absolute-path", "absent-file", "wrong-hash", "no-binary-line", "unlisted-file"],
)
def test_a_package_that_fails_its_checksums_changes_nothing(installed, sums, extra, message):
    """Given an update package whose MD5SUMS does not vouch for every file (update.c:172)
    When `nemesis update` runs
    Then it exits 8, names the problem, and leaves the unit as it was
    """
    nem = installed
    res = nem.run("update", update_zip(nem, sums=sums, extra_sums=extra))
    assert res.returncode == 8
    assert message in res.stderr
    assert unchanged(nem)
    assert not nem.path("data/nemesis/update/stage").exists()
    assert "reboot" not in nem.calls()


@known_bug("G24", "a line listed twice makes up for an unlisted file, so that file is never checked")
def test_a_file_listed_twice_does_not_excuse_an_unlisted_one(installed):
    """Given MD5SUMS that lists one file twice and leaves another out (update.c:172)
    When `nemesis update` runs
    Then it exits 8, because every file must be listed and match
    """
    nem = installed
    sums = lambda m: (
        f"{md5(m['core/nemesis'])}  core/nemesis\n{md5(m['NEMESIS.data'])}  NEMESIS.data\n"
        f"{md5(m['NEMESIS.data'])}  NEMESIS.data\n"
    )
    assert nem.run("update", update_zip(nem, files={"scripts/migrate.sh": "exit 0\n"}, sums=sums)).returncode == 8
    assert unchanged(nem)


def test_checksum_lines_accept_comments_uppercase_and_the_binary_marker(installed):
    nem = installed

    def sums(members):
        lines = ["# made by hand\n", "\n"]
        for name, data in members.items():
            lines.append(f"{md5(data).upper()} *{name}\r\n")
        return "".join(lines)

    res = nem.run("update", update_zip(nem, sums=sums))
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("name", ["core/..notes", "core/a..b", "core/notes.."])
def test_a_file_name_with_two_dots_is_not_a_parent_path(installed, name):
    """Given an MD5SUMS line whose name holds two dots but no parent step (update.c:133)
    When `nemesis update` checks it
    Then it is treated as an ordinary file, so what fails is the checksum and not the path
    """
    nem = installed
    res = nem.run("update", update_zip(nem, extra_sums=f"{md5('x')}  {name}\n"))
    assert res.returncode == 8
    assert "md5 mismatch" in res.stderr


@pytest.mark.parametrize("path", ["core/../x", ".."])
def test_a_parent_step_anywhere_in_a_path_is_refused(installed, path):
    nem = installed
    res = nem.run("update", update_zip(nem, extra_sums=f"{md5('x')}  {path}\n"))
    assert res.returncode == 8
    assert "bad path" in res.stderr


def test_a_zip_that_will_not_unpack_is_refused(installed):
    """Given an update whose metadata reads fine but where one member is also a folder of another
    When `nemesis update` runs
    Then it exits 1, removes its staging folder and leaves the unit as it was
    """
    nem = installed
    res = nem.run("update", update_zip(nem, files={"clash": "x", "clash/inner": "y"}))
    assert res.returncode == 1
    assert "unzip failed" in res.stderr
    assert not nem.path("data/nemesis/update/stage").exists()
    assert unchanged(nem)


@pytest.mark.parametrize("binary, message", [(b"#!/bin/sh\nexit 0\n", "not an ELF"), (b"", "not an ELF"), (b"\x7fELFnot really", "does not run")])
def test_a_binary_that_cannot_run_is_never_installed(installed, binary, message):
    """Given a package whose checksums are right but whose binary would brick the unit (update.c:448)
    When `nemesis update` runs
    Then it exits 8 before it touches anything
    """
    nem = installed
    res = nem.run("update", update_zip(nem, binary=binary))
    assert res.returncode == 8
    assert message in res.stderr
    assert unchanged(nem)


# --- Failing part way


def test_a_read_only_system_changes_nothing(installed, locked_dirs):
    nem = installed
    locked_dirs("system/xbin")
    res = nem.run("update", "--no-reboot", update_zip(nem))
    assert res.returncode == 10
    assert "nothing was changed" in res.stderr
    assert unchanged(nem)


def test_a_failed_backup_stops_before_anything_changes(installed):
    nem = installed
    apk = nem.path("system/app/NEMESIS.apk")
    apk.chmod(0)
    try:
        res = nem.run("update", update_zip(nem))
    finally:
        apk.chmod(0o644)
    assert res.returncode == 1
    assert "backup of" in res.stderr
    assert unchanged(nem)


def test_a_failed_backup_of_the_qb_hook_stops_before_anything_changes(installed):
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n")
    qb.chmod(0)
    try:
        res = nem.run("update", update_zip(nem))
    finally:
        qb.chmod(0o644)
    assert res.returncode == 1
    assert unchanged(nem)


def test_a_failed_migration_restores_the_previous_version(installed):
    """Given an update whose migrate.sh fails after the files are in place (update.c:678)
    When `nemesis update` runs
    Then it exits 10, every core file is back, new ones are gone, and it does not reboot
    """
    nem = installed
    nem.path("system/app/NEMESIS.apk").unlink()
    files = {
        "core/NEMESIS.apk": "new apk",
        "core/install-recovery.sh": "new recovery",
        f"core/cacerts/{CA}": "cert",
        "scripts/migrate.sh": "exit 3\n",
    }
    res = nem.run("update", update_zip(nem, files=files))
    assert res.returncode == 10
    assert "previous version restored" in res.stdout
    assert unchanged(nem)
    assert stat.S_IMODE(nem.cli.stat().st_mode) == 0o4755
    assert nem.path("system/etc/install-recovery.sh").read_text() == "old recovery"
    assert not nem.path("system/app/NEMESIS.apk").exists()
    assert not nem.path(f"system/etc/security/cacerts/{CA}").exists()
    assert not nem.path("data/nemesis/owned-cacerts").exists()
    assert last_result(nem) == "failed restored\n"
    assert "update: failed; restore ok" in nem.log()
    assert "reboot" not in nem.calls()


def test_a_restore_that_cannot_remount_is_reported(installed, locked_dirs):
    """Given a failed migration that also leaves /system unwritable (update.c:416)
    When the restore starts
    Then it stops there and exits 11
    """
    nem = installed
    locked_dirs("system/xbin").chmod(0o755)
    script = f'chmod 555 "{nem.path("system/xbin")}"\nexit 3\n'
    res = nem.run("update", update_zip(nem, files={"scripts/migrate.sh": script}))
    assert res.returncode == 11
    assert "restore: remount rw failed" in nem.log()
    assert last_result(nem) == "failed restore-failed\n"


def test_a_target_that_cannot_be_replaced_fails_the_update(installed):
    """Given a directory where the Manager APK should go, so the final rename fails (update.c:390)
    When `nemesis update` runs
    Then it reports that and restores, rather than carrying on as if the file were placed
    """
    nem = installed
    apk = nem.path("system/app/NEMESIS.apk")
    apk.unlink()
    apk.mkdir()
    res = nem.run("update", update_zip(nem, files={"core/NEMESIS.apk": "new"}))
    assert res.returncode == 10
    assert "could not replace" in res.stderr
    assert unchanged(nem)


def test_a_failed_restore_is_reported_as_such(installed, locked_dirs):
    """Given a failed migration, and a restore that cannot write /system/etc
    When `nemesis update` runs
    Then it exits 11 and says the restore failed, rather than claiming the old version is back
    """
    nem = installed
    locked_dirs("system/etc").chmod(0o755)
    script = f'chmod 555 "{nem.path("system/etc")}"\nexit 3\n'
    res = nem.run("update", update_zip(nem, files={"scripts/migrate.sh": script}))
    assert res.returncode == 11
    assert "restore failed" in res.stdout
    assert last_result(nem) == "failed restore-failed\n"
    assert "update: failed; restore FAILED" in nem.log()


def test_a_file_that_cannot_be_placed_restores_the_previous_version(installed, locked_dirs):
    nem = installed
    nem.path("system/app/NEMESIS.apk").unlink()
    locked_dirs("system/app")
    res = nem.run("update", update_zip(nem, files={"core/NEMESIS.apk": "new"}))
    assert res.returncode == 10
    assert "could not replace" in res.stderr
    assert unchanged(nem)
    assert list(nem.path("system/xbin").glob("*.nemesis-new")) == []


def test_a_file_that_cannot_be_owned_by_root_is_not_placed(installed):
    nem = installed
    nem.path("chown_fails").write_text("")
    res = nem.run("update", update_zip(nem))
    # The restore uses the same placement, so with chown still failing it cannot succeed either.
    assert res.returncode == 11
    assert "could not replace" in res.stderr
    assert unchanged(nem)
    assert not nem.cli.with_name("nemesis.nemesis-new").exists()


def test_migrate_runs_as_root_in_the_package_root(installed):
    nem = installed
    log = nem.root / "migrated"
    files = {"scripts/migrate.sh": f'pwd > "{log}"\nexit 0\n'}
    assert nem.run("update", update_zip(nem, files=files)).returncode == 0
    assert log.read_text().strip().endswith("update/stage")


# --- QuickBoot hook


HOOK = "# NEMESIS_QB_HOOK\nif true; then\n  echo new\nfi\n"
OLD_HOOK = "# NEMESIS_QB_HOOK\nif true; then\n  echo old\nfi\n"


def test_update_swaps_the_quickboot_hook_and_keeps_the_rest(installed):
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n" + OLD_HOOK)
    assert nem.run("update", update_zip(nem, files={"core/qb_hook.sh": HOOK})).returncode == 0
    assert qb.read_text() == "stock\n" + HOOK


def test_an_unusable_hook_restores_the_quickboot_file(installed):
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n" + OLD_HOOK)
    res = nem.run("update", update_zip(nem, files={"core/qb_hook.sh": ""}))
    assert res.returncode == 10
    assert "QB hook" in res.stderr
    assert qb.read_text() == "stock\n" + OLD_HOOK
    assert unchanged(nem)


def test_a_restore_also_puts_the_quickboot_file_back(installed):
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n" + OLD_HOOK)
    files = {"core/qb_hook.sh": HOOK, "scripts/migrate.sh": "exit 1\n"}
    assert nem.run("update", update_zip(nem, files=files)).returncode == 10
    assert qb.read_text() == "stock\n" + OLD_HOOK


def test_a_filesystem_that_refuses_labels_does_not_stop_the_update(installed):
    """Given a unit where the QuickBoot file's label can be read but not set
    When an update replaces the hook
    Then the update still finishes, because the hook's contents are what matter (pkg.c:1135)
    """
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n" + OLD_HOOK)
    nem.path("labels_refused").write_text("")
    res = nem.run("update", update_zip(nem, files={"core/qb_hook.sh": HOOK}))
    assert res.returncode == 0, res.stderr + nem.log()
    assert qb.read_text() == "stock\n" + HOOK


def test_a_restore_does_not_fail_over_a_file_that_never_changed(installed):
    """Given a failed update that never touched the QuickBoot file, on a unit that refuses labels
    When the restore runs
    Then it reports success, because everything is back as it was (update.c:429)
    """
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n")
    nem.path("labels_refused").write_text("")
    res = nem.run("update", update_zip(nem, files={"scripts/migrate.sh": "exit 1\n"}))
    assert res.returncode == 10, res.stdout
    assert last_result(nem) == "failed restored\n"
    assert qb.read_text() == "stock\n"


def test_a_hook_too_large_to_read_whole_is_refused(installed):
    """Given a hook one byte over what qb_rewrite can read, and one exactly at the limit (pkg.c:1161)
    When the update applies each
    Then the first fails and restores, and the second lands whole
    """
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n")
    over = "# NEMESIS_QB_HOOK\n" + "#" * (8191 - 19) + "\n"
    assert len(over) == 8191
    assert nem.run("update", update_zip(nem, files={"core/qb_hook.sh": over})).returncode == 10
    assert qb.read_text() == "stock\n"
    fits = over[:-2] + "\n"
    assert nem.run("update", update_zip(nem, files={"core/qb_hook.sh": fits})).returncode == 0
    assert qb.read_text() == "stock\n" + fits


def test_an_update_stops_only_the_watchdog_and_service_manager(installed):
    """Given a USB handler running, which is another NEMESIS process (pkg.c:1219)
    When the update stops the daemons
    Then the handler is left alone, so it can report the result
    """
    nem = installed
    handler = nem.daemon("usb-handler")
    assert wait_for(lambda: "usb-handler start" in nem.log())
    assert nem.run("update", "--no-reboot", update_zip(nem)).returncode == 0
    assert handler.poll() is None


# --- Root certificates


def test_update_adds_only_certificates_the_unit_lacks(installed):
    """Given a unit with its own root certificate and a package bringing that one and a new one
    When the update runs
    Then the unit's copy is untouched, the new one is added, and only it is recorded as ours
    """
    nem = installed
    stock = nem.path(f"system/etc/security/cacerts/{CA}")
    stock.parent.mkdir(parents=True)
    stock.write_text("stock cert")
    files = {f"core/cacerts/{CA}": "ours", "core/cacerts/11223344.0": "ours", "core/cacerts/not-a-cert.pem": "no"}
    assert nem.run("update", update_zip(nem, files=files)).returncode == 0
    assert stock.read_text() == "stock cert"
    assert (stock.parent / "11223344.0").read_text() == "ours"
    assert not (stock.parent / "not-a-cert.pem").exists()
    assert nem.path("data/nemesis/owned-cacerts").read_text() == "11223344.0\n"


def certs(nem, names):
    source = nem.workdir / "certs"
    source.mkdir(exist_ok=True)
    for name in names:
        (source / name).write_text("cert")
    return source


def test_an_owned_certificate_put_back_is_recorded_once(nem):
    """Given two certificates NEMESIS added, one of which has since gone missing
    When the same set is installed again
    Then it is put back and the record does not list it twice (update.c:234)
    """
    source = certs(nem, ["11111111.0", "22222222.0"])
    nem.run("cacerts-install", source)
    owned = nem.path("data/nemesis/owned-cacerts")
    lines = owned.read_text().splitlines()
    assert sorted(lines) == ["11111111.0", "22222222.0"]
    for name in lines:
        nem.path(f"system/etc/security/cacerts/{name}").unlink()
    assert "ok added 2 root" in nem.run("cacerts-install", source).stdout
    assert sorted(owned.read_text().splitlines()) == ["11111111.0", "22222222.0"]


@pytest.mark.parametrize("name", ["aabbccdd.0", "a.9", "00000000.0"])
def test_cacerts_install_adds_well_named_files(nem, name):
    res = nem.run("cacerts-install", certs(nem, [name]))
    assert res.returncode == 0
    assert "ok added 1 root" in res.stdout
    assert stat.S_IMODE(nem.path(f"system/etc/security/cacerts/{name}").stat().st_mode) == 0o644


@pytest.mark.parametrize("name", ["AABBCCDD.0", "aabbccdd.x", "aabbccdd0", ".0", "aabbccddaabbccdd.0", "aabbccdg.0"])
def test_cacerts_install_ignores_other_names(nem, name):
    res = nem.run("cacerts-install", certs(nem, [name]))
    assert res.returncode == 0
    assert "ok added 0 root" in res.stdout
    assert not nem.path(f"system/etc/security/cacerts/{name}").exists()


def test_cacerts_install_twice_adds_nothing_the_second_time(nem):
    source = certs(nem, [CA])
    nem.run("cacerts-install", source)
    assert "ok added 0 root" in nem.run("cacerts-install", source).stdout
    assert nem.path("data/nemesis/owned-cacerts").read_text() == f"{CA}\n"


def test_cacerts_install_needs_a_directory(nem):
    assert nem.run("cacerts-install").returncode == 1
    assert nem.run("cacerts-install", nem.workdir / "absent").returncode == 1


def test_cacerts_install_needs_a_writable_system(nem, locked_dirs):
    source = certs(nem, [CA])
    locked_dirs("system/xbin")
    res = nem.run("cacerts-install", source)
    assert res.returncode == 1
    assert "remount" in res.stderr


def test_cacerts_install_reports_a_failed_copy(nem):
    source = certs(nem, [CA])
    (source / CA).chmod(0)
    res = nem.run("cacerts-install", source)
    assert res.returncode == 1
    assert "copy failed" in res.stderr
    assert not nem.path(f"system/etc/security/cacerts/{CA}").exists()


def test_wipe_removes_only_the_certificates_nemesis_added(nem):
    """Given one stock certificate and one NEMESIS added
    When NEMESIS is wiped
    Then the stock one stays
    """
    folder = nem.path("system/etc/security/cacerts")
    folder.mkdir(parents=True)
    (folder / "99999999.0").write_text("stock")
    nem.run("cacerts-install", certs(nem, [CA]))
    assert (folder / CA).exists()
    assert nem.run("wipe", "--confirm", "--no-reboot").returncode == 0
    assert not (folder / CA).exists()
    assert (folder / "99999999.0").read_text() == "stock"


def test_a_failed_update_keeps_certificates_added_earlier(installed):
    nem = installed
    nem.run("cacerts-install", certs(nem, [CA]))
    files = {"core/cacerts/11223344.0": "new", "scripts/migrate.sh": "exit 1\n"}
    assert nem.run("update", update_zip(nem, files=files)).returncode == 10
    folder = nem.path("system/etc/security/cacerts")
    assert (folder / CA).exists() and not (folder / "11223344.0").exists()
    assert nem.path("data/nemesis/owned-cacerts").read_text() == f"{CA}\n"


# --- Core packages


def core_package(nem, id_="core.tool"):
    return make_zip(nem.workdir / "core.zip", {"NEMESIS.data": f"[nemesis]\nid={id_}\nname=Core\ntype=service\n"}).read_bytes()


def test_core_packages_install_locked(installed):
    nem = installed
    files = {"core-packages/core.zip": core_package(nem), "core-packages/readme.txt": "skipped"}
    assert nem.run("update", update_zip(nem, files=files)).returncode == 0
    assert "core.tool|Core|1|service" in nem.db()


def test_a_core_package_with_a_one_letter_name_installs(installed):
    nem = installed
    assert nem.run("update", update_zip(nem, files={"core-packages/a.zip": core_package(nem)})).returncode == 0
    assert "core.tool|" in nem.db()


def test_a_core_package_that_fails_restores_the_previous_version(installed):
    nem = installed
    files = {"core-packages/core.zip": b"not a zip"}
    res = nem.run("update", update_zip(nem, files=files))
    assert res.returncode == 10
    assert "core package core.zip failed" in res.stderr
    assert unchanged(nem)


# --- make_update_zip.py, the tool that writes these packages


def tool(*args):
    prefix = ["-m", "coverage", "run"] if os.environ.get("NEMESIS_COVERAGE") else []
    return subprocess.run(
        [sys.executable, *prefix, str(TOOL), *map(str, args)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


@pytest.fixture
def tool_files(installed):
    """Everything the tool can bundle, passed so it never reads the repo's unbuilt defaults."""
    nem = installed
    folder = nem.workdir / "bundle"
    folder.mkdir()
    (folder / "nemesis").write_bytes(new_binary(nem))
    (folder / "NEMESIS.apk").write_text("new apk")
    (folder / "recovery.sh").write_text("new recovery")
    (folder / "qb.sh").write_text(HOOK)
    (folder / "certs").mkdir()
    (folder / "certs" / CA).write_text("cert")
    (folder / "certs" / "readme.txt").write_text("skipped")
    (folder / "migrate.sh").write_text("exit 0\n")
    (folder / "core.zip").write_bytes(core_package(nem))
    return folder


def tool_args(folder):
    return [
        "--binary", folder / "nemesis", "--apk", folder / "NEMESIS.apk", "--recovery", folder / "recovery.sh",
        "--qb-hook", folder / "qb.sh", "--cacerts", folder / "certs",
    ]


def test_a_package_from_the_tool_installs(installed, tool_files):
    """Given a package built by make_update_zip.py with every optional part
    When `nemesis update` runs on it
    Then the tool's format and the CLI's checks agree, and every part lands
    """
    nem = installed
    nem.path("system/etc/qb_runtime.sh").write_text("stock\n")
    out = nem.workdir / "t.zip"
    args = [*tool_args(tool_files), "--core-package", tool_files / "core.zip", "--migrate", tool_files / "migrate.sh"]
    built = tool("-o", out, "--version", NEWER, *args)
    assert built.returncode == 0, built.stderr
    res = nem.run("update", out)
    assert res.returncode == 0, res.stderr
    assert nem.cli.read_bytes() == new_binary(nem)
    assert nem.path("system/app/NEMESIS.apk").read_text() == "new apk"
    assert nem.path("system/etc/qb_runtime.sh").read_text() == "stock\n" + HOOK
    assert nem.path(f"system/etc/security/cacerts/{CA}").exists()
    assert "core.tool|" in nem.db()


def test_a_wrapped_package_from_the_tool_installs(installed, tool_files):
    nem = installed
    out = nem.workdir / "t.zip"
    assert tool("-o", out, "--version", NEWER, "--wrap", "top", *tool_args(tool_files)).returncode == 0
    res = nem.run("update", out)
    assert res.returncode == 0, res.stderr
    assert nem.cli.read_bytes() == new_binary(nem)


def test_the_tool_defaults_to_the_version_in_the_source(installed, tool_files):
    nem = installed
    out = nem.workdir / "t.zip"
    assert tool("-o", out, "--no-apk", *tool_args(tool_files)).returncode == 0
    assert f"package-version={VERSION}\n" in nem.run("pkg-info", out).stdout


def test_the_tool_compresses_and_can_leave_out_the_apk(installed, tool_files):
    nem = installed
    out = nem.workdir / "t.zip"
    assert tool("-o", out, "--version", NEWER, *tool_args(tool_files)).returncode == 0
    with zipfile.ZipFile(out) as z:
        assert z.getinfo("core/nemesis").compress_type == zipfile.ZIP_DEFLATED
        assert "core/NEMESIS.apk" in z.namelist()
    assert tool("-o", out, "--version", NEWER, "--no-apk", *tool_args(tool_files)).returncode == 0
    assert "core/NEMESIS.apk" not in zipfile.ZipFile(out).namelist()


def test_the_tool_leaves_out_an_apk_that_does_not_exist(installed, tool_files):
    (tool_files / "NEMESIS.apk").unlink()
    out = installed.workdir / "t.zip"
    assert tool("-o", out, "--version", NEWER, *tool_args(tool_files)).returncode == 0
    assert "core/NEMESIS.apk" not in zipfile.ZipFile(out).namelist()


def test_the_tool_accepts_a_description_at_the_limit(installed, tool_files):
    res = tool("-o", installed.workdir / "t.zip", "--short-desc", "x" * 64, *tool_args(tool_files))
    assert res.returncode == 0, res.stderr


def test_a_package_the_tool_corrupts_on_request_is_refused(installed, tool_files):
    nem = installed
    out = nem.workdir / "t.zip"
    assert tool("-o", out, "--version", NEWER, "--corrupt", "core/NEMESIS.apk", *tool_args(tool_files)).returncode == 0
    assert nem.run("update", out).returncode == 8
    assert unchanged(nem)


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--version", "1.x"], "bad version"),
        (["--short-desc", "x" * 65], "over 64"),
    ],
    ids=["version", "description"],
)
def test_the_tool_refuses_bad_options(installed, tool_files, extra, message):
    res = tool("-o", installed.workdir / "t.zip", *tool_args(tool_files), *extra)
    assert res.returncode != 0
    assert message in res.stderr


def test_the_tool_refuses_a_binary_that_is_not_elf(installed, tool_files):
    (tool_files / "nemesis").write_text("#!/bin/sh\n")
    res = tool("-o", installed.workdir / "t.zip", *tool_args(tool_files))
    assert res.returncode != 0
    assert "not an ELF" in res.stderr


def test_the_tool_refuses_a_hook_without_its_marker(installed, tool_files):
    (tool_files / "qb.sh").write_text("echo no marker\n")
    res = tool("-o", installed.workdir / "t.zip", *tool_args(tool_files))
    assert res.returncode != 0
    assert "NEMESIS_QB_HOOK" in res.stderr
