"""Descriptor keys, compatibility, dependencies, and the USB flows that 1.16 added.

Sources: 04-Packages.md sections 2 and 3, 05-Run-Scripts.md section 2, and
02-Debugging-Execution-Interface.md for the command output.
"""
from __future__ import annotations

import re
import subprocess
import time

import pytest

from helpers import builder_cmd, known_bug
from native_unit import executable, make_zip, wait_for
from test_native_update import NEWER, OLDER, VERSION, new_binary, update_zip


def info(nem, text):
    return nem.run("pkg-info", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": text}))


def field(res, name):
    for line in res.stdout.splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{name} not in {res.stdout!r}")


def device(nem, os_id="1.0", model="EV6"):
    (nem.root / "props" / "ro.build.product").write_text(os_id)
    (nem.root / "props" / "ro.mobis.targetdir").write_text(model)


def install(nem, id_, version="", depends="", extra=""):
    lines = f"[nemesis]\nversion=1.1\nid={id_}\nname={id_}\ntype=application\n"
    if version:
        lines += f"package-version={version}\n"
    if depends:
        lines += f"depends={depends}\n"
    return nem.run("install", *extra.split(), make_zip(nem.workdir / f"{id_}.zip", {"NEMESIS.data": lines}))


# --- Descriptor keys


# Each is refused by the unit and by the builder, so a package that builds also installs.
REFUSED = [
    ("version=abc\nid=my.tool\n", "bad version"),
    ("version=1.\nid=my.tool\n", "bad version"),
    ("version=.1\nid=my.tool\n", "bad version"),
    ("version=1..2\nid=my.tool\n", "bad version"),
    ("version=1a2\nid=my.tool\n", "bad version"),
    ("version=1.1\nid=my.tool\npackage-version=1.x\n", "package-version"),
    ("version=1.1\nid=my.tool\npackage-version=1a2\n", "package-version"),
    ("id=my.tool\nshort-desc=" + "x" * 65 + "\n", "short-desc"),
    ("id=my.tool\ndetailed-desc=" + "x" * 257 + "\n", "detailed-desc"),
    ("id=my.tool\ndepends=@1.0\n", "bad depends"),
    ("id=my.tool\ndepends=other@x\n", "bad depends"),
    ("id=my.tool\ndepends=other@>=x\n", "bad depends"),
    ("id=my.tool\ndepends=other@-1.0\n", "bad depends"),
    ("id=my.tool\ndepends=other@1.0-\n", "bad depends"),
    ("id=my.tool\ndepends=my.tool\n", "depends on itself"),
    ('id=my.tool\nshort-desc="never closed\n', "unterminated"),
]


@pytest.mark.parametrize("body, message", REFUSED)
def test_the_unit_refuses_a_bad_descriptor(nem, body, message):
    res = info(nem, "[nemesis]\n" + body)
    assert res.returncode != 0
    assert message in res.stderr


@pytest.mark.parametrize("body, message", REFUSED)
def test_the_builder_refuses_what_the_unit_refuses(tmp_path, body, message):
    """Given a descriptor the unit would refuse
    When the builder runs on it
    Then the mistake is caught on the host and no zip is written
    """
    folder = tmp_path / "pkg"
    folder.mkdir()
    (folder / "NEMESIS.data").write_text("[nemesis]\n" + body)
    res = subprocess.run(builder_cmd(folder, "-o", tmp_path / "o.zip"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    assert res.returncode != 0
    assert message in res.stderr
    assert not (tmp_path / "o.zip").exists()


@pytest.mark.parametrize(
    "key, limit",
    [("short-desc", 64), ("detailed-desc", 256)],
)
def test_description_limits_count_characters_not_bytes(nem, key, limit):
    """Given a description of exactly the limit in a two-byte character (04-Packages.md section 2)
    When the unit reads it
    Then it is accepted
    """
    assert info(nem, f"[nemesis]\nid=my.tool\n{key}={'é' * limit}\n").returncode == 0
    assert info(nem, f"[nemesis]\nid=my.tool\n{key}={'é' * (limit + 1)}\n").returncode != 0


@pytest.mark.parametrize(
    "version, keys, expected",
    [
        ("1.1", "type=new\ntypes=old\n", "new"),
        ("1.1", "types=old\n", "old"),
        ("1.0", "type=new\ntypes=old\n", "old"),
        ("1.0", "type=new\n", "new"),
    ],
)
def test_each_format_reads_the_other_type_key_as_a_fallback(nem, version, keys, expected):
    assert field(info(nem, f"[nemesis]\nversion={version}\nid=my.tool\n{keys}"), "type") == expected


def test_a_missing_version_means_1_0(nem):
    assert field(info(nem, "[nemesis]\nid=my.tool\n"), "version") == "1.0"


def test_pkg_info_escapes_what_would_break_its_lines(nem):
    res = info(nem, '[nemesis]\nid=my.tool\ndetailed-desc="a|b%c\nd"\n')
    assert field(res, "detailed-desc") == "a%7Cb%25c%0Ad"


def test_pkg_info_reads_an_installed_package_by_id(nem):
    install(nem, "my.tool", "1.2")
    res = nem.run("pkg-info", "my.tool")
    assert res.returncode == 0
    assert field(res, "installed") == "1"
    assert field(res, "installed-version") == "1.2"


def test_pkg_info_refuses_what_it_cannot_find_or_read(nem):
    assert nem.run("pkg-info").returncode == 1
    assert nem.run("pkg-info", "absent").returncode == 1
    install(nem, "my.tool", "1.2")
    (nem.app() / "NEMESIS.data").write_text("[nemesis]\nversion=abc\n")
    assert nem.run("pkg-info", "my.tool").returncode == 1


# --- Compatibility verdict


@pytest.mark.parametrize(
    "os_list, models, verdict",
    [
        ("any", "any", "full"),
        ("1.0", "none", "full"),
        ("1.*", "none", "full"),
        ("?.0", "none", "full"),
        ("9,1.0", "none", "full"),
        (" 9 , 1.0 ", "none", "full"),
        ("\t9\t,\t1.0\t", "none", "full"),
        ("9,\n1.0\n", "none", "full"),
        ("1.*9", "none", "incompatible"),
        ("1.0?", "none", "incompatible"),
        ("9", "EV6", "partial"),
        ("9", "EV*", "partial"),
        ("9", "any", "partial"),
        ("9", "none", "incompatible"),
    ],
)
def test_the_verdict_follows_the_table(nem, os_list, models, verdict):
    device(nem)
    res = nem.run("compat-check", os_list, models)
    assert res.returncode == 0
    assert field(res, "compat") == verdict


def test_the_verdict_gives_its_reasons(nem):
    device(nem)
    res = nem.run("compat-check", "9", "none", "absent.dep")
    assert "dep-missing absent.dep\n" in res.stdout
    assert "os 1.0 not in 9\n" in res.stdout
    assert "model EV6 not in none\n" in res.stdout


def test_an_unknown_device_is_named_as_unknown(nem):
    res = nem.run("compat-check", "9", "none")
    assert "os unknown not in 9\n" in res.stdout
    assert "model unknown not in none\n" in res.stdout
    assert field(res, "compat") == "incompatible"


def test_compat_check_needs_both_lists(nem):
    assert nem.run("compat-check", "any").returncode == 1


@pytest.mark.parametrize(
    "spec, ok",
    [
        ("dep", True),
        ("dep@any", True),
        ("dep@1.2", True),
        ("dep@1.2.0", True),
        ("dep@1.3", False),
        ("dep@>=1.2", True),
        ("dep@>=1.3", False),
        ("dep@<=1.2", True),
        ("dep@<=1.1", False),
        ("dep@1.0-1.5", True),
        ("dep@1.3-1.5", False),
        ("dep@1.0-1.1", False),
        ("dep@1.10", False),
        ("dep@1.2.1", False),
        ("dep@1.2-1.5", True),
        ("dep@1.0-1.2", True),
        ("dep @ 1.2", True),
        ("\tdep\t@\t1.2\t", True),
        ("dep@", True),
        ("dep@" + "1" * 32 + "-2", False),
    ],
)
def test_a_dependency_spec_matches_as_documented(nem, spec, ok):
    assert install(nem, "dep", "1.2").returncode == 0
    res = nem.run("compat-check", "any", "any", spec)
    assert field(res, "compat") == ("full" if ok else "incompatible")
    assert ("dep-version dep have 1.2" in res.stdout) == (not ok)


def test_versions_compare_as_numbers_not_text(nem):
    install(nem, "dep", "1.10")
    assert field(nem.run("compat-check", "any", "any", "dep@>=1.9"), "compat") == "full"


def test_a_dependency_with_no_version_meets_only_any(nem):
    install(nem, "dep")
    assert field(nem.run("compat-check", "any", "any", "dep"), "compat") == "full"
    assert field(nem.run("compat-check", "any", "any", "dep@>=0"), "compat") == "incompatible"
    res = nem.run("compat-check", "any", "any", "dep@1.0")
    assert field(res, "compat") == "incompatible"
    assert "have unknown" in res.stdout


def test_the_model_comes_from_the_last_part_of_the_target_directory(nem):
    device(nem, model="/x/y/EV6//")
    assert field(nem.run("compat-check", "any", "any"), "model") == "EV6"


def test_with_no_target_directory_a_single_onebin_folder_names_the_model(nem):
    folder = nem.path("system/onebin")
    (folder / "EV6").mkdir(parents=True)
    (folder / "note.txt").write_text("not a folder")
    assert field(nem.run("compat-check", "any", "any"), "model") == "EV6"


def test_two_onebin_folders_name_no_model(nem):
    folder = nem.path("system/onebin")
    (folder / "EV6").mkdir(parents=True)
    (folder / "IONIQ5").mkdir()
    assert field(nem.run("compat-check", "any", "any"), "model") == ""


def test_compat_reports_on_a_zip_and_an_installed_package(nem):
    device(nem)
    package = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\nos=9\nmodels=none\n"})
    assert "compat=incompatible" in nem.run("compat", package).stdout
    install(nem, "other", "1.0")
    assert "compat=full" in nem.run("compat", "other").stdout


def test_compat_needs_something_it_can_find(nem):
    assert nem.run("compat").returncode == 1
    assert nem.run("compat", "absent").returncode == 1
    bad = nem.workdir / "bad.zip"
    bad.write_text("not a zip")
    assert nem.run("compat", bad).returncode == 1


def test_an_installed_package_with_no_manifest_is_reported(nem):
    install(nem, "my.tool", "1.2")
    (nem.app() / "NEMESIS.data").unlink()
    res = nem.run("pkg-info", "my.tool")
    assert res.returncode == 1
    assert "invalid installed metadata" in res.stderr


def apk_package(nem):
    members = {"NEMESIS.data": "[nemesis]\nid=my.tool\n[app.0]\nkind=apk\npath=a.apk\npackage=com.example.tool\n", "a.apk": "apk"}
    return make_zip(nem.workdir / "apk.zip", members)


def test_an_apk_that_will_not_uninstall_keeps_the_package(nem):
    """Given Android up and a pm that leaves the app in place (04-Packages.md section 5.4)
    When the package is uninstalled
    Then it exits 5, does not claim success, and the package stays
    """
    assert nem.run("install", apk_package(nem)).returncode == 0
    nem.pm_script('case "$1" in path) echo package:/data/app/a.apk ;; *) echo Success ;; esac\n')
    res = nem.run("uninstall", "my.tool")
    assert res.returncode == 5
    assert "ok uninstalled" not in res.stdout
    assert nem.app().is_dir()


def test_with_android_down_uninstall_does_not_wait_on_pm_path(nem):
    assert nem.run("install", apk_package(nem)).returncode == 0
    nem.android(up=False)
    nem.pm_script('case "$1" in path) echo package:/data/app/a.apk ;; *) echo Success ;; esac\n')
    res = nem.run("uninstall", "my.tool")
    assert res.returncode == 0
    assert "ok uninstalled my.tool" in res.stdout
    assert "pm path" not in nem.calls()


# --- What the verdict and dependencies stop


def test_an_incompatible_package_needs_the_risk_accepted(nem):
    """Given a package marked for another OS and model (04-Packages.md section 3)
    When it installs without and then with --accept-risk
    Then it exits 20 and installs nothing, and then it installs
    """
    device(nem)
    package = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\nos=9\nmodels=none\n"})
    res = nem.run("install", package)
    assert res.returncode == 20
    assert "compat=incompatible" in res.stdout
    assert "my.tool" not in nem.db()
    assert nem.run("install", "--accept-risk", package).returncode == 0
    assert "my.tool|" in nem.db()


def test_a_missing_dependency_makes_a_package_incompatible(nem):
    assert install(nem, "my.tool", depends="absent").returncode == 20


def test_uninstalling_a_package_others_need_is_blocked_even_with_force(nem):
    install(nem, "dep", "1.0")
    install(nem, "my.tool", depends="dep")
    for args in (("uninstall", "dep"), ("uninstall", "--force", "dep")):
        res = nem.run(*args)
        assert res.returncode == 4
        assert "dependent my.tool|my.tool" in res.stdout
    assert nem.app("dep").is_dir()


def test_an_update_that_breaks_a_dependent_is_blocked(nem):
    install(nem, "dep", "1.0")
    install(nem, "my.tool", depends="dep@1.0")
    res = install(nem, "dep", "2.0", extra="--update")
    assert res.returncode == 21
    assert "dependent my.tool" in res.stdout
    assert "package-version=1.0" in (nem.app("dep") / "NEMESIS.data").read_text()


def test_an_update_that_keeps_a_dependent_satisfied_goes_ahead(nem):
    install(nem, "dep", "1.0")
    assert install(nem, "my.tool", depends="dep@>=1.0").returncode == 0
    assert install(nem, "dep", "2.0", extra="--update").returncode == 0


def test_a_package_that_depends_on_something_else_does_not_block_uninstall(nem):
    install(nem, "dep", "1.0")
    install(nem, "other", "1.0")
    assert install(nem, "my.tool", depends="other").returncode == 0
    assert nem.run("uninstall", "dep").returncode == 0


@pytest.mark.parametrize("spec", ["any", "1.2", ">=1.2", "<=1.2", "1.2-1.5", "1" * 31 + "-2"])
def test_every_documented_spec_form_is_accepted(nem, spec):
    assert info(nem, f"[nemesis]\nid=my.tool\ndepends=other@{spec}\n").returncode == 0


def test_a_range_too_long_to_compare_is_refused(nem):
    assert info(nem, "[nemesis]\nid=my.tool\ndepends=other@" + "1" * 32 + "-2\n").returncode != 0


@known_bug("G29", "the builder has no length limit on a range, so it builds a package the unit refuses")
def test_the_builder_refuses_a_range_too_long_for_the_unit(tmp_path):
    folder = tmp_path / "pkg"
    folder.mkdir()
    (folder / "NEMESIS.data").write_text("[nemesis]\nid=my.tool\ndepends=other@" + "1" * 32 + "-2\n")
    res = subprocess.run(builder_cmd(folder, "-o", tmp_path / "o.zip"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert res.returncode != 0


def test_a_missing_id_is_named(nem):
    res = info(nem, "[nemesis]\nname=N\n")
    assert res.returncode != 0
    assert "missing id" in res.stderr


# Sizes from pkg_meta in nemesis.h. A value that does not fit is refused rather than cut short.
FIELDS = [
    ("name", 128, "x"),
    ("type", 80, "x"),
    ("package-version", 32, "1"),
    ("depends", 512, "x"),
    ("os", 256, "x"),
    ("models", 256, "x"),
]


@pytest.mark.parametrize("key, size, char", FIELDS)
def test_a_field_that_does_not_fit_is_refused(nem, key, size, char):
    """Given a descriptor field exactly the size of its buffer, and one a byte smaller
    When the unit reads it
    Then the first is refused and named, and the second is accepted (meta.c:165)
    """
    version = "version=1.1\n" if key == "type" else ""
    too_long = info(nem, f"[nemesis]\n{version}id=my.tool\n{key}={char * size}\n")
    assert too_long.returncode != 0
    assert f"{key} is too long" in too_long.stderr
    assert info(nem, f"[nemesis]\n{version}id=my.tool\n{key}={char * (size - 1)}\n").returncode == 0


def test_an_id_too_long_for_its_buffer_is_refused(nem):
    res = info(nem, "[nemesis]\nid=" + "x" * 96 + "\n")
    assert res.returncode != 0
    assert "id is too long" in res.stderr


def test_ini_values_keep_their_quoted_forms(nem):
    res = info(nem, '[nemesis]\nid=my.tool\nname=\t"a\\"b\\\\c"\n')
    assert field(res, "name") == 'a"b\\c'


@pytest.mark.parametrize("quote", ["", '"'], ids=["plain", "quoted"])
def test_ini_values_fit_up_to_their_limit(nem, quote):
    """Given a value of 1023 bytes, and one of 1024 (ini.c:100)
    When the unit reads them
    Then the first is accepted and the second is refused by name
    """
    fits = info(nem, f"[nemesis]\nid=my.tool\nnote={quote}{'x' * 1023}{quote}\n")
    assert fits.returncode == 0, fits.stderr
    over = info(nem, f"[nemesis]\nid=my.tool\nnote={quote}{'x' * 1024}{quote}\n")
    assert over.returncode != 0
    assert "value too long" in over.stderr


@pytest.mark.parametrize(
    "text",
    [
        "[nemesis]\rid=my.tool\rname=N\r",
        "[nemesis]\r\n\tid=my.tool\r\n",
        '[nemesis]\n# x="never closed\nid=my.tool\n',
        '[nemesis]\n; x="never closed\nid=my.tool\n',
    ],
    ids=["lone-cr", "tab-indent", "hash-comment", "semicolon-comment"],
)
def test_ini_line_forms_are_read(nem, text):
    res = info(nem, text)
    assert res.returncode == 0, res.stderr
    assert field(res, "id") == "my.tool"


def failing_preuninstall(nem):
    members = {"NEMESIS.data": "[nemesis]\nid=my.tool\npreuninstall=scripts/u.sh\n", "scripts/u.sh": "exit 1\n"}
    assert nem.run("install", make_zip(nem.workdir / "p.zip", members)).returncode == 0


def test_a_failed_uninstall_keeps_the_package(nem):
    failing_preuninstall(nem)
    assert nem.run("uninstall", "my.tool").returncode == 5
    assert nem.app().is_dir()


def test_force_carries_on_past_a_failed_uninstall(nem):
    failing_preuninstall(nem)
    assert nem.run("uninstall", "--force", "my.tool").returncode == 0
    assert not nem.app().exists()


def test_a_core_install_is_locked_and_only_core_can_replace_it(nem):
    """Given a package installed with --core, as a NEMESIS update does (pkg.c:723)
    When something tries to replace or remove it
    Then only another --core install can
    """
    package = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\nname=N\ntype=service\n"})
    assert nem.run("install", "--core", package).returncode == 0
    assert "my.tool|N|1|service" in nem.db()
    assert nem.run("install", "--update", package).returncode == 2
    assert nem.run("uninstall", "my.tool").returncode != 0
    assert nem.run("install", "--update", "--core", package).returncode == 0


def test_wipe_reboots_unless_told_not_to(nem):
    assert nem.run("wipe", "--confirm").returncode == 0
    assert "reboot" in nem.calls()


def test_wipe_with_no_reboot_does_not_reboot(nem):
    assert nem.run("wipe", "--confirm", "--no-reboot").returncode == 0
    assert "reboot" not in nem.calls()


# --- Service manager changes in 1.16


def test_starting_a_disabled_service_by_hand_gives_it_one_more_run(nem):
    """Given a restart service that was disabled after 5 deaths (04-Packages.md section 4)
    When it is started by hand
    Then it runs, and the log says it was a manual start
    """
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=restart\n"
    nem.run("install", nem.package(log_hooks=False, sections=sections, files={"svc/d.sh": "sleep 60\n"}))
    nem.run("service", "stop", "my.tool/d")
    nem.path("data/nemesis/pids/my.tool+d.fails").write_text("5\n")
    assert nem.run("service", "start", "my.tool/d").returncode == 0
    assert "|running" in nem.run("service", "status", "my.tool/d").stdout
    assert "manual start of disabled service my.tool/d" in nem.log()


def sleeper(nem, name="d"):
    sections = f"[service.0]\nname={name}\nkind=sh\npath=svc/d.sh\nmode=background\n"
    assert nem.run("install", nem.package(log_hooks=False, sections=sections, files={"svc/d.sh": "sleep 60\n"})).returncode == 0
    return nem.path(f"data/nemesis/pids/my.tool+{name}")


def is_running(nem, qname="my.tool/d"):
    return "|running" in nem.run("service", "status", qname).stdout


def test_a_pidfile_records_the_process_and_this_boot(nem):
    """Given a service started on this boot (util.c:512)
    When its pidfile is written
    Then it holds the pid, its start time, and the 32 hex token kept in tmpfs
    """
    fields = sleeper(nem).read_text().split()
    assert len(fields) == 3
    token = nem.path("dev/.nemesis-boot").read_text().strip()
    assert re.fullmatch(r"[0-9a-f]{32}", token)
    assert fields[2] == token


def test_a_pidfile_from_an_earlier_boot_does_not_count(nem):
    """Given a live process whose pid and start time match a pidfile, as can repeat across boots
    When the file's token is from another boot
    Then the service counts as stopped
    """
    pidfile = sleeper(nem)
    assert is_running(nem)
    pid, start, _ = pidfile.read_text().split()
    pidfile.write_text(f"{pid} {start} {'0' * 32}\n")
    assert not is_running(nem)


def test_a_new_token_after_a_reboot_makes_old_pidfiles_stale(nem):
    pidfile = sleeper(nem)
    token = nem.path("dev/.nemesis-boot")
    token.write_text("1" * 32 + "\n")
    assert not is_running(nem)
    assert pidfile.exists()


def test_a_matching_token_needs_the_start_time_to_match_too(nem):
    pidfile = sleeper(nem)
    pid, start, token = pidfile.read_text().split()
    pidfile.write_text(f"{pid} {int(start) + 1} {token}\n")
    assert not is_running(nem)


def test_a_matching_token_is_proof_without_the_command_line(nem):
    """Given a pidfile with the right pid, start time and token, on a service whose name is not in its command line"""
    pidfile = sleeper(nem)
    proc = subprocess.Popen(["sleep", "60"])
    try:
        start = open(f"/proc/{proc.pid}/stat").read().rsplit(")", 1)[1].split()[19]
        token = pidfile.read_text().split()[2]
        pidfile.write_text(f"{proc.pid} {start} {token}\n")
        assert is_running(nem)
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("command, running", [("zzdaemon", True), ("other", False)])
def test_a_pidfile_from_1_15_needs_the_command_line_to_match(nem, command, running):
    """Given a pidfile with no token, as 1.15 wrote them (util.c:594)
    When its start time matches a live process
    Then the service counts as running only if the process's command line names it
    """
    pidfile = sleeper(nem, "zzdaemon")
    script = nem.workdir / command
    script.write_text("sleep 60\n")
    proc = subprocess.Popen(["sh", str(script)])
    try:
        assert wait_for(lambda: str(script).encode() in open(f"/proc/{proc.pid}/cmdline", "rb").read())
        start = open(f"/proc/{proc.pid}/stat").read().rsplit(")", 1)[1].split()[19]
        pidfile.write_text(f"{proc.pid} {start}\n")
        assert is_running(nem, "my.tool/zzdaemon") == running
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("text", ["short\n", "", "g" * 10 + "\n"], ids=["short", "empty", "not-32"])
def test_a_damaged_token_file_falls_back_to_a_pidfile_without_a_token(nem, text):
    """Given a token file too short to use, which link() cannot replace (util.c:538)
    When a service starts
    Then its pidfile has no token, and the service still counts as running
    """
    nem.path("dev/.nemesis-boot").write_text(text)
    pidfile = sleeper(nem)
    assert len(pidfile.read_text().split()) == 2
    assert is_running(nem)


def test_a_token_is_created_only_once(nem):
    sleeper(nem)
    token = nem.path("dev/.nemesis-boot").read_text()
    nem.run("service", "stop", "my.tool/d")
    nem.run("service", "start", "my.tool/d")
    assert nem.path("dev/.nemesis-boot").read_text() == token
    assert [p.name for p in nem.path("dev").glob(".nemesis-boot.*")] == []


def test_a_unit_where_no_token_can_be_written_still_runs_services(nem):
    dev = nem.path("dev")
    dev.chmod(0o555)
    try:
        pidfile = sleeper(nem)
        assert len(pidfile.read_text().split()) == 2
        assert is_running(nem)
    finally:
        dev.chmod(0o755)


def test_a_stale_watchdog_pidfile_naming_this_process_does_not_stop_it(nem):
    """Given a watchdog pidfile holding the pid the new watchdog happens to get (util.c:574)
    When the watchdog starts
    Then it does not take itself for one already running
    """
    pidfile = nem.path("data/nemesis/watchdog.pid")
    subprocess.Popen(["sh", "-c", f'echo "$$ 1 {"0" * 32}" > "{pidfile}"; exec "{nem.cli}" watchdog'], cwd=str(nem.workdir), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert wait_for(lambda: "watchdog start" in nem.log()), nem.log()
    assert "already running" not in nem.log()


def test_a_running_service_earns_back_its_restart_count_only_after_staying_up(nem):
    """Given a running service with deaths on record (04-Packages.md section 4)
    When svcmgr watches it
    Then the count stands while the service is young, and clears once it has stayed up 10 seconds
    """
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=restart\n"
    nem.run("install", nem.package(log_hooks=False, sections=sections, files={"svc/d.sh": "sleep 60\n"}))
    fails = nem.path("data/nemesis/pids/my.tool+d.fails")
    fails.write_text("3\n")
    nem.daemon("svcmgr")
    time.sleep(4)
    assert fails.read_text().strip() == "3"
    assert wait_for(lambda: fails.read_text().strip() == "0", 15)


def android_up_events(nem):
    return nem.calls().count("com.nemesis.manager.ANDROID_UP")


def test_android_must_hold_up_before_it_is_announced(nem):
    """Given Android just up, since the package manager can dip the value during an install (svc.c:324)
    When the service manager has seen it for under 5 seconds
    Then nothing is sent yet, and the service manager outlives the event
    """
    daemon = nem.daemon("svcmgr")
    time.sleep(3)
    assert android_up_events(nem) == 0
    assert wait_for(lambda: android_up_events(nem) == 1, 15), nem.log()
    assert daemon.poll() is None


def test_android_up_is_announced_only_once_android_is_up(nem):
    """Given the service manager running with Android down (svc.c:329)
    When Android comes up and stays up
    Then the event goes out once, and not before
    """
    nem.android(up=False)
    nem.daemon("svcmgr")
    time.sleep(7)
    assert android_up_events(nem) == 0
    nem.android(up=True)
    assert wait_for(lambda: android_up_events(nem) == 1, 20), nem.log()


def test_android_up_is_not_announced_twice_in_one_boot(nem):
    """Given the event already sent this boot
    When the daemons restart
    Then it is not sent again, because the mark holds the boot id
    """
    nem.daemon("svcmgr")
    assert wait_for(lambda: android_up_events(nem) == 1, 20), nem.log()
    assert nem.path("data/nemesis/android-up").read_text().strip()
    nem.kill_all()
    nem.daemon("svcmgr")
    time.sleep(9)
    assert android_up_events(nem) == 1


# --- nemesis usb-info


def test_usb_info_with_no_stick(nem):
    res = nem.run("usb-info")
    assert res.returncode == 1
    assert res.stdout == "mounted=0\n"


def test_usb_info_with_a_mounted_stick_and_no_descriptor(nem):
    (nem.root / "proc_mounts").write_text("/dev/block/sda1 /storage/usb0 vfat rw 0 0\n")
    res = nem.run("usb-info")
    assert res.returncode == 1
    assert res.stdout == "mounted=1\n"


def test_usb_info_describes_the_stick(nem):
    usb = nem.stick("[nemesis]\ntype=install\npath=p.zip\n")
    res = nem.run("usb-info")
    assert res.returncode == 0
    assert field(res, "mounted") == "1"
    assert field(res, "meta") == f"{usb}/NEMESIS.data"
    assert field(res, "root") == str(usb)
    assert field(res, "type") == "install"
    assert field(res, "path") == f"{usb}/p.zip"


def test_usb_info_leaves_out_a_path_the_handler_would_refuse(nem):
    nem.stick("[nemesis]\ntype=install\npath=../p.zip\n")
    assert "path=" not in nem.run("usb-info").stdout


def test_usb_info_reports_a_descriptor_it_cannot_read(nem):
    nem.stick('[nemesis]\ntype="never closed\n')
    res = nem.run("usb-info")
    assert res.returncode == 1
    assert "type=" not in res.stdout


def test_usb_info_list_prints_the_descriptor(nem):
    text = "[nemesis]\ntype=list\n\n[package.0]\npath=p.zip\n"
    nem.stick(text)
    res = nem.run("usb-info", "--list")
    assert res.returncode == 0
    assert res.stdout == text


# --- USB type=update and type=list


def run_handler(nem):
    return nem.daemon("usb-handler")


def handled(nem):
    assert wait_for(lambda: "handled" in nem.session_log(), timeout=25), nem.session_log() + nem.log()


def update_stick(nem, version=NEWER, meta="", **kwargs):
    package = update_zip(nem, version, meta=meta, **kwargs)
    return nem.stick("[nemesis]\ntype=update\npath=u.zip\n", {"u.zip": package.read_bytes()})


def test_a_newer_update_stick_asks_then_updates(nem):
    """Given a stick with a newer core update and Android up (05-Run-Scripts.md section 2)
    When the user answers yes
    Then the update runs
    """
    nem.answer_dialogs("yes")
    update_stick(nem)
    run_handler(nem)
    assert wait_for(lambda: "reboot" in nem.calls(), timeout=25), nem.log()
    assert "nemesis_mode confirm_update" in nem.calls()
    assert nem.cli.read_bytes() == new_binary(nem)


def test_declining_an_update_stick_changes_nothing(nem):
    nem.answer_dialogs("no")
    update_stick(nem)
    run_handler(nem)
    handled(nem)
    assert nem.cli.read_bytes() == nem.binary.read_bytes()


@pytest.mark.parametrize("version", [OLDER, VERSION])
def test_an_update_stick_that_is_not_newer_reports_without_asking(nem, version):
    nem.answer_dialogs("ok")
    update_stick(nem, version)
    run_handler(nem)
    handled(nem)
    assert "nemesis_mode up_to_date" in nem.calls()
    assert "confirm_update" not in nem.calls()


def test_an_update_stick_runs_with_android_down_only_when_marked_for_emergency(nem):
    nem.android(up=False)
    update_stick(nem)
    run_handler(nem)
    assert wait_for(lambda: "reboot" in nem.calls(), timeout=25), nem.log()
    assert "am " not in nem.calls()


def test_an_update_stick_that_fails_says_so(nem):
    nem.answer_dialogs("yes")
    update_stick(nem, sums=False)
    run_handler(nem)
    assert wait_for(lambda: "nemesis_mode result_fail" in nem.calls(), timeout=25), nem.log()
    assert nem.cli.read_bytes() == nem.binary.read_bytes()
    assert "confirm_risk" not in nem.calls()


def test_a_declined_update_stick_asks_only_once(nem):
    nem.answer_dialogs("no")
    update_stick(nem)
    run_handler(nem)
    handled(nem)
    time.sleep(4)
    assert nem.calls().count("nemesis_mode confirm_update") == 1


def test_a_list_stick_opens_the_store_only_once(nem):
    nem.stick("[nemesis]\ntype=list\n")
    run_handler(nem)
    assert wait_for(lambda: "store_mode usb" in nem.calls()), nem.log()
    time.sleep(4)
    assert nem.calls().count("store_mode usb") == 1


@pytest.mark.parametrize("answer, installed", [("yes", True), ("no", False)])
def test_a_first_install_of_an_incompatible_package_asks_about_the_risk(nem, answer, installed):
    """Given a stick with a package marked for another OS and model, not yet installed (usb.c:425)
    When the user confirms the install and then answers the risk dialog
    Then yes installs it, and no leaves nothing behind
    """
    answer_in_turn(nem, "yes", answer, "ok")
    package = make_zip(nem.workdir / "new.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\nos=none\nmodels=none\n"})
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": package.read_bytes()})
    run_handler(nem)
    expected = "nemesis_mode result_ok" if installed else "nemesis_mode confirm_risk"
    assert wait_for(lambda: expected in nem.calls(), timeout=25), nem.log()
    if not installed:
        assert wait_for(lambda: "risk declined" in nem.log(), timeout=25), nem.log()
    assert ("my.tool|" in nem.db()) == installed


def answer_in_turn(nem, *replies):
    """Manager stand-in that gives each dialog the next reply, as a person tapping would."""
    answers = nem.root / "answers"
    answers.write_text("".join(f"{reply}\n" for reply in replies))
    session = nem.root / "data" / "nemesis" / "usb-session"
    script = (
        f'echo "am $*" >> "{nem.root}/calls"\n'
        f'(sleep 1; mkdir -p "{session}"; head -1 "{answers}" > "{session}/reply"; sed -i 1d "{answers}") >/dev/null 2>&1 &\n'
    )
    executable(nem.bin("am"), script)


def test_accepting_the_risk_on_an_update_stick_updates(nem):
    answer_in_turn(nem, "yes", "yes", "ok")
    update_stick(nem, meta="os=none\nmodels=none\n")
    run_handler(nem)
    assert wait_for(lambda: "reboot" in nem.calls(), timeout=30), nem.log()
    assert "nemesis_mode confirm_risk" in nem.calls()
    assert nem.cli.read_bytes() == new_binary(nem)


def test_declining_the_risk_on_an_update_stick_changes_nothing(nem):
    """Given an update stick marked for another OS and model (update.c exit 20)
    When the user confirms the update and then declines the risk
    Then nothing is installed and no reboot happens
    """
    answer_in_turn(nem, "yes", "no")
    update_stick(nem, meta="os=none\nmodels=none\n")
    run_handler(nem)
    assert wait_for(lambda: "compatibility risk declined" in nem.log(), timeout=30), nem.log()
    assert "reboot" not in nem.calls()
    assert nem.cli.read_bytes() == nem.binary.read_bytes()


def test_with_android_down_an_old_update_stick_is_recorded_as_up_to_date(nem):
    nem.android(up=False)
    update_stick(nem, OLDER)
    run_handler(nem)
    handled(nem)
    assert "up-to-date" in nem.session_log()
    assert nem.cli.read_bytes() == nem.binary.read_bytes()


def test_the_log_says_why_a_stick_is_waiting(nem):
    nem.android(up=False)
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", emergency=False)
    run_handler(nem)
    assert wait_for(lambda: "waiting for Android" in nem.log()), nem.log()
    time.sleep(2)
    assert nem.log().count("waiting for Android") == 1


@pytest.mark.parametrize(
    "descriptor, files",
    [
        ("[nemesis]\ntype=update\npath=../u.zip\n", {}),
        ("[nemesis]\ntype=update\npath=absent.zip\n", {}),
        ("[nemesis]\ntype=update\npath=u.zip\n", {"u.zip": "not a zip"}),
    ],
    ids=["bad-path", "absent", "not-a-zip"],
)
def test_an_update_stick_it_cannot_read_is_invalid(nem, descriptor, files):
    nem.answer_dialogs("ok")
    nem.stick(descriptor, files)
    run_handler(nem)
    handled(nem)
    assert "nemesis_mode invalid" in nem.calls()


def test_an_update_stick_with_another_package_is_invalid(nem):
    nem.answer_dialogs("ok")
    package = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\npackage-version=1.0\n"})
    nem.stick("[nemesis]\ntype=update\npath=u.zip\n", {"u.zip": package.read_bytes()})
    run_handler(nem)
    handled(nem)
    assert "nemesis_mode invalid" in nem.calls()
    assert "my.tool" not in nem.db()


def test_a_list_stick_opens_the_store(nem):
    usb = nem.stick("[nemesis]\ntype=list\n")
    run_handler(nem)
    assert wait_for(lambda: "store_mode usb" in nem.calls()), nem.log()
    assert f"store_list {usb}/NEMESIS.data" in nem.calls()


def test_a_list_stick_waits_for_android_even_on_an_emergency_stick(nem):
    nem.android(up=False)
    nem.stick("[nemesis]\ntype=list\n", emergency=True)
    run_handler(nem)
    assert wait_for(lambda: "type=list waits for Android" in nem.log()), nem.log()
    assert "store_mode" not in nem.calls()
    nem.android(up=True)
    assert wait_for(lambda: "store_mode usb" in nem.calls()), nem.log()


# --- USB reinstall


def reinstall_stick(nem, new_members):
    nem.run("install", nem.package(log_hooks=False, files={"v": "1"}))
    package = make_zip(nem.workdir / "new.zip", new_members)
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": package.read_bytes()})


def test_a_failed_reinstall_from_a_stick_keeps_the_installed_package(nem):
    """Given an installed package and a stick whose new copy fails its preinstall (04-Packages.md section 5)
    When the user confirms the update
    Then the failure is reported and the package they had is still there
    """
    nem.answer_dialogs("yes")
    reinstall_stick(nem, {"NEMESIS.data": "[nemesis]\nid=my.tool\npreinstall=scripts/p.sh\n", "scripts/p.sh": "exit 1\n"})
    run_handler(nem)
    handled(nem)
    assert "nemesis_mode result_fail" in nem.calls()
    assert (nem.app() / "v").read_text() == "1"
    assert "my.tool|" in nem.db()


def test_declining_the_risk_on_a_reinstall_keeps_the_installed_package(nem):
    """Given an installed package and a stick whose new copy is marked incompatible
    When the user confirms the update and then declines the risk
    Then nothing has changed
    """
    device(nem)
    answer_in_turn(nem, "yes", "no")
    reinstall_stick(nem, {"NEMESIS.data": "[nemesis]\nid=my.tool\nos=9\nmodels=none\n"})
    run_handler(nem)
    assert wait_for(lambda: "nemesis_mode confirm_risk" in nem.calls(), timeout=25), nem.log()
    assert wait_for(lambda: "risk declined" in nem.log(), timeout=25), nem.log()
    assert (nem.app() / "v").read_text() == "1"
    assert "my.tool|" in nem.db()
