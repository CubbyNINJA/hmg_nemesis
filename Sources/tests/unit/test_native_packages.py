"""CLI package behaviour against 04-Packages.md and AGENTS.md sections 4 and 9."""
from __future__ import annotations

import os
import signal
import struct
import time
import zipfile

import pytest

from helpers import PACKAGES, REPO, SOURCES, known_bug
from native_unit import executable, make_zip, wait_for

GOOD = "[nemesis]\nid=my.tool\nname=My Tool\ntypes=a,b\n"


def info(nem, members):
    return nem.run("pkg-info", make_zip(nem.workdir / "p.zip", members))


# --- metadata


def test_pkg_info_reports_the_documented_fields(nem):
    res = info(nem, {"NEMESIS.data": GOOD})
    assert res.returncode == 0
    assert res.stdout == "id=my.tool\nname=My Tool\ntypes=a,b\ninstalled=0\nlocked=0\n"


def test_name_defaults_to_the_id(nem):
    assert "name=my.tool\n" in info(nem, {"NEMESIS.data": "[nemesis]\nid=my.tool\n"}).stdout


def test_ini_trims_whitespace_and_accepts_crlf_and_comments(nem):
    text = "# c\r\n; c\r\n[ nemesis ]\r\n  id = my.tool  \r\nnot a pair\r\n[broken\r\nname=N\r\n"
    res = info(nem, {"NEMESIS.data": text})
    assert "id=my.tool\n" in res.stdout and "name=N\n" in res.stdout


def test_first_duplicate_key_wins(nem):
    assert "id=first\n" in info(nem, {"NEMESIS.data": "[nemesis]\nid=first\nid=second\n"}).stdout


def test_keys_outside_the_section_are_not_metadata(nem):
    res = info(nem, {"NEMESIS.data": "id=x\n[other]\nid=y\n"})
    assert res.returncode != 0
    assert "invalid package metadata" in res.stderr


@known_bug("G16", "a UTF-8 BOM hides the first section header")
def test_utf8_bom_is_tolerated(nem):
    assert info(nem, {"NEMESIS.data": b"\xef\xbb\xbf" + GOOD.encode()}).returncode == 0


@known_bug("G16", "text past 1023 characters parses as a new line")
def test_a_long_comment_cannot_inject_a_key(nem):
    text = "[nemesis]\n#" + "x" * 1022 + "id=injected\nid=my.tool\n"
    assert "id=my.tool\n" in info(nem, {"NEMESIS.data": text}).stdout


@known_bug("G15", "manifest read truncates silently at 8191 bytes")
def test_a_large_manifest_keeps_its_id(nem):
    text = "[nemesis]\n" + "# pad\n" * 2000 + "id=my.tool\n"
    assert info(nem, {"NEMESIS.data": text}).returncode == 0


@known_bug("G15", "any member named NEMESIS.data can supply the metadata")
def test_a_nested_manifest_does_not_supply_the_metadata(nem):
    members = {"A/NEMESIS.data": "[nemesis]\nid=nested\n", "NEMESIS.data": GOOD}
    assert "id=my.tool\n" in info(nem, members).stdout


@pytest.mark.parametrize(
    "bad",
    ["../../../tmp-badid", ".hidden", "a..b", "a/b", "a b", "a|b", "x" * 65],
)
def test_bad_ids_are_refused(nem, bad):
    res = nem.run("install", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": f"[nemesis]\nid={bad}\n"}))
    assert res.returncode != 0
    assert "invalid package id" in res.stderr
    assert nem.db().count(bad) == 0


@pytest.mark.parametrize("good", ["a", "adb.tcp", "usb_x-1", "A9", "x" * 64])
def test_good_ids_are_accepted(nem, good):
    assert nem.run("install", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": f"[nemesis]\nid={good}\n"})).returncode == 0


# --- archive handling


def test_missing_zip_is_reported(nem):
    res = nem.run("install", "/nope.zip")
    assert res.returncode == 1 and "missing zip" in res.stderr


def test_install_without_arguments_prints_usage(nem):
    assert "usage: nemesis install" in nem.run("install").stderr


def test_garbage_is_not_a_package(nem):
    target = nem.workdir / "g.zip"
    target.write_bytes(b"not a zip\x00\x01\x02")
    res = nem.run("install", target)
    assert res.returncode != 0 and "invalid package metadata" in res.stderr


def test_zip_without_a_manifest_is_refused(nem):
    res = nem.run("install", make_zip(nem.workdir / "p.zip", {"README": "x"}))
    assert res.returncode != 0 and "invalid package metadata" in res.stderr


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_files_extract_with_their_bytes(nem, method):
    blob = bytes(range(256)) * 40
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD, "files/deep/blob.bin": blob, "empty": b""}, method)
    assert nem.run("install", target).returncode == 0
    assert (nem.app() / "files/deep/blob.bin").read_bytes() == blob
    assert (nem.app() / "empty").read_bytes() == b""


def test_macos_clutter_is_ignored(nem):
    members = {"NEMESIS.data": GOOD, "__MACOSX/x": "x", ".DS_Store": "x", "a/.DS_Store": "x", "a/keep": "k"}
    assert nem.run("install", make_zip(nem.workdir / "p.zip", members)).returncode == 0
    assert not (nem.app() / "__MACOSX").exists() and not (nem.app() / ".DS_Store").exists()
    assert (nem.app() / "a/keep").exists() and not (nem.app() / "a/.DS_Store").exists()


def test_a_zip_of_the_folder_still_installs(nem):
    # pkg.c package_root: paths are relative to the directory holding the manifest.
    target = make_zip(nem.workdir / "p.zip", {"my-tool/NEMESIS.data": GOOD, "my-tool/bin/a": "a"})
    assert nem.run("install", target).returncode == 0
    assert (nem.app() / "NEMESIS.data").exists() and (nem.app() / "bin/a").exists()


@pytest.mark.parametrize("member", ["../tmp/traversal-probe", "a/../../b", "/abs/path", "\\abs\\path"])
def test_d1_a_traversal_member_rejects_the_whole_package(nem, member):
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD, member: "x"})
    res = nem.run("install", target)
    assert res.returncode != 0
    assert "my.tool" not in nem.db()
    assert not nem.app().exists()
    assert not (nem.root / "data/local/tmp/tmp/traversal-probe").exists()


def _patch_flags(path, value):
    raw = bytearray(path.read_bytes())
    at = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, at + 8, value)
    path.write_bytes(raw)


def test_encrypted_members_are_refused(nem):
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD})
    _patch_flags(target, 1)
    assert nem.run("install", target).returncode != 0


def test_unsupported_compression_is_refused(nem):
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD})
    raw = bytearray(target.read_bytes())
    struct.pack_into("<H", raw, raw.index(b"PK\x01\x02") + 10, 12)
    target.write_bytes(raw)
    assert nem.run("install", target).returncode != 0


@known_bug("G14", "CRC is never checked")
def test_a_corrupted_member_is_refused(nem):
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD, "payload": b"A" * 64}, zipfile.ZIP_STORED)
    raw = target.read_bytes()
    target.write_bytes(raw.replace(b"A" * 64, b"A" * 63 + b"B"))
    assert nem.run("install", target).returncode != 0


def _break_eocd_signature(raw, payload_at):
    at = raw.rfind(b"PK\x05\x06")
    return raw[:at] + b"XXXX" + raw[at + 4:]


def _overstate_entry_count(raw, payload_at):
    at = raw.rfind(b"PK\x05\x06") + 10
    count = struct.unpack_from("<H", raw, at)[0]
    return raw[:at] + struct.pack("<H", count + 1) + raw[at + 2:]


def _overstate_name_length(raw, payload_at):
    # Under MAX_PATH, so the check that fails is the short read and not the length limit.
    at = raw.rfind(b"PK\x01\x02") + 28
    return raw[:at] + struct.pack("<H", 100) + raw[at + 2:]


def _overstate_compressed_size(raw, payload_at):
    at = raw.rfind(b"PK\x01\x02") + 20
    return raw[:at] + struct.pack("<I", 10000000) + raw[at + 4:]


def _break_local_header(raw, payload_at):
    return raw[:payload_at] + b"XXXX" + raw[payload_at + 4:]


def _garble_deflate_stream(raw, payload_at):
    start = payload_at + 30 + len("payload")
    # Stay inside the 15-byte stream: going further breaks the central directory, not the inflate.
    return raw[:start] + b"\xff" * 8 + raw[start + 8:]


@pytest.mark.parametrize(
    "damage",
    [
        _break_eocd_signature,
        _overstate_entry_count,
        _overstate_name_length,
        _overstate_compressed_size,
        _break_local_header,
        _garble_deflate_stream,
    ],
)
def test_a_damaged_zip_installs_nothing(nem, damage):
    """Given a package zip damaged in one structural field (zip.c zip_iter)
    When it is installed
    Then the install fails, the package is not recorded, and no tree is left
    """
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD, "payload": b"payload " * 64})
    with zipfile.ZipFile(target) as z:
        payload_at = z.getinfo("payload").header_offset
    target.write_bytes(damage(target.read_bytes(), payload_at))
    assert nem.run("install", target).returncode != 0
    assert "my.tool" not in nem.db()
    assert not nem.app().exists()


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_a_member_that_cannot_be_written_fails_the_install(nem, method):
    """Given a zip where one member's path runs through another member that is a file (zip.c:164,177)
    When it is installed
    Then the install fails and the package is not recorded
    """
    members = {"NEMESIS.data": GOOD, "x": "a file", "x/y": "cannot exist under a file"}
    assert nem.run("install", make_zip(nem.workdir / "p.zip", members, method)).returncode != 0
    assert "my.tool" not in nem.db()
    assert not nem.app().exists()


@known_bug("G14", "a member shorter than its declared size is written with heap bytes")
def test_a_member_shorter_than_declared_is_refused(nem):
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD, "payload": b"A" * 64})
    raw = bytearray(target.read_bytes())
    at = raw.rindex(b"PK\x01\x02")
    usize = struct.unpack_from("<I", raw, at + 24)[0]
    struct.pack_into("<I", raw, at + 24, usize + 512)
    target.write_bytes(raw)
    assert nem.run("install", target).returncode != 0


@known_bug("G16", "lowercase manifest installs, then uninstall cannot find it")
def test_lowercase_manifest_keeps_its_hooks_on_uninstall(nem):
    log = f'echo ran >> "{nem.root}/hooks"\n'
    members = {"nemesis.data": GOOD + "preuninstall=scripts/u.sh\n", "scripts/u.sh": log}
    assert nem.run("install", make_zip(nem.workdir / "p.zip", members)).returncode == 0
    nem.run("uninstall", "my.tool")
    assert nem.hooks() == ["ran"]


# --- install and uninstall order


def test_install_runs_hooks_in_order_with_the_documented_cwds(nem):
    res = nem.run("install", nem.package())
    assert res.returncode == 0
    assert "ok installed my.tool" in res.stdout
    pre, post = nem.hooks()
    assert pre == f"pre {nem.root}/data/local/tmp/nemesis/stage/my.tool"
    assert post == f"post {nem.app()}"


def test_install_records_the_package_and_clears_the_stage(nem):
    nem.run("install", nem.package())
    assert "my.tool|My Tool|0|application" in nem.db()
    assert not (nem.root / "data/local/tmp/nemesis/stage/my.tool").exists()


def test_hooks_get_the_wrapper_directory_first_on_path(nem):
    script = f'echo "$PATH" > "{nem.root}/path"\n'
    nem.run("install", nem.package(extra_meta="", files={"scripts/post.sh": script}))
    assert (nem.root / "path").read_text().startswith(f"{nem.root}/data/nemesis/bin:")


def test_postinstall_sees_services_and_package_already_registered(nem):
    # 04-Packages.md section 6, step 5.
    script = f'cat "{nem.root}/data/nemesis/packages.db" "{nem.root}/data/nemesis/services.db" > "{nem.root}/seen"\n'
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=background\n"
    nem.run("install", nem.package(sections=sections, files={"scripts/post.sh": script, "svc/d.sh": "sleep 30\n"}))
    seen = (nem.root / "seen").read_text()
    assert "my.tool|" in seen and "my.tool/d|" in seen


def test_preinstall_failure_stops_the_install(nem):
    res = nem.run("install", nem.package(files={"scripts/pre.sh": "exit 7\n"}))
    assert res.returncode == 7
    assert "my.tool" not in nem.db()
    assert not nem.app().exists()
    assert not (nem.root / "data/local/tmp/nemesis/stage/my.tool").exists()


def test_d6_postinstall_failure_returns_failure_but_stays_installed(nem):
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=background\n"
    res = nem.run("install", nem.package(sections=sections, files={"scripts/post.sh": "exit 5\n", "svc/d.sh": "sleep 30\n"}))
    assert res.returncode == 5
    assert "my.tool|" in nem.db()
    assert nem.app().exists()
    assert "my.tool/d|background|running" in nem.run("service", "status", "my.tool/d").stdout


def test_a_missing_hook_script_is_skipped(nem):
    meta = GOOD + "postinstall=scripts/none.sh\n"
    res = nem.run("install", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": meta}))
    assert res.returncode == 0
    assert "skip missing script" in nem.log()


def test_same_id_needs_update(nem):
    nem.run("install", nem.package())
    res = nem.run("install", nem.package())
    assert res.returncode == 3 and "pass --update" in res.stderr


def test_update_replaces_the_old_tree_and_runs_old_uninstall_hooks(nem):
    nem.run("install", nem.package(files={"old.txt": "x"}))
    (nem.root / "hooks").unlink()
    res = nem.run("install", "--update", nem.package(files={"new.txt": "y"}))
    assert res.returncode == 0 and "ok updated my.tool" in res.stdout
    assert (nem.app() / "new.txt").exists() and not (nem.app() / "old.txt").exists()
    assert [h.split()[0] for h in nem.hooks()] == ["preun", "postun", "pre", "post"]
    assert nem.db().count("my.tool|") == 1


@known_bug("G2", "update deletes the old tree before the new install can fail")
def test_a_failed_update_leaves_the_package_usable(nem):
    nem.run("install", nem.package(files={"old.txt": "x"}))
    res = nem.run("install", "--update", nem.package(files={"scripts/pre.sh": "exit 9\n"}))
    assert res.returncode != 0
    assert (nem.app() / "old.txt").exists()


def test_locked_package_cannot_be_updated_or_removed(nem):
    nem.run("pkg-list")
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=usb-handler\n"})
    res = nem.run("install", "--update", target)
    assert res.returncode == 2 and "locked" in res.stderr
    res = nem.run("uninstall", "usb-handler")
    assert res.returncode == 2 and "package is locked" in res.stderr
    assert nem.app("usb-handler").exists()


def test_pkg_list_seeds_the_locked_core_package(nem):
    res = nem.run("pkg-list")
    assert "usb-handler|USB Handler|1|service" in res.stdout


def test_seeded_usb_handler_matches_the_shipped_manifest(nem):
    nem.run("pkg-list")
    seeded = (nem.app("usb-handler") / "NEMESIS.data").read_text()
    assert seeded == (PACKAGES / "usb-handler/NEMESIS.data").read_text()


def test_pkg_info_reports_an_installed_package(nem):
    target = nem.package()
    nem.run("install", target)
    assert "installed=1\nlocked=0" in nem.run("pkg-info", target).stdout


def test_uninstall_runs_hooks_and_removes_everything(nem):
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=background\n"
    nem.run("install", nem.package(sections=sections, files={"svc/d.sh": "sleep 30\n"}))
    (nem.root / "hooks").unlink()
    assert nem.run("uninstall", "my.tool").returncode == 0
    names = [h.split()[0] for h in nem.hooks()]
    assert names == ["preun", "postun"]
    assert "my.tool" not in nem.db() and "my.tool/d" not in nem.db("services.db")
    assert not nem.app().exists()


def test_postuninstall_runs_from_a_copy_outside_the_tree(nem):
    # 04-Packages.md section 2: copied to tmp so it still exists after the tree is deleted.
    script = f'echo "$0" >> "{nem.root}/hooks"\n'
    nem.run("install", nem.package(files={"scripts/postun.sh": script}))
    (nem.root / "hooks").unlink()
    nem.run("uninstall", "my.tool")
    assert nem.hooks()[-1] == f"{nem.root}/data/local/tmp/nemesis/postun-my.tool.sh"
    assert not list((nem.root / "data/local/tmp/nemesis").glob("postun-*"))


def test_uninstall_unknown_package(nem):
    res = nem.run("uninstall", "not-installed")
    assert res.returncode == 1 and "package not installed" in res.stderr


def test_uninstall_without_id_prints_usage(nem):
    assert "usage: nemesis uninstall" in nem.run("uninstall").stderr


@known_bug("G3", "a pipe in name shifts the DB fields, and locked reads as 1")
def test_a_package_name_cannot_lock_the_package(nem):
    nem.run("install", nem.package(extra_meta=""))
    target = make_zip(nem.workdir / "pipe-name.zip", {"NEMESIS.data": "[nemesis]\nid=pipe.tool\nname=x|1|y\n"})
    nem.run("install", target)
    res = nem.run("uninstall", "pipe.tool")
    assert res.returncode in (0, 1)
    assert "locked" not in res.stderr


@known_bug("G4", "preuninstall failure is ignored and the tree is deleted")
def test_a_failed_preuninstall_keeps_the_package(nem):
    nem.run("install", nem.package(files={"scripts/preun.sh": "exit 4\n"}))
    res = nem.run("uninstall", "my.tool")
    assert res.returncode != 0
    assert nem.app().exists()


# --- APKs


APK = "[app.0]\nkind=apk\npath=app/a.apk\npackage=com.a\n"


def apk_package(nem, sections=APK):
    return nem.package(sections=sections, files={"app/a.apk": "apk"}, log_hooks=False)


def test_apk_installs_with_the_property_cleared_only_for_that_call(nem):
    assert nem.run("install", apk_package(nem)).returncode == 0
    assert f'pm install -r {nem.app()}/app/a.apk boot=0' in nem.calls()
    assert nem.prop("sys.boot_completed") == "1"


def test_a_pm_failure_with_android_up_fails_the_install(nem):
    nem.pm_script('echo "Failure [INSTALL_FAILED_X]"\n')
    res = nem.run("install", apk_package(nem))
    assert res.returncode != 0
    assert "pm install failed" in res.stderr
    assert "my.tool" not in nem.db() and not nem.app().exists()
    assert nem.prop("sys.boot_completed") == "1"


def test_a_pm_failure_with_android_down_keeps_the_package(nem):
    nem.android(up=False)
    nem.pm_script('echo "Error: Could not access the Package Manager"\n')
    assert nem.run("install", apk_package(nem)).returncode == 0
    assert (nem.app() / "app/a.apk").exists()
    assert "my.tool|" in nem.db()


def test_property_is_left_alone_when_android_is_down(nem):
    nem.android(up=False)
    nem.run("install", apk_package(nem))
    assert "boot=0" in nem.calls()
    assert nem.prop("sys.boot_completed") == "0"


def test_a_different_signer_is_uninstalled_and_retried_once(nem):
    state = nem.root / "pm-tried"
    nem.pm_script(
        f'case "$1" in\n'
        f'install) if [ -f "{state}" ]; then echo Success; else touch "{state}"; echo "Failure [INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES]"; fi ;;\n'
        f'path) echo package:/system/app/a.apk ;;\n'
        f'uninstall) echo Success ;;\n'
        f"esac\n"
    )
    assert nem.run("install", apk_package(nem)).returncode == 0
    calls = nem.calls()
    assert calls.count("pm install") == 2 and "pm uninstall com.a" in calls


def test_a_certificate_failure_for_a_new_package_is_not_retried(nem):
    nem.pm_script(
        'case "$1" in\n'
        'install) echo "Failure [INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES]" ;;\n'
        "path) ;;\n"
        "esac\n"
    )
    assert nem.run("install", apk_package(nem)).returncode != 0
    assert nem.calls().count("pm install") == 1
    # The retry path uninstalls with the property cleared. Rollback (pkg.c:515) runs after it is restored.
    assert "pm uninstall com.a boot=0" not in nem.calls()


def test_missing_apk_fails_the_install(nem):
    assert nem.run("install", nem.package(sections=APK, log_hooks=False)).returncode != 0


def test_uninstall_calls_pm_with_the_package_name(nem):
    nem.run("install", apk_package(nem))
    nem.run("uninstall", "my.tool")
    assert "pm uninstall com.a" in nem.calls()


def test_uninstall_uses_the_package_half_of_component(nem):
    sections = "[app.0]\nkind=apk\npath=app/a.apk\ncomponent=com.b/.Main\n"
    nem.run("install", apk_package(nem, sections))
    nem.run("uninstall", "my.tool")
    assert "pm uninstall com.b" in nem.calls()


def test_uninstall_without_package_or_component_does_not_call_pm(nem):
    sections = "[app.0]\nkind=apk\npath=app/a.apk\n"
    nem.run("install", apk_package(nem, sections))
    nem.run("uninstall", "my.tool")
    assert "pm uninstall" not in nem.calls()


def test_an_unsafe_android_package_name_never_reaches_pm(nem):
    sections = "[app.0]\nkind=apk\npath=app/a.apk\npackage=com.a;touch${IFS}x\n"
    nem.run("install", apk_package(nem, sections))
    nem.run("uninstall", "my.tool")
    assert "pm uninstall" not in nem.calls()


@known_bug("G5", "killing the CLI during pm leaves sys.boot_completed at 0")
def test_a_killed_install_does_not_leave_the_property_cleared(nem):
    marker = nem.root / "in-pm"
    nem.pm_script(f'touch "{marker}"\nsleep 30\necho Success\n')
    proc = nem.daemon("install", str(apk_package(nem)))
    assert wait_for(marker.exists)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    assert nem.prop("sys.boot_completed") == "1"


# --- launch


def test_launch_prefers_component_then_package_then_script(nem):
    sections = (
        "[app.0]\nkind=sh\npath=bin/a.sh\ncomponent=com.a/.Main\npackage=com.ignored\n"
        "[app.1]\nkind=sh\npath=bin/b.sh\npackage=com.b\n"
        "[app.2]\nkind=sh\npath=bin/c.sh\n"
    )
    script = f'echo script-ran >> "{nem.root}/calls"\n'
    nem.run("install", nem.package(sections=sections, files={"bin/c.sh": script, "bin/a.sh": script, "bin/b.sh": script}))
    res = nem.run("pkg-launch", "my.tool")
    assert res.returncode == 0 and "ok launched my.tool" in res.stdout
    calls = nem.calls()
    assert calls.index("am start -n com.a/.Main") < calls.index("monkey -p com.b") < calls.index("script-ran")
    assert calls.count("script-ran") == 1
    assert "com.ignored" not in calls


def test_launch_starts_instance_services(nem):
    sections = "[service.0]\nname=i\nkind=instance\npath=com.a/.Svc\nmode=background\n"
    nem.run("install", nem.package(sections=sections))
    open(nem.root / "calls", "w").close()
    assert nem.run("pkg-launch", "my.tool").returncode == 0
    assert wait_for(lambda: "com.a/.Svc" in nem.calls())


def test_launch_installs_an_apk_left_behind_by_a_recovery_install(nem):
    nem.android(up=False)
    sections = APK + "component=com.a/.Main\n"
    nem.run("install", apk_package(nem, sections))
    nem.android(up=True)
    nem.pm_script('[ "$1" = path ] && exit 1\necho Success\n')
    assert nem.run("pkg-launch", "my.tool").returncode == 0
    assert nem.calls().rindex("pm install") > nem.calls().rindex("pm path")
    assert "am start -n com.a/.Main" in nem.calls()


def test_launch_with_nothing_to_launch_fails(nem):
    nem.run("install", nem.package(log_hooks=False))
    res = nem.run("pkg-launch", "my.tool")
    assert res.returncode == 1 and "nothing to launch" in res.stderr


def test_launch_unknown_package_fails(nem):
    res = nem.run("pkg-launch", "nope")
    assert res.returncode == 1 and "package not installed" in res.stderr


def test_launch_refuses_a_component_that_could_break_the_quoting(nem):
    sections = "[app.0]\nkind=sh\ncomponent=com.a/.M'x\n"
    nem.run("install", nem.package(sections=sections))
    assert nem.run("pkg-launch", "my.tool").returncode != 0
    assert "am start" not in nem.calls()


@known_bug("D3", "pkg-launch always runs scripts as root and ignores uid=system")
def test_d3_app_uid_system_is_honoured(nem):
    sections = "[app.0]\nkind=sh\npath=bin/a.sh\nuid=system\n"
    nem.run("install", nem.package(sections=sections, files={"bin/a.sh": "exit 0\n"}))
    (nem.root / "ids.log").unlink()
    nem.run("pkg-launch", "my.tool")
    assert "uid 1000" in nem.ids()


@known_bug("D4", "NEMESIS.data paths are not checked for ..")
@pytest.mark.parametrize(
    "extra,sections",
    [
        ("postinstall=../../outside.sh\n", ""),
        ("", "[app.0]\nkind=sh\npath=../../outside.sh\n"),
        ("", "[service.0]\nname=d\nkind=sh\npath=../../outside.sh\n"),
    ],
    ids=["hook", "app", "service"],
)
def test_d4_parent_paths_in_the_manifest_are_refused(nem, extra, sections):
    target = nem.package(extra_meta=extra, sections=sections, log_hooks=False)
    assert nem.run("install", target).returncode != 0


def test_d4_absolute_hook_paths_are_accepted(nem):
    script = executable(nem.root / "abs.sh", f'echo abs >> "{nem.root}/hooks"\n')
    assert nem.run("install", nem.package(extra_meta=f"postinstall={script}\n", log_hooks=False)).returncode == 0
    assert nem.hooks() == ["abs"]


# --- builder output is accepted by the CLI


@pytest.mark.parametrize("pkg", sorted(p.parent.name for p in (SOURCES / "examples").glob("package-*/NEMESIS.data")))
def test_cli_reads_what_the_builder_writes(nem, pkg, tmp_path):
    import subprocess
    import sys

    out = tmp_path / "o.zip"
    subprocess.check_call([sys.executable, str(SOURCES / "tools/make_nemesis_package.py"), str(SOURCES / "examples" / pkg), "-o", str(out)], stdout=subprocess.DEVNULL)
    res = nem.run("pkg-info", out)
    assert res.returncode == 0 and "id=hello.tool" in res.stdout


# --- wipe


def test_wipe_needs_confirm(nem):
    res = nem.run("wipe")
    assert res.returncode == 1 and "usage: nemesis wipe --confirm" in res.stderr
    assert nem.cli.exists()


def _install_system_files(nem):
    stock = (SOURCES / "tests/qb_runtime.stock.sh").read_bytes().replace(b"\r\n", b"\n")
    hook = (SOURCES / "boot/qb_hook.sh").read_bytes().replace(b"\r\n", b"\n")
    (nem.root / "system/etc/qb_runtime.sh").write_bytes(stock + b"\n" + hook + b"\n")
    (nem.root / "system/etc/install-recovery.sh").write_text("x")
    (nem.root / "system/app/NEMESIS.apk").write_text("x")
    return stock


def test_wipe_removes_packages_system_files_and_the_qb_hook(nem):
    stock = _install_system_files(nem)
    nem.run("install", nem.package())
    (nem.root / "hooks").unlink()
    res = nem.run("wipe", "--confirm")
    assert res.returncode == 0 and "ok wiped" in res.stdout
    assert [h.split()[0] for h in nem.hooks()] == ["preun", "postun"]
    assert not nem.cli.exists()
    assert not (nem.root / "system/app/NEMESIS.apk").exists()
    assert not (nem.root / "system/etc/install-recovery.sh").exists()
    assert not (nem.root / "data/nemesis/apps").exists()
    runtime = (nem.root / "system/etc/qb_runtime.sh").read_bytes()
    assert b"NEMESIS_QB_HOOK" not in runtime and b"nemesis watchdog" not in runtime
    assert runtime.rstrip() == stock.rstrip()


def test_wipe_removes_the_locked_package_too(nem):
    nem.run("pkg-list")
    assert nem.run("wipe", "--confirm").returncode == 0
    assert not nem.app("usb-handler").exists()


def test_wipe_leaves_a_stock_quickboot_script_alone(nem):
    stock = (SOURCES / "tests/qb_runtime.stock.sh").read_bytes()
    (nem.root / "system/etc/qb_runtime.sh").write_bytes(stock)
    nem.run("wipe", "--confirm")
    assert (nem.root / "system/etc/qb_runtime.sh").read_bytes() == stock


@known_bug("G10", "log_msg recreates /data/nemesis after it is removed")
def test_wipe_leaves_no_data_directory_behind(nem):
    nem.run("wipe", "--confirm")
    assert not (nem.root / "data/nemesis").exists()


@known_bug("G10", "wipe does not stop the running daemons")
def test_wipe_stops_the_daemons(nem):
    nem.run("watchdog")
    assert wait_for((nem.root / "data/nemesis/watchdog.pid").exists)
    pid = int((nem.root / "data/nemesis/watchdog.pid").read_text().split()[0])
    nem.run("wipe", "--confirm")
    time.sleep(1)
    assert not os.path.exists(f"/proc/{pid}")


@known_bug("G13", "DB writes truncate in place with no lock, so a concurrent reader or writer can lose rows")
def test_db_writes_are_atomic():
    # No reliable way to force the race from outside, so this checks the mechanism.
    source = (REPO / "Sources/native/pkg.c").read_text(encoding="utf-8")
    body = source[source.index("int pkg_save_all") : source.index("int pkg_upsert")]
    assert "rename(" in body
