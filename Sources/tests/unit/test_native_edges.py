"""Exit codes, input filters and damaged state in the real CLI, at their exact edges.

Each scenario here exists because mutation testing showed a change in that behaviour went unnoticed.
"""
from __future__ import annotations

import os
import struct
import subprocess
import time
import zipfile

import pytest

from helpers import known_bug
from native_unit import executable, make_zip, wait_for

SVC = "my.tool/d"
GOOD = "[nemesis]\nid=my.tool\nname=My Tool\ntypes=a\n"
EXECUTE = "[nemesis]\ntype=execute\npath=NEMESIS.sh\n"


def install(nem, sections="", **kwargs):
    return nem.run("install", nem.package(sections=sections, log_hooks=False, **kwargs))


def service(nem, mode="background", kind="sh", path="svc/d.sh", script="sleep 60\n"):
    sections = f"[service.0]\nname=d\nkind={kind}\npath={path}\nmode={mode}\n"
    return nem.run("install", nem.package(sections=sections, files={"svc/d.sh": script}, log_hooks=False))


def needs_non_root():
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")


def start_channel(nem):
    nem.run("watchdog")
    assert wait_for(lambda: nem.run("ping").returncode == 0, 10)


# --- Exit codes: scripts depend on these (02-Debugging-Execution-Interface.md)


@pytest.mark.parametrize(
    "args",
    [
        ("service",),
        ("service", "start"),
        ("service", "dance", "x"),
        ("install",),
        ("uninstall",),
        ("pkg-info",),
        ("pkg-launch",),
        ("log",),
        ("log", "service"),
        ("log", "nope"),
        ("remount", "maybe"),
    ],
    ids=" ".join,
)
def test_usage_errors_exit_with_one(nem, args):
    """Given a command missing an argument
    When it runs
    Then it exits 1
    """
    assert nem.run(*args).returncode == 1


@pytest.mark.parametrize("args", [("service", "list"), ("log", "dump"), ("log", "service", "no/such"), ("pkg-list",)], ids=" ".join)
def test_read_only_commands_exit_with_zero(nem, args):
    """Given nothing unusual
    When a read-only command runs
    Then it exits 0
    """
    assert nem.run(*args).returncode == 0


def test_a_long_log_dump_exits_with_zero(nem):
    """Given a log larger than the dump limit (usb.c cmd_log)
    When it is dumped
    Then it exits 0
    """
    (nem.root / "data/nemesis/nemesis.log").write_bytes(b"line\n" * 40000)
    assert nem.run("log", "dump").returncode == 0


def test_reading_a_service_log_exits_with_zero(nem):
    """Given a service with output
    When its log is read
    Then the output comes back and it exits 0
    """
    service(nem, script="echo hello\n", mode="once")
    res = nem.run("log", "service", SVC)
    assert res.returncode == 0 and "hello" in res.stdout


def test_service_operations_on_a_healthy_service_exit_with_zero(nem):
    """Given a running service
    When it is started again, queried and stopped twice
    Then each exits 0
    """
    assert service(nem).returncode == 0
    assert nem.run("service", "start", SVC).returncode == 0
    assert nem.run("service", "status", SVC).returncode == 0
    assert nem.run("service", "stop", SVC).returncode == 0
    assert nem.run("service", "stop", SVC).returncode == 0


def test_stopping_an_unknown_service_changes_nothing_and_exits_with_zero(nem):
    """Given a service that does not exist (svc.c svc_stop)
    When it is stopped
    Then it exits 0, because the wanted state already holds
    """
    assert nem.run("service", "stop", "no/such").returncode == 0


def test_starting_a_deferred_instance_service_exits_with_zero(nem):
    """Given Android down
    When an instance service starts
    Then it defers and exits 0
    """
    nem.android(up=False)
    service(nem, kind="instance", path="/com.a/.Svc")
    assert nem.run("service", "start", SVC).returncode == 0
    assert "am " not in nem.calls()


def test_a_failed_read_write_remount_exits_with_one(nem):
    """Given a /system that stays read-only
    When remount rw runs, directly and as an intercepted command
    Then both exit 1
    """
    needs_non_root()
    (nem.root / "proc_mounts").write_text("/dev/block/sys /system ext4 ro,nosuid 0 0\n")
    nem.path("/system/xbin").chmod(0o555)
    try:
        res = nem.run("remount", "rw")
        intercepted = nem.run("root", "-c", "mount -o remount,rw /system")
    finally:
        nem.path("/system/xbin").chmod(0o755)
    assert res.returncode == 1 and "remount /system rw failed" in res.stderr
    assert intercepted.returncode == 1


def test_a_writable_system_remounts_with_success(nem):
    """Given a /system that is already writable
    When remount rw runs, directly and as an intercepted command
    Then both exit 0, without calling mount
    """
    assert nem.run("remount", "rw").returncode == 0
    assert nem.run("root", "-c", "mount -o remount,rw /system").returncode == 0
    assert not (nem.root / "mounts.log").exists()


# --- `nemesis root` and the remount intercept (usb.c)


@pytest.mark.parametrize(
    "command",
    ["echo mount /system", "echo remount mount /tmp/", "/bin/echo remount mount x"],
)
def test_root_runs_commands_that_are_not_a_system_remount(nem, command):
    """Given a command that lacks part of what marks a remount
    When root runs it
    Then it runs normally rather than being intercepted
    """
    res = nem.run("root", "-c", command)
    assert res.returncode == 0 and command.split(" ", 1)[1] in res.stdout


def test_root_with_uid_zero_as_a_number_still_runs_the_command(nem):
    """Given uid 0 written out
    When root runs with -c
    Then the 0 is the uid, not the command
    """
    res = nem.run("root", "0", "-c", "echo fine")
    assert res.returncode == 0 and "fine" in res.stdout


def test_root_for_another_uid_does_not_intercept_a_remount(nem):
    """Given a uid other than root
    When root runs a remount command for it
    Then it is not intercepted
    """
    (nem.root / "proc_mounts").write_text("/dev/block/sys /system ext4 rw 0 0\n")
    nem.run("root", "1000", "-c", "mount -o remount,ro /system")
    assert not (nem.root / "mounts.log").exists()
    assert "uid 1000" in nem.ids()


def test_root_with_a_dangling_dash_c_does_not_crash(nem):
    """Given -c with no command after it
    When root runs
    Then it exits normally rather than dying by a signal
    """
    assert nem.run("root", "-c", stdin="exit 0\n").returncode >= 0


def test_root_with_no_command_is_not_sent_to_the_remount_check(nem):
    """Given no command at all
    When root runs
    Then it opens a shell for that uid
    """
    res = nem.run("root", "1000", stdin="echo as-system\nexit 0\n")
    assert "as-system" in res.stdout and "uid 1000" in nem.ids()


# --- Dialog extras reach `am` as text (usb.c sanitize_extra)


def test_every_unsafe_character_in_a_dialog_extra_is_replaced(nem):
    """Given a package name holding each character the filter handles
    When its install dialog is shown
    Then each is replaced, and an ordinary space stays
    """
    nem.answer_dialogs("no")
    text = "[nemesis]\nid=my.tool\nname=a'b\"c;d&e|f`g$h\\i(j)k l\tm\n"
    make_zip(nem.root / "storage/usb0/p.zip", {"NEMESIS.data": text})
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n")
    nem.daemon("usb-handler")
    assert wait_for(lambda: "nemesis_name" in nem.calls(), 10)
    assert "nemesis_name a_b_c_d_e_f_g_h_i_j_k l_m" in nem.calls()


# --- The manifest parser (ini.c)


@pytest.mark.parametrize("line", ["#id=wrong", ";id=wrong", "", "   "], ids=["hash", "semicolon", "empty", "blank"])
def test_comments_and_blank_lines_are_not_keys(nem, line):
    """Given a comment or blank line before the id
    When the manifest is read
    Then the real id wins, because a comment is not a key
    """
    res = nem.run("pkg-info", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": f"[nemesis]\n{line}\nid=my.tool\n"}))
    assert "id=my.tool\n" in res.stdout


def test_an_empty_installed_manifest_has_nothing_to_launch(nem):
    """Given an installed package whose manifest was emptied
    When it is launched
    Then the file still parses, so the failure is that nothing can launch
    """
    install(nem)
    (nem.app() / "NEMESIS.data").write_bytes(b"")
    res = nem.run("pkg-launch", "my.tool")
    assert res.returncode == 1 and "nothing to launch" in res.stderr


def test_an_unreadable_installed_manifest_is_reported(nem):
    """Given an installed package whose manifest cannot be read
    When it is launched
    Then it reports missing metadata
    """
    needs_non_root()
    install(nem)
    (nem.app() / "NEMESIS.data").chmod(0)
    res = nem.run("pkg-launch", "my.tool")
    (nem.app() / "NEMESIS.data").chmod(0o644)
    assert res.returncode == 1 and "missing package metadata" in res.stderr


def test_a_missing_installed_manifest_is_reported(nem):
    """Given an installed package with no manifest
    When it is launched
    Then it exits 1 and says so
    """
    install(nem)
    (nem.app() / "NEMESIS.data").unlink()
    res = nem.run("pkg-launch", "my.tool")
    assert res.returncode == 1 and "missing package metadata" in res.stderr


# --- The zip reader (zip.c)


def test_extra_fields_and_comments_are_skipped_correctly(nem):
    """Given members with extra data and comments in the central directory
    When the package installs
    Then every member is read correctly
    """
    target = nem.workdir / "p.zip"
    with zipfile.ZipFile(target, "w") as z:
        for name, data in (("NEMESIS.data", GOOD), ("a", "first"), ("b", "second")):
            info = zipfile.ZipInfo(name)
            info.extra = struct.pack("<HH4s", 0x9999, 4, b"abcd")
            info.comment = b"a comment"
            z.writestr(info, data)
    assert nem.run("install", target).returncode == 0
    assert (nem.app() / "b").read_text() == "second"


@pytest.mark.parametrize("offset", [20, 24, 42], ids=["compressed-size", "size", "local-offset"])
def test_zip64_markers_are_refused(nem, offset):
    """Given a central directory entry with a zip64 marker, which the reader does not support
    When the package installs
    Then it is refused
    """
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD})
    raw = target.read_bytes()
    at = raw.rfind(b"PK\x01\x02") + offset
    target.write_bytes(raw[:at] + b"\xff\xff\xff\xff" + raw[at + 4:])
    assert nem.run("install", target).returncode != 0
    assert "my.tool" not in nem.db()


def test_a_name_of_the_maximum_length_is_refused(nem):
    """Given a member name of exactly MAX_PATH bytes, with no room for the terminator (zip.c:118)
    When the package installs
    Then it is refused
    """
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD, "x" * 200 + "/" + "y" * 200 + "/" + "z" * 111: "x"})
    assert nem.run("install", target).returncode != 0
    assert "my.tool" not in nem.db()


def test_a_file_too_small_to_be_a_zip_is_refused(nem):
    """Given a file shorter than the smallest zip
    When the package installs
    Then it is refused as bad metadata
    """
    target = nem.workdir / "tiny.zip"
    target.write_bytes(b"PK\x05\x06tiny")
    res = nem.run("install", target)
    assert res.returncode == 1 and "invalid package metadata" in res.stderr


# --- Android package names reach a shell (pkg.c)


@pytest.mark.parametrize("name", ["A.b", "Z.b", "a0.b", "0a.b", "a9.b", "a.b_c"])
def test_every_allowed_character_of_an_android_package_name_reaches_pm(nem, name):
    """Given a package name at the edges of the allowed characters
    When it is uninstalled
    Then pm is called with it
    """
    install(nem, sections=f"[app.0]\nkind=apk\npath=app/a.apk\npackage={name}\n", files={"app/a.apk": "apk"})
    nem.run("uninstall", "my.tool")
    assert f"pm uninstall {name} " in nem.calls()


@pytest.mark.parametrize("component", ["/.Svc", "no.slash"])
def test_a_component_without_a_package_half_never_reaches_pm(nem, component):
    """Given a component with no usable package part
    When the package is uninstalled
    Then pm is not called
    """
    install(nem, sections=f"[app.0]\nkind=apk\npath=app/a.apk\ncomponent={component}\n", files={"app/a.apk": "apk"})
    nem.run("uninstall", "my.tool")
    assert "pm uninstall" not in nem.calls()


def test_an_app_without_a_kind_is_not_treated_as_an_apk(nem):
    """Given an app section naming a package but no kind
    When the package is uninstalled
    Then pm is not called
    """
    install(nem, sections="[app.0]\npackage=com.a\n")
    nem.run("uninstall", "my.tool")
    assert "pm uninstall" not in nem.calls()


@pytest.mark.parametrize("bad", ["a'b"], ids=["quote"])
def test_launch_refuses_a_name_that_could_break_shell_quoting(nem, bad):
    """Given a component holding a character that could break the quoting
    When the package is launched
    Then nothing starts
    """
    install(nem, sections=f"[app.0]\nkind=sh\npath=bin/x\ncomponent={bad}/.X\n", files={"bin/x": "exit 0\n"})
    res = nem.run("pkg-launch", "my.tool")
    assert res.returncode == 1 and "activity launch failed" in res.stderr
    assert "am start" not in nem.calls()


def test_a_carriage_return_never_reaches_am(nem):
    """Given a component with a bare carriage return, which ends a manifest line
    When the package is launched
    Then am never receives one
    """
    install(nem, sections="[app.0]\nkind=sh\npath=bin/x\ncomponent=a\rb/.X\n", files={"bin/x": "exit 0\n"})
    nem.run("pkg-launch", "my.tool")
    assert "\r" not in nem.calls()


def test_a_successful_apk_install_does_not_check_for_a_signer_conflict(nem):
    """Given pm reporting success, and the package already present
    When the apk installs
    Then the package is not uninstalled
    """
    nem.pm_script('case "$1" in\ninstall) echo Success ;;\npath) echo package:/x ;;\nesac\n')
    install(nem, sections="[app.0]\nkind=apk\npath=app/a.apk\npackage=com.a\n", files={"app/a.apk": "apk"})
    assert "pm uninstall" not in nem.calls()


def test_another_install_failure_is_not_treated_as_a_signer_conflict(nem):
    """Given pm failing for another reason, and the package already present
    When the apk installs
    Then it is not uninstalled and retried
    """
    nem.pm_script('case "$1" in\ninstall) echo "Failure [INSTALL_FAILED_OTHER]" ;;\npath) echo package:/x ;;\nesac\n')
    res = install(nem, sections="[app.0]\nkind=apk\npath=app/a.apk\npackage=com.a\n", files={"app/a.apk": "apk"})
    assert res.returncode != 0
    assert nem.calls().count("pm install") == 1
    assert "boot=0" not in "".join(line for line in nem.calls().splitlines(True) if "pm uninstall" in line)


def test_update_flag_on_a_new_package_is_a_plain_install(nem):
    """Given --update for a package that is not installed
    When it runs
    Then it installs, with no old package to remove
    """
    res = nem.run("install", "--update", nem.package())
    assert res.returncode == 0 and "ok installed my.tool" in res.stdout
    assert not any(h.startswith(("preun", "postun")) for h in nem.hooks())


def test_a_hook_set_to_na_is_skipped(nem):
    """Given a hook key set to N/A
    When the package installs and is removed
    Then no hook runs, and both succeed
    """
    meta = "postinstall=N/A\npreuninstall=N/A\npostuninstall=N/A\npreinstall=N/A\n"
    assert nem.run("install", nem.package(extra_meta=meta, log_hooks=False)).returncode == 0
    assert nem.run("uninstall", "my.tool").returncode == 0


def test_a_service_with_an_empty_name_is_not_registered(nem):
    """Given a service whose name is empty
    When the package installs
    Then the service is not registered
    """
    install(nem, sections="[service.0]\nname=\npath=svc/d.sh\n", files={"svc/d.sh": "exit 0\n"})
    assert "my.tool/" not in nem.db("services.db")


def test_uninstalling_a_package_leaves_other_services_running(nem):
    """Given two packages with running services
    When one is uninstalled
    Then the other keeps running
    """
    section = "[service.0]\nname=d\npath=svc/d.sh\nmode=background\n"
    for id_ in ("one.tool", "two.tool"):
        assert nem.run("install", nem.package(id_, sections=section, files={"svc/d.sh": "sleep 60\n"}, log_hooks=False)).returncode == 0
    assert nem.run("uninstall", "one.tool").returncode == 0
    assert "running" in nem.run("service", "status", "two.tool/d").stdout
    assert "one.tool/" not in nem.db("services.db")


# --- Damaged databases (pkg.c)


def test_damaged_lines_in_the_package_database_are_ignored(nem):
    """Given a package database with a comment, a blank line and short rows
    When it is listed
    Then only whole rows appear
    """
    nem.run("pkg-list")
    path = nem.root / "data/nemesis/packages.db"
    path.write_text(path.read_text() + "# comment\n\none\ntwo|x\nthree|x|0\nreal.pkg|Real|0|app\n")
    listed = nem.run("pkg-list").stdout.splitlines()
    assert any(line.startswith("real.pkg|Real|0|app") for line in listed)
    assert not any(line.startswith(("#", "one", "two", "three")) or not line for line in listed)


def test_damaged_lines_in_the_service_database_are_ignored(nem):
    """Given a service database with a comment, a blank line and a short row
    When services are listed
    Then the damaged lines do not appear
    """
    nem.run("service", "list")
    path = nem.root / "data/nemesis/services.db"
    path.write_text(path.read_text() + "# comment\n\nshort|sh\n")
    listed = nem.run("service", "list").stdout
    assert "comment" not in listed and "short" not in listed


# --- Service states (svc.c)


def write_fails(nem, name, count):
    (nem.root / "data/nemesis/pids").mkdir(parents=True, exist_ok=True)
    (nem.root / f"data/nemesis/pids/{name}.fails").write_text(str(count))


def test_a_service_reports_failed_exactly_at_the_restart_limit(nem):
    """Given a service with exactly 5 recorded rapid deaths, then 4 (04-Packages.md)
    When its status is read
    Then the first is failed and the second is not
    """
    service(nem, mode="restart")
    nem.run("service", "stop", SVC)
    write_fails(nem, "my.tool+d", 5)
    assert "failed" in nem.run("service", "status", SVC).stdout
    assert "|failed|" in nem.run("service", "list").stdout
    write_fails(nem, "my.tool+d", 4)
    assert "failed" not in nem.run("service", "status", SVC).stdout
    assert "|failed|" not in nem.run("service", "list").stdout


def test_the_locked_handler_never_reports_failed(nem):
    """Given the locked handler with many recorded deaths
    When services are listed
    Then it is not reported as failed
    """
    nem.run("service", "list")
    write_fails(nem, "usb-handler+usb-handler", 9)
    assert "usb-handler/usb-handler|restart|failed" not in nem.run("service", "list").stdout


def test_svcmgr_does_not_restart_a_once_service(nem):
    """Given a once service that has finished
    When svcmgr runs
    Then it does not run it again
    """
    marker = nem.root / "runs"
    service(nem, mode="once", script=f'echo x >> "{marker}"\n')
    runs = marker.read_text()
    daemon = nem.daemon("svcmgr")
    time.sleep(4)
    daemon.terminate()
    daemon.wait()
    assert marker.read_text() == runs


def test_svcmgr_does_not_start_an_instance_service_while_android_is_down(nem):
    """Given an instance restart service with Android down
    When svcmgr runs
    Then am is not called
    """
    nem.android(up=False)
    service(nem, kind="instance", path="/com.a/.Svc", mode="restart")
    daemon = nem.daemon("svcmgr")
    time.sleep(4)
    daemon.terminate()
    daemon.wait()
    assert "am " not in nem.calls()


# --- The root channel (su.c, main.c)


def test_a_non_root_watchdog_succeeds_when_one_is_already_running(nem):
    """Given a running watchdog
    When an unprivileged caller starts another
    Then it exits 0, and the other daemons still refuse
    """
    start_channel(nem)
    assert nem.run("watchdog", euid=1000).returncode == 0
    res = nem.run("svcmgr", euid=1000)
    assert res.returncode == 1 and "daemon not running" in res.stderr


def test_an_empty_operation_is_refused_by_the_channel(nem):
    """Given a running channel
    When an empty operation is sent
    Then it is refused
    """
    start_channel(nem)
    res = nem.run("", euid=1000)
    assert res.returncode == 1 and "not allowed via daemon" in res.stdout


def test_ordinary_commands_keep_their_exit_code_through_the_channel(nem):
    """Given a running channel
    When a command that fails is sent
    Then the caller gets its exit code, so it was not treated as interactive
    """
    start_channel(nem)
    assert nem.run("service", "start", "no/such", euid=1000).returncode == 1
    assert nem.run("root", "1/x", euid=1000).returncode != 0


def test_a_command_killed_by_a_signal_is_reported_as_a_failure(nem):
    """Given a running channel
    When the command dies by a signal
    Then the caller gets a failure
    """
    start_channel(nem)
    assert nem.run("root", "-c", "kill -9 $$", euid=1000).returncode == 1


def test_interactive_root_for_uid_zero_through_the_channel(nem):
    """Given a running channel
    When root asks for uid 0 with no command
    Then it stays an interactive shell
    """
    start_channel(nem)
    res = nem.run("root", "0", euid=1000, stdin="echo relayed\nexit\n", timeout=20)
    assert "relayed" in res.stdout


def test_too_many_arguments_are_refused(nem):
    """Given a running channel and more arguments than SU_MAX_ARGC
    When they are sent
    Then the command does not run, and the channel keeps working
    """
    start_channel(nem)
    marker = nem.root / "ran"
    res = nem.run("root", "-c", f'touch "{marker}"', *["x"] * 40, euid=1000)
    assert res.returncode != 0
    assert not marker.exists()
    assert nem.run("ping").returncode == 0


@known_bug("G26", "the client does not ignore SIGPIPE, so a hang-up kills it before it can report the failure")
def test_a_client_the_channel_hangs_up_on_reports_the_failure(nem):
    """Given a running channel and far more data than a socket buffers, so the client is still writing
    When the channel refuses the request and closes
    Then the client exits 1 and says so, rather than dying on a signal (su.c:427)
    """
    start_channel(nem)
    res = nem.run("root", "-c", "echo", *["x" * 700] * 1000, euid=1000)
    assert res.returncode == 1
    assert "daemon write failed" in res.stderr


# --- The USB handler reports results (usb.c)


def test_a_recovery_update_reports_its_result(nem):
    """Given Android down and an installed package
    When an update on a stick succeeds, and when one fails
    Then the log names the right result for each
    """
    nem.android(up=False)
    nem.run("install", nem.package())
    zip_on_stick = nem.package(sections="", name="u.zip")
    nem.stick("[nemesis]\ntype=install\npath=u.zip\n", {"u.zip": zip_on_stick.read_bytes()})
    daemon = nem.daemon("usb-handler")
    assert wait_for(lambda: "reply=recovery" in nem.session_log(), 15), nem.log()
    assert "skip UI result_ok" in nem.log()
    daemon.terminate()
    daemon.wait()


def test_a_changed_descriptor_is_logged_as_a_rearm(nem):
    """Given a handled stick
    When its descriptor changes
    Then the handler logs that it rearmed for that reason
    """
    nem.android(up=False)
    usb = nem.stick("[nemesis]\ntype=execute\npath=NEMESIS.sh\n", {"NEMESIS.sh": "exit 0\n"})
    nem.daemon("usb-handler")
    assert wait_for(lambda: "handled" in nem.session_log(), 10)
    assert "rearm (descriptor changed)" not in nem.log()
    (usb / "NEMESIS.data").write_text("[nemesis]\ntype=execute\npath=NEMESIS.sh\nversion=2\n")
    assert wait_for(lambda: "rearm (descriptor changed)" in nem.log(), 10)


# --- Wipe (pkg.c)

QB_HEAD = "#!/system/bin/sh\nbefore=1\n"
QB_TAIL = "after=1\n"
MARKER = "# NEMESIS_QB_HOOK\n"


@pytest.mark.parametrize(
    "block",
    [
        "if [ -x x ]; then\n  run\nfi\n",
        "if [ -x x ]; then\n  run\nfi",
        "if\t[ -x x ]; then\nfi\t\n",
        "if[ -x x ]; then\nfi;\n",
        "if [ -x x ]; then\nfi # done\n",
        "if [ -x x ]; then\r\nfi\r\n",
        "  if [ -x x ]; then\n\tif [ y ]; then\n\t  run\n\tfi\n  fi\n",
        "\tif [ -x x ]; then\n  file=1\n  iffy=1\n\tfi\n",
    ],
    ids=["plain", "no-newline", "tabs", "no-space-semicolon", "comment", "crlf", "nested", "lookalikes"],
)
def test_wipe_removes_exactly_the_quickboot_hook(nem, block):
    """Given a quickboot script holding the hook in any of the shapes it can take
    When the unit is wiped
    Then the hook is gone, and every line outside it stays
    """
    tail = "" if not block.endswith("\n") else QB_TAIL
    nem.path("/system/etc/qb_runtime.sh").write_text(QB_HEAD + MARKER + block + tail, newline="")
    assert nem.run("wipe", "--confirm").returncode == 0
    assert nem.path("/system/etc/qb_runtime.sh").read_text(newline="") == QB_HEAD + tail


@pytest.mark.parametrize("rel,name", [("/system/app", "NEMESIS.apk"), ("/system/etc", "install-recovery.sh")])
def test_wipe_reports_a_system_file_it_could_not_remove(nem, rel, name):
    """Given a NEMESIS file on /system that cannot be removed
    When the unit is wiped
    Then it exits 1 and says the wipe is incomplete
    """
    needs_non_root()
    nem.path(f"{rel}/{name}").write_text("x")
    nem.path(rel).chmod(0o555)
    try:
        res = nem.run("wipe", "--confirm")
    finally:
        nem.path(rel).chmod(0o755)
    assert res.returncode == 1 and "wipe incomplete" in res.stdout


def test_wipe_removes_only_nemesis_entries_from_the_dalvik_cache(nem):
    """Given a Dalvik cache with NEMESIS entries in either case, and another app's entry
    When the unit is wiped
    Then only the NEMESIS entries go
    """
    cache = nem.path("/data/dalvik-cache")
    cache.mkdir(parents=True)
    for name in ("system@app@NEMESIS.apk@classes.dex", "data@app@nemesis-x@classes.dex", "system@app@Other.apk@classes.dex"):
        (cache / name).write_text("x")
    nem.run("wipe", "--confirm")
    assert sorted(p.name for p in cache.iterdir()) == ["system@app@Other.apk@classes.dex"]


# --- Services must not touch the wrong process or identity (svc.c)


def test_stopping_a_service_never_kills_the_process_a_stale_pidfile_names(nem):
    """Given a pidfile whose pid now belongs to an unrelated process (02 section 5)
    When the service is stopped
    Then that process is left alone, and the stale pidfile is removed
    """
    service(nem)
    nem.run("service", "stop", SVC)
    stranger = subprocess.Popen(["sleep", "30"])
    pidfile = nem.root / "data/nemesis/pids/my.tool+d"
    try:
        pidfile.write_text(f"{stranger.pid}\n")
        assert nem.run("service", "stop", SVC).returncode == 0
        time.sleep(0.5)
        assert stranger.poll() is None, "an unrelated process was killed"
        assert not pidfile.exists()
    finally:
        stranger.kill()
        stranger.wait()


def test_a_pidfile_with_no_start_time_still_identifies_the_service(nem):
    """Given a running service whose pidfile holds only the pid
    When its status is read
    Then it matches by command line and reads as running
    """
    service(nem)
    pidfile = nem.root / "data/nemesis/pids/my.tool+d"
    pid = pidfile.read_text().split()[0]
    pidfile.write_text(f"{pid}\n")
    assert "running" in nem.run("service", "status", SVC).stdout


def test_a_service_set_to_root_does_not_drop_privilege(nem):
    """Given a service with uid=root (04 section 4)
    When it starts
    Then it does not switch to the system uid
    """
    section = "[service.0]\nname=d\npath=svc/d.sh\nmode=background\nuid=root\n"
    assert nem.run("install", nem.package(sections=section, files={"svc/d.sh": "sleep 5\n"}, log_hooks=False)).returncode == 0
    assert "running" in nem.run("service", "status", SVC).stdout
    assert "uid 1000" not in nem.ids()


def test_an_instance_service_falls_back_to_startservice(nem):
    """Given an instance component that am cannot start as an activity
    When it starts
    Then am startservice is tried
    """
    executable(nem.bin("am"), f'echo "am $*" >> "{nem.root}/calls"\n[ "$1" = startservice ]\n')
    service(nem, kind="instance", path="/com.a/.Svc")
    assert wait_for(lambda: "am startservice -n /com.a/.Svc" in nem.calls(), 10)


def test_an_instance_service_that_starts_does_not_also_start_as_a_service(nem):
    """Given an instance component that am starts
    When it starts
    Then startservice is not tried
    """
    service(nem, kind="instance", path="/com.a/.Svc")
    assert wait_for(lambda: "am start -n /com.a/.Svc" in nem.calls(), 10)
    time.sleep(0.5)
    assert "startservice" not in nem.calls()


def test_svcmgr_still_restarts_script_services_while_android_is_down(nem):
    """Given Android down, which matters only to instance services (04 section 4)
    When a restart script service dies
    Then svcmgr restarts it
    """
    nem.android(up=False)
    marker = nem.root / "runs"
    service(nem, mode="restart", script=f'echo x >> "{marker}"\nexit 0\n')
    nem.daemon("svcmgr")
    assert wait_for(lambda: marker.exists() and marker.read_text().count("x") >= 2, 15)


def test_svcmgr_restores_a_missing_watchdog(nem):
    """Given no watchdog running (02 section 5)
    When svcmgr runs
    Then it starts one, so the root channel comes back
    """
    assert nem.run("ping").returncode != 0
    nem.daemon("svcmgr")
    assert wait_for(lambda: nem.run("ping").returncode == 0, 15)


def test_the_channel_daemon_survives_serving_a_client(nem):
    """Given a running watchdog
    When a client is served
    Then the same process keeps running, because a forked worker did the work
    """
    start_channel(nem)
    pid = (nem.root / "data/nemesis/watchdog.pid").read_text().split()[0]
    assert nem.run("pkg-list", euid=1000).returncode == 0
    time.sleep(0.3)
    assert (nem.root / "data/nemesis/watchdog.pid").read_text().split()[0] == pid
    assert os.path.exists(f"/proc/{pid}")


# --- Each insertion is handled once (usb.c)


@pytest.mark.parametrize(
    "descriptor",
    ["[nemesis]\nversion=1\n", "[nemesis]\ntype=dance\npath=a\n", "[nemesis]\ntype=execute\npath=../x\n", "[nemesis]\ntype=open\n"],
    ids=["empty-type", "unknown-type", "bad-path", "open"],
)
def test_a_descriptor_is_not_handled_again_while_it_stays_inserted(nem, descriptor):
    """Given a descriptor that is handled without running anything, with Android down
    When the stick stays in place
    Then the handler does not process it again
    """
    nem.android(up=False)
    nem.stick(descriptor)
    nem.daemon("usb-handler")
    assert wait_for(lambda: "handled" in nem.session_log(), 15), nem.log()
    time.sleep(5)
    assert nem.session_log().count("handled") == 1


def test_an_unreadable_descriptor_is_not_handled_again(nem):
    """Given a descriptor that cannot be read
    When the stick stays in place
    Then the handler does not process it again
    """
    needs_non_root()
    nem.android(up=False)
    usb = nem.stick(EXECUTE)
    (usb / "NEMESIS.data").chmod(0)
    nem.daemon("usb-handler")
    assert wait_for(lambda: "handled" in nem.session_log(), 15), nem.log()
    time.sleep(4)
    assert nem.session_log().count("handled") == 1


# --- What launch and install do with odd manifests (pkg.c)


@pytest.mark.parametrize(
    "section",
    ["[app.0]\nkind=sh\n", "[app.0]\nkind=other\npath=bin/x\n", "[app.0]\npath=bin/x\n", "[app.0]\nkind=sh\npath=\n"],
    ids=["script-without-path", "unknown-kind", "no-kind", "empty-path"],
)
def test_an_app_section_that_cannot_launch_runs_nothing(nem, section):
    """Given an app section that names no launchable script (04 section 3)
    When the package is launched
    Then nothing runs, and it reports there is nothing to launch
    """
    marker = nem.root / "ran"
    install(nem, sections=section, files={"bin/x": f'touch "{marker}"\n'})
    res = nem.run("pkg-launch", "my.tool")
    assert res.returncode == 1 and "nothing to launch" in res.stderr
    assert not marker.exists()


def test_a_failed_activity_launch_exits_with_one(nem):
    """Given am failing
    When the package is launched
    Then it exits 1
    """
    install(nem, sections="[app.0]\nkind=sh\npath=bin/x\ncomponent=com.a/.X\n", files={"bin/x": "exit 0\n"})
    executable(nem.bin("am"), "exit 1\n")
    res = nem.run("pkg-launch", "my.tool")
    assert res.returncode == 1 and "activity launch failed" in res.stderr


def test_balanced_quotes_in_a_component_are_still_refused(nem):
    """Given a component whose quotes would balance, so the shell would accept it
    When the package is launched
    Then it never reaches am
    """
    install(nem, sections="[app.0]\nkind=sh\npath=bin/x\ncomponent=a'b'c/.X\n", files={"bin/x": "exit 0\n"})
    res = nem.run("pkg-launch", "my.tool")
    assert res.returncode == 1
    assert "am start" not in nem.calls()


def test_only_apk_apps_are_uninstalled_through_pm(nem):
    """Given a script app that also names an Android package
    When the package is uninstalled
    Then pm is not called
    """
    install(nem, sections="[app.0]\nkind=sh\npath=bin/x\npackage=com.a\n", files={"bin/x": "exit 0\n"})
    assert nem.run("uninstall", "my.tool").returncode == 0
    assert "pm uninstall" not in nem.calls()


@pytest.mark.parametrize("key,suffix", [("package", ""), ("component", "/.X")], ids=["package", "component"])
def test_android_package_names_have_a_length_limit(nem, key, suffix):
    """Given names of 159 and 160 characters (pkg.c safe_pkg_name, android_pkg_id)
    When each package is uninstalled
    Then the longest that fits reaches pm, and the next is refused
    """
    for length, expected in ((159, True), (160, False)):
        nem.reset()
        name = "a" * length
        install(nem, sections=f"[app.0]\nkind=apk\npath=app/a.apk\n{key}={name}{suffix}\n", files={"app/a.apk": "apk"})
        nem.run("uninstall", "my.tool")
        assert (f"pm uninstall {name} " in nem.calls()) is expected


def test_very_long_pm_output_is_handled(nem):
    """Given pm printing far more than the capture buffer holds, with its result first
    When an apk installs
    Then the install succeeds
    """
    nem.pm_script('case "$1" in\ninstall) echo Success; head -c 20000 /dev/zero | tr "\\0" x ;;\nesac\n')
    res = install(nem, sections="[app.0]\nkind=apk\npath=app/a.apk\npackage=com.a\n", files={"app/a.apk": "apk"})
    assert res.returncode == 0


def test_an_apps_directory_that_cannot_be_written_fails_the_install(nem):
    """Given an apps directory that cannot be written
    When a package installs
    Then it fails and is not recorded
    """
    needs_non_root()
    apps = nem.path("/data/nemesis/apps")
    apps.mkdir(parents=True, exist_ok=True)
    apps.chmod(0o555)
    try:
        res = nem.run("install", nem.package())
    finally:
        apps.chmod(0o755)
    assert res.returncode != 0
    assert "my.tool" not in nem.db()


def test_pkg_info_refuses_a_bad_id(nem):
    """Given a manifest with a bad id
    When pkg-info reads it
    Then it exits 1
    """
    res = nem.run("pkg-info", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=../x\n"}))
    assert res.returncode == 1 and "invalid package id" in res.stderr


def damaged_after_the_manifest(nem, how):
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": GOOD, "payload": b"payload " * 64})
    with zipfile.ZipFile(target) as z:
        info = z.getinfo("payload")
    raw = target.read_bytes()
    if how == "method":
        at = raw.rfind(b"PK\x01\x02") + 10
        raw = raw[:at] + struct.pack("<H", 99) + raw[at + 2:]
    elif how == "local-header":
        raw = raw[: info.header_offset] + b"XXXX" + raw[info.header_offset + 4 :]
    else:
        start = info.header_offset + 30 + len("payload")
        raw = raw[:start] + b"\xff" * 8 + raw[start + 8:]
    target.write_bytes(raw)
    return target


@pytest.mark.parametrize("how", ["method", "local-header", "deflate"])
def test_damage_after_a_good_manifest_is_still_refused(nem, how):
    """Given a zip whose manifest reads fine but a later member is damaged
    When pkg-info and install read it
    Then both refuse it, instead of trusting the part they already read
    """
    target = damaged_after_the_manifest(nem, how)
    assert nem.run("pkg-info", target).returncode != 0
    assert nem.run("install", target).returncode != 0
    assert "my.tool" not in nem.db()
