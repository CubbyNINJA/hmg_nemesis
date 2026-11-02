"""Services, USB handler, root channel and CLI against docs 02, 04 section 4 and 05."""
from __future__ import annotations

import os
import time

import pytest

from helpers import known_bug
from native_unit import executable, make_zip, wait_for

SVC = "my.tool/d"


def install_service(nem, script="sleep 60\n", mode="background", kind="sh", uid="", path="svc/d.sh"):
    uid_line = f"uid={uid}\n" if uid else ""
    sections = f"[service.0]\nname=d\nkind={kind}\npath={path}\nmode={mode}\n{uid_line}"
    return nem.run("install", nem.package(sections=sections, files={"svc/d.sh": script}, log_hooks=False))


def status(nem, name=SVC):
    return nem.run("service", "status", name).stdout.strip()


# --- services


def test_service_registers_under_package_and_local_name(nem):
    install_service(nem)
    line = [l for l in nem.db("services.db").splitlines() if l.startswith(SVC + "|")][0]
    assert line == f"{SVC}|sh|{nem.app()}/svc/d.sh|root|background"


def test_two_packages_can_use_the_same_local_name(nem):
    sections = "[service.0]\nname=d\nkind=sh\npath=d.sh\nmode=background\n"
    for id_ in ("one.tool", "two.tool"):
        nem.run("install", nem.package(id_, sections=sections, files={"d.sh": "sleep 60\n"}, log_hooks=False))
    db = nem.db("services.db")
    assert "one.tool/d|" in db and "two.tool/d|" in db


def test_service_defaults(nem):
    sections = "[service.0]\nname=d\npath=d.sh\n"
    nem.run("install", nem.package(sections=sections, files={"d.sh": "sleep 60\n"}, log_hooks=False))
    assert nem.db("services.db").strip().endswith("|sh|" + f"{nem.app()}/d.sh|root|background")


def test_service_without_a_name_is_not_registered(nem):
    sections = "[service.0]\nkind=sh\npath=d.sh\n"
    nem.run("install", nem.package(sections=sections, files={"d.sh": "sleep 60\n"}, log_hooks=False))
    assert "my.tool" not in nem.db("services.db")


def test_install_starts_services_and_status_reports_running(nem):
    install_service(nem)
    name, mode, state, pid = status(nem).split("|")
    assert (name, mode, state) == (SVC, "background", "running")
    assert os.path.exists(f"/proc/{pid}")


def test_stop_ends_the_process_and_status_reports_stopped(nem):
    install_service(nem)
    pid = status(nem).split("|")[3]
    assert nem.run("service", "stop", SVC).returncode == 0
    assert wait_for(lambda: not os.path.exists(f"/proc/{pid}") or "Z" in open(f"/proc/{pid}/stat").read().split(")")[1][:3], 5)
    assert status(nem) == f"{SVC}|background|stopped|0"


def test_start_is_idempotent(nem):
    install_service(nem)
    pid = status(nem).split("|")[3]
    assert nem.run("service", "start", SVC).returncode == 0
    assert status(nem).split("|")[3] == pid


def test_service_output_goes_to_its_log_and_can_be_read_back(nem):
    install_service(nem, script='echo hello-out\necho hello-err >&2\nsleep 60\n')
    target = nem.root / "data/nemesis/logs/my.tool/d.log"
    assert wait_for(lambda: target.exists() and "hello-err" in target.read_text(), 5)
    res = nem.run("log", "service", SVC)
    assert "hello-out" in res.stdout and "hello-err" in res.stdout


def test_once_service_waits_and_returns_the_exit_code(nem):
    install_service(nem, script="exit 6\n", mode="once")
    assert nem.run("service", "start", SVC).returncode == 6


def test_system_uid_service_drops_privilege(nem):
    install_service(nem, uid="system")
    assert wait_for(lambda: "uid 1000" in nem.ids(), 5)
    assert "gid 1000" in nem.ids()


@known_bug("G21", "the package root is prepended to an instance component, so am gets a file path")
def test_instance_service_uses_am_when_android_is_up(nem):
    install_service(nem, kind="instance", path="com.a/.Svc")
    assert wait_for(lambda: "am start -n com.a/.Svc" in nem.calls(), 5)


def test_instance_service_is_deferred_while_android_is_down(nem):
    nem.android(up=False)
    install_service(nem, kind="instance", path="com.a/.Svc")
    time.sleep(0.3)
    assert "com.a/.Svc" not in nem.calls()
    assert "defer instance" in nem.log()


def test_unknown_service(nem):
    for op in ("start", "status"):
        res = nem.run("service", op, "no/such")
        assert res.returncode == 1 and "unknown service" in res.stderr


def test_service_usage_errors(nem):
    assert "usage" in nem.run("service").stderr
    assert "usage" in nem.run("service", "start").stderr
    assert "unknown service op" in nem.run("service", "dance", "x").stderr


def test_service_list_format_includes_the_locked_handler(nem):
    install_service(nem)
    lines = nem.run("service", "list").stdout.splitlines()
    assert any(l.startswith("usb-handler/usb-handler|restart|") for l in lines)
    assert any(l.startswith(f"{SVC}|background|running|") for l in lines)


def test_stale_pidfile_for_another_process_does_not_count_as_running(nem):
    # 02 section 5: a reused pid cannot block the next start.
    install_service(nem)
    nem.run("service", "stop", SVC)
    (nem.root / "data/nemesis/pids/my.tool+d").write_text(f"{os.getpid()} 1\n")
    assert status(nem).split("|")[2] == "stopped"


def test_svcmgr_gives_up_after_five_rapid_deaths(nem):
    # 04 section 4: ordinary packages stop after 5 rapid deaths.
    install_service(nem, script=f'echo x >> "{nem.root}/deaths"\nexit 1\n', mode="restart")
    nem.daemon("svcmgr")
    assert wait_for(lambda: f"{SVC}|restart|failed" in status(nem), 30)
    deaths = (nem.root / "deaths").read_text().count("x")
    time.sleep(3)
    assert (nem.root / "deaths").read_text().count("x") == deaths


def test_svcmgr_restarts_a_service_that_died(nem):
    install_service(nem, mode="restart")
    nem.daemon("svcmgr")

    def pid_other_than(*seen):
        # Read once per check. Each status call reads the service list again, and it can change.
        fields = status(nem).split("|")
        return fields[3] if len(fields) > 3 and fields[3] not in seen else None

    seen = []
    assert wait_for(lambda: seen.append(pid_other_than("0")) or seen[-1], 10)
    pid = seen[-1]
    os.kill(int(pid), 9)
    assert wait_for(lambda: pid_other_than("0", pid), 10)


def test_svcmgr_does_not_restart_a_background_service(nem):
    install_service(nem)
    nem.daemon("svcmgr")
    time.sleep(0.5)
    pid = status(nem).split("|")[3]
    os.kill(int(pid), 9)
    time.sleep(3)
    assert status(nem).split("|")[2] == "stopped"


def test_svcmgr_always_restarts_the_locked_handler(nem):
    # The seeded script execs the unit's literal /system/xbin/nemesis, so it dies at once here.
    nem.daemon("svcmgr")
    assert wait_for(lambda: nem.log().count("restart usb-handler/usb-handler (locked)") >= 6, 30)
    assert "usb-handler/usb-handler|restart|failed" not in nem.run("service", "list").stdout


def test_watchdog_starts_svcmgr_and_answers_ping(nem):
    assert nem.run("watchdog").returncode == 0
    assert wait_for((nem.root / "data/nemesis/svcmgr.pid").exists, 10)
    assert nem.run("ping").returncode == 0


def test_second_watchdog_does_not_replace_the_first(nem):
    nem.run("watchdog")
    pidfile = nem.root / "data/nemesis/watchdog.pid"
    assert wait_for(pidfile.exists, 10)
    first = pidfile.read_text()
    assert nem.run("watchdog").returncode == 0
    assert pidfile.read_text() == first
    assert "already running" in nem.log()


def test_reinstall_clears_the_failed_state(nem):
    install_service(nem, script="exit 1\n", mode="restart")
    (nem.root / "data/nemesis/pids/my.tool+d.fails").write_text("5\n")
    nem.run("uninstall", "my.tool")
    install_service(nem, script="sleep 60\n", mode="restart")
    assert nem.run("service", "start", SVC).returncode == 0
    assert status(nem).split("|")[2] == "running"


@known_bug("G7", "svcmgr restarts a stopped restart-mode service")
def test_a_stopped_restart_service_stays_stopped(nem):
    install_service(nem, mode="restart")
    nem.daemon("svcmgr")
    assert wait_for(lambda: "running" in status(nem), 5)
    nem.run("service", "stop", SVC)
    time.sleep(4)
    assert status(nem).split("|")[2] == "stopped"


@known_bug("G11", "a deferred background instance service never starts after Android comes up")
def test_deferred_instance_service_starts_when_android_comes_up(nem):
    nem.android(up=False)
    install_service(nem, kind="instance", path="com.a/.Svc")
    nem.daemon("svcmgr")
    time.sleep(1)
    nem.android(up=True)
    assert wait_for(lambda: "com.a/.Svc" in nem.calls(), 8)


@known_bug("G12", "svcmgr can restart a service between stop and deregister")
def test_uninstall_leaves_no_orphan_service(nem):
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=restart\n"
    files = {"svc/d.sh": f'echo $$ >> "{nem.root}/pids"\nsleep 60\n', "scripts/preun.sh": "sleep 4\n"}
    nem.run("install", nem.package(sections=sections, files=files))
    nem.daemon("svcmgr")
    assert wait_for(lambda: "running" in status(nem), 5)
    nem.run("uninstall", "my.tool")
    alive = [p for p in (nem.root / "pids").read_text().split() if os.path.exists(f"/proc/{p}")]
    assert not alive


# --- USB handler


EXECUTE = "[nemesis]\nversion=1.0\ntype=execute\npath=NEMESIS.sh\n"


def run_handler(nem):
    return nem.daemon("usb-handler")


def handled(nem, text="handled", timeout=15):
    assert wait_for(lambda: text in nem.session_log(), timeout), nem.log()


def marker_script(nem):
    return f'pwd > "{nem.root}/ran"\nexit 0\n'


def test_execute_with_android_up_runs_after_yes_with_cwd_at_usb_root(nem):
    nem.answer_dialogs("yes")
    usb = nem.stick(EXECUTE, {"NEMESIS.sh": marker_script(nem)})
    run_handler(nem)
    handled(nem, "handled type=execute path=NEMESIS.sh reply=yes")
    assert (nem.root / "ran").read_text().strip() == str(usb)
    assert "nemesis_mode confirm_execute" in nem.calls()
    assert "nemesis_mode result_ok" in nem.calls()


def test_execute_with_android_up_does_nothing_after_no(nem):
    nem.answer_dialogs("no")
    nem.stick(EXECUTE, {"NEMESIS.sh": marker_script(nem)})
    run_handler(nem)
    handled(nem, "reply=no")
    assert not (nem.root / "ran").exists()


def test_execute_with_android_down_runs_without_a_dialog(nem):
    nem.android(up=False)
    nem.stick(EXECUTE, {"NEMESIS.sh": marker_script(nem)})
    run_handler(nem)
    handled(nem, "reply=recovery")
    assert (nem.root / "ran").exists()
    assert "am " not in nem.calls()
    assert "android down; auto-confirm" in nem.log()


def test_failed_script_reports_a_failure_dialog(nem):
    nem.answer_dialogs("yes")
    nem.stick(EXECUTE, {"NEMESIS.sh": "exit 3\n"})
    run_handler(nem)
    assert wait_for(lambda: "nemesis_mode result_fail" in nem.calls())


def test_bare_script_without_a_descriptor_is_ignored(nem):
    usb = nem.root / "storage/usb0"
    (usb / "NEMESIS.sh").write_text(marker_script(nem))
    (nem.root / "proc_mounts").write_text("/dev/x /storage/usb0 vfat rw 0 0\n")
    run_handler(nem)
    time.sleep(2)
    assert not (nem.root / "ran").exists()
    assert not nem.session_log()


@pytest.mark.parametrize("mount", ["storage/usb1", "storage/usb2", "mnt/media_rw/usb0", "mnt/media_rw/usb1", "mnt/media_rw/usb2"])
def test_every_documented_mount_is_watched(nem, mount):
    nem.android(up=False)
    usb = nem.stick(EXECUTE, {"NEMESIS.sh": marker_script(nem)}, mount=mount)
    run_handler(nem)
    handled(nem, "reply=recovery")
    assert (nem.root / "ran").read_text().strip() == str(usb)


def test_install_with_android_up_asks_then_installs(nem):
    nem.answer_dialogs("yes")
    zip_bytes = nem.package(log_hooks=False).read_bytes()
    nem.stick("[nemesis]\ntype=install\npath=packages/p.zip\n", {"packages/p.zip": zip_bytes})
    run_handler(nem)
    handled(nem, "handled type=install path=packages/p.zip reply=yes")
    assert "nemesis_mode confirm_install" in nem.calls()
    assert "my.tool|" in nem.db()
    assert "nemesis_mode result_ok" in nem.calls()


def test_install_with_android_down_installs_without_a_dialog(nem):
    nem.android(up=False)
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": nem.package(log_hooks=False).read_bytes()})
    run_handler(nem)
    handled(nem, "reply=recovery")
    assert "my.tool|" in nem.db()


@pytest.mark.parametrize("answer,version", [("yes", "2"), ("no", "1")])
def test_d2_installed_package_with_android_up_asks_before_replacing(nem, answer, version):
    """Given an installed package and a newer copy on a stick, with Android up (04 section 2)
    When the user answers the dialog
    Then yes replaces it and no leaves it alone
    """
    nem.run("install", nem.package(log_hooks=False, files={"v": "1"}))
    nem.answer_dialogs(answer)
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": nem.package(log_hooks=False, files={"v": "2"}, name="u.zip").read_bytes()})
    run_handler(nem)
    handled(nem)
    assert "nemesis_mode confirm_reinstall" in nem.calls()
    assert (nem.app() / "v").read_text() == version


def test_a_stick_without_the_emergency_file_waits_for_android(nem):
    """Given Android down and a stick with no NEMESIS.emergency (05 section 2)
    When the handler sees it, and then Android comes up
    Then nothing runs until Android is up, and then the user is asked
    """
    nem.android(up=False)
    nem.answer_dialogs("yes")
    nem.stick(EXECUTE, {"NEMESIS.sh": marker_script(nem)}, emergency=False)
    run_handler(nem)
    assert wait_for(lambda: "waiting for Android" in nem.log(), 10)
    time.sleep(2)
    assert not (nem.root / "ran").exists()
    assert "am " not in nem.calls()
    nem.android(up=True)
    assert wait_for(lambda: (nem.root / "ran").exists(), 15)
    assert "nemesis_mode confirm_execute" in nem.calls()


def test_d2_installed_package_with_android_down_updates_automatically(nem):
    nem.run("install", nem.package(log_hooks=False, files={"v": "1"}))
    nem.android(up=False)
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": nem.package(log_hooks=False, files={"v": "2"}).read_bytes()})
    run_handler(nem)
    handled(nem, "reply=recovery")
    assert (nem.app() / "v").read_text() == "2"


def test_locked_package_on_a_stick_is_forbidden(nem):
    nem.run("pkg-list")
    nem.answer_dialogs("ok")
    z = make_zip(nem.workdir / "l.zip", {"NEMESIS.data": "[nemesis]\nid=usb-handler\n"})
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": z.read_bytes()})
    run_handler(nem)
    handled(nem)
    assert "nemesis_mode forbidden" in nem.calls()


def test_open_starts_manager_with_android_up(nem):
    nem.stick("[nemesis]\ntype=open\n")
    run_handler(nem)
    handled(nem, "reply=opened")
    assert "am start -n com.nemesis.manager/.MainActivity" in nem.calls()


def test_open_is_skipped_with_android_down(nem):
    nem.android(up=False)
    nem.stick("[nemesis]\ntype=open\n")
    run_handler(nem)
    handled(nem, "reply=skip-ui")
    assert "am " not in nem.calls()


@pytest.mark.parametrize(
    "bad",
    ["", "/abs.sh", "\\abs.sh", "a/../b.sh", "a;b", "a|b", "a&b", "a`b", "a$b", "a'b", 'a"b', "a(b", "a)b", "a<b", "a>b"],
)
def test_invalid_paths_are_rejected_and_never_run(nem, bad):
    # 05 section 2, path rules.
    nem.answer_dialogs("yes")
    nem.stick(f"[nemesis]\ntype=execute\npath={bad}\n", {"NEMESIS.sh": marker_script(nem)})
    run_handler(nem)
    assert wait_for(lambda: "nemesis_mode invalid" in nem.calls())
    handled(nem)
    assert not (nem.root / "ran").exists()


def test_unknown_type_is_invalid(nem):
    nem.answer_dialogs("ok")
    nem.stick("[nemesis]\ntype=dance\npath=a\n")
    run_handler(nem)
    assert wait_for(lambda: "nemesis_mode invalid" in nem.calls())
    handled(nem, "handled type=dance path=a reply=ok")


def test_empty_type_waits_then_is_invalid(nem):
    # 05 section 2: an incomplete copy gets about 3 seconds.
    nem.answer_dialogs("ok")
    nem.stick("[nemesis]\nversion=1\n")
    run_handler(nem)
    time.sleep(1)
    assert "nemesis_mode invalid" not in nem.calls()
    assert wait_for(lambda: "nemesis_mode invalid" in nem.calls(), 6)
    handled(nem)


def test_type_arriving_during_the_wait_is_used(nem):
    nem.android(up=False)
    usb = nem.stick("[nemesis]\nversion=1\n", {"NEMESIS.sh": marker_script(nem)})
    run_handler(nem)
    time.sleep(0.5)
    (usb / "NEMESIS.data").write_text(EXECUTE)
    handled(nem, "reply=recovery")
    assert (nem.root / "ran").exists()


def test_descriptor_is_handled_once_until_rearmed(nem):
    nem.android(up=False)
    nem.stick(EXECUTE, {"NEMESIS.sh": f'echo x >> "{nem.root}/runs"\n'})
    run_handler(nem)
    handled(nem)
    time.sleep(3)
    assert (nem.root / "runs").read_text().count("x") == 1


def test_unplug_rearms(nem):
    nem.android(up=False)
    nem.stick(EXECUTE, {"NEMESIS.sh": f'echo x >> "{nem.root}/runs"\n'})
    run_handler(nem)
    handled(nem)
    nem.unplug()
    assert wait_for(lambda: "rearm (unmounted)" in nem.log())
    (nem.root / "proc_mounts").write_text("/dev/x /storage/usb0 vfat rw 0 0\n")
    assert wait_for(lambda: (nem.root / "runs").read_text().count("x") == 2)


def test_changed_descriptor_rearms(nem):
    nem.android(up=False)
    usb = nem.stick(EXECUTE, {"NEMESIS.sh": f'echo x >> "{nem.root}/runs"\n'})
    run_handler(nem)
    handled(nem)
    (usb / "NEMESIS.data").write_text(EXECUTE + "# changed\n")
    assert wait_for(lambda: (nem.root / "runs").read_text().count("x") == 2)
    assert "rearm (descriptor changed)" in nem.log()


def test_removed_descriptor_rearms(nem):
    nem.android(up=False)
    usb = nem.stick(EXECUTE, {"NEMESIS.sh": f'echo x >> "{nem.root}/runs"\n'})
    run_handler(nem)
    handled(nem)
    (usb / "NEMESIS.data").unlink()
    assert wait_for(lambda: "rearm (descriptor gone)" in nem.log())


def test_unplugging_cancels_the_dialog_wait(nem):
    # am records the dialog but nobody answers.
    nem.stick(EXECUTE, {"NEMESIS.sh": marker_script(nem)})
    run_handler(nem)
    assert wait_for(lambda: "confirm_execute" in nem.calls())
    nem.unplug()
    handled(nem, "reply=unmount")
    assert not (nem.root / "ran").exists()


def test_d5_unplugging_does_not_end_the_wait_for_a_running_script(nem):
    nem.android(up=False)
    done = nem.root / "done"
    nem.stick(EXECUTE, {"NEMESIS.sh": f'sleep 3\ntouch "{done}"\n'})
    run_handler(nem)
    time.sleep(1)
    nem.unplug()
    time.sleep(0.5)
    assert not nem.session_log()
    handled(nem, "reply=recovery")
    assert done.exists()


def test_dialog_extras_are_sanitized(nem):
    nem.answer_dialogs("no")
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": make_zip(nem.workdir / "x.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\nname=a;b$c\n"}).read_bytes()})
    run_handler(nem)
    handled(nem)
    assert "a_b_c" in nem.calls() and "a;b$c" not in nem.calls()


# --- root and remount


def test_root_runs_one_command_with_the_privileged_path(nem):
    res = nem.run("root", "-c", 'echo "$PATH"')
    assert res.returncode == 0
    assert res.stdout.startswith(f"{nem.root}/data/nemesis/bin:")


def test_root_returns_the_command_status(nem):
    assert nem.run("root", "-c", "exit 9").returncode == 9


@pytest.mark.parametrize("args,uid", [(["1000", "-c", "true"], "1000"), (["--system", "-c", "true"], "1000"), (["-c", "true"], "0")])
def test_root_uid_selection(nem, args, uid):
    nem.run("root", *args)
    assert f"uid {uid}" in nem.ids()


def test_root_without_a_command_opens_a_shell(nem):
    res = nem.run("root", stdin="echo in-shell\nexit 4\n")
    assert res.returncode == 4 and "in-shell" in res.stdout


@pytest.mark.parametrize("command", ["mount -o remount,rw /system", "mount -o rw,remount /", "/system/bin/mount -o remount,ro /system"])
def test_root_intercepts_a_plain_system_remount(nem, command):
    # 02 section 4. Toolbox mount fails on the unit, so this must not reach it.
    (nem.root / "proc_mounts").write_text("/dev/block/sys /system ext4 ro,nosuid,nodev 0 0\n")
    # A read-only /system, or the rw probe succeeds and no mount call is needed.
    nem.path("/system/xbin").chmod(0o555)
    try:
        nem.run("root", "-c", command)
    finally:
        nem.path("/system/xbin").chmod(0o755)
    if os.geteuid() == 0:
        pytest.skip("root ignores the directory mode, so the rw probe succeeds first")
    assert (nem.root / "mounts.log").exists()


@known_bug("G8", "the remount intercept swallows the rest of a compound command")
def test_root_runs_every_part_of_a_compound_command(nem):
    marker = nem.root / "second"
    nem.run("root", "-c", f'mount -o remount,rw /system && touch "{marker}"')
    assert marker.exists()


@known_bug("G8", "any command mentioning mount, remount and /system is intercepted")
def test_root_does_not_intercept_a_command_that_only_mentions_remount(nem):
    nem.path("/system/etc/x").write_text("remount mount\n")
    res = nem.run("root", "-c", f"grep remount {nem.root}/system/etc/x /system/none mount")
    assert "remount mount" in res.stdout


@known_bug("G8", "ro/rw is found by substring, so a path containing rw reads as rw")
def test_read_only_remount_is_not_misread_from_the_path(nem):
    (nem.root / "proc_mounts").write_text("/dev/block/sys /system ext4 rw,nosuid 0 0\n")
    nem.run("root", "-c", "mount -o remount,ro /system/overwrite")
    flags = int((nem.root / "mounts.log").read_text().split()[-1])
    assert flags & 1


def test_remount_keeps_nosuid_and_nodev(nem):
    # 02 section 4: dropping nosuid is what makes the kernel refuse.
    (nem.root / "proc_mounts").write_text("/dev/block/sys /system ext4 ro,nosuid,nodev,noatime 0 0\n")
    nem.path("/system/xbin").chmod(0o555)
    try:
        nem.run("remount", "rw")
    finally:
        nem.path("/system/xbin").chmod(0o755)
    if os.geteuid() == 0:
        pytest.skip("root ignores the directory mode, so the rw probe succeeds first")
    flags = int((nem.root / "mounts.log").read_text().splitlines()[0].split()[-1])
    assert flags & 2 and flags & 4 and not flags & 1


def test_remount_ro_reports_success_and_sets_the_read_only_flag(nem):
    (nem.root / "proc_mounts").write_text("/dev/block/sys /system ext4 rw,nosuid 0 0\n")
    res = nem.run("remount", "ro")
    assert res.returncode == 0 and "remount /system ro ok" in res.stdout
    assert int((nem.root / "mounts.log").read_text().split()[-1]) & 1


def test_remount_falls_back_to_the_root_mount(nem):
    (nem.root / "proc_mounts").write_text("/dev/root / ext4 rw 0 0\n")
    assert nem.run("remount", "ro").returncode == 0
    assert (nem.root / "mounts.log").read_text().split()[1] == "/"


def test_remount_rejects_other_arguments(nem):
    assert "usage: nemesis remount" in nem.run("remount", "maybe").stderr


def test_wrapper_is_installed_with_the_privileged_directories(nem):
    nem.run("pkg-list")
    wrapper = nem.root / "data/nemesis/bin/mount"
    assert wrapper.exists() and os.access(str(wrapper), os.X_OK)


# --- logs


def test_log_dump_without_a_log(nem):
    log = nem.root / "data/nemesis/nemesis.log"
    nem.run("pkg-list")
    if log.exists():
        log.unlink()
    res = nem.run("log", "dump")
    assert res.returncode == 0
    assert "(no log yet)" in res.stdout


def test_log_dump_truncates_to_the_tail(nem):
    log = nem.root / "data/nemesis/nemesis.log"
    log.write_bytes(b"old\n" * 40000 + b"newest-line\n")
    res = nem.run("log", "dump")
    assert "(truncated)" in res.stdout and "newest-line" in res.stdout
    assert len(res.stdout) < 140 * 1024


def test_log_usage_and_unknown_source(nem):
    assert "usage: nemesis log" in nem.run("log").stderr
    assert "unknown log source" in nem.run("log", "dance").stderr
    assert "usage: nemesis log service" in nem.run("log", "service").stderr


def test_log_logcat_and_dmesg_reach_the_system_tools(nem):
    executable(nem.bin("logcat"), 'echo "logcat $*"\n')
    executable(nem.bin("dmesg"), 'echo "dmesg $*"\n')
    assert "logcat -d" in nem.run("log", "logcat", "-d").stdout
    assert "dmesg -c" in nem.run("log", "dmesg", "-c").stdout


# --- CLI and the command channel


def test_no_arguments_and_unknown_commands_print_usage(nem):
    assert nem.run().returncode == 1
    res = nem.run("dance")
    assert res.returncode == 1 and "nemesis install [--update]" in res.stderr


def test_ping_fails_without_a_watchdog(nem):
    assert nem.run("ping").returncode != 0


def test_daemons_refuse_to_start_without_root_or_a_channel(nem):
    for op in ("svcmgr", "usb-handler"):
        res = nem.run(op, euid=1000)
        assert res.returncode == 1 and "daemon not running" in res.stderr


def start_channel(nem):
    nem.run("watchdog")
    assert wait_for(lambda: nem.run("ping").returncode == 0, 10)


def test_any_uid_can_run_commands_through_the_channel(nem):
    # 02 section 1: the Manager and other UIDs reach root through the sockets.
    start_channel(nem)
    res = nem.run("pkg-list", euid=1000)
    assert res.returncode == 0 and "usb-handler|USB Handler|1|service" in res.stdout


def test_channel_carries_the_exit_code_and_output(nem):
    start_channel(nem)
    res = nem.run("root", "-c", "echo through; exit 7", euid=1000)
    assert res.returncode == 7 and "through" in res.stdout


def test_channel_refuses_daemon_operations(nem):
    start_channel(nem)
    import socket
    import struct

    for sock_rel in ("dev/nemesis.sock", "data/nemesis/root.sock"):
        client = socket.socket(socket.AF_UNIX)
        client.connect(str(nem.root / sock_rel))
        payload = b"NEM\x01" + struct.pack("<II", 0, 1) + struct.pack("<I", 6) + b"svcmgr"
        client.sendall(payload)
        data = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
        client.close()
        assert struct.unpack_from("<I", data)[0] == 1
        assert b"not allowed via daemon" in data


def test_channel_ignores_a_client_with_the_wrong_magic(nem):
    start_channel(nem)
    import socket

    client = socket.socket(socket.AF_UNIX)
    client.connect(str(nem.root / "dev/nemesis.sock"))
    # Exactly the magic's length: closing with unread bytes would reset instead of a clean EOF.
    client.sendall(b"XXXX")
    client.settimeout(5)
    assert client.recv(16) == b""
    client.close()


def test_interactive_root_through_the_channel(nem):
    start_channel(nem)
    res = nem.run("root", euid=1000, stdin="echo relayed\nexit\n", timeout=20)
    assert "relayed" in res.stdout


def test_client_reports_when_no_daemon_is_running(nem):
    res = nem.run("pkg-list", euid=1000, timeout=30)
    assert res.returncode == 1 and "daemon not running" in res.stderr


@known_bug("G19", "the client allows a 768-byte argument and the server rejects it")
def test_an_argument_at_the_size_limit_works_or_is_refused_by_the_client(nem):
    start_channel(nem)
    res = nem.run("root", "-c", "echo " + "x" * 763, euid=1000)
    assert res.returncode == 0 or "too long" in res.stderr


@pytest.mark.skipif(not os.environ.get("NEMESIS_SANITIZE"), reason="only ASan can see this; the log usually looks right without it")
@known_bug("G22", "usb.c frees the descriptor at line 563, then logs type and path from it at line 566")
def test_the_handler_logs_an_insertion_without_reading_freed_memory(nem):
    """Given a build with ASan and a stick with a valid execute descriptor
    When the USB Handler finishes with it
    Then it survives to record the insertion
    """
    nem.android(up=False)
    nem.stick(EXECUTE, {"NEMESIS.sh": "exit 0\n"})
    run_handler(nem)
    handled(nem, "handled type=execute path=NEMESIS.sh reply=recovery", 5)


def test_an_argument_over_the_limit_is_refused_by_the_client(nem):
    """Given a running channel and an argument above SU_MAX_ARG (su.c:51)
    When an unprivileged caller sends it
    Then the client fails with a write error and the command does not run
    """
    start_channel(nem)
    marker = nem.root / "ran"
    res = nem.run("root", "-c", f'touch "{marker}" #' + "x" * 800, euid=1000)
    assert res.returncode == 1 and "daemon write failed" in res.stderr
    assert not marker.exists()


def test_channel_output_is_capped_and_keeps_the_exit_code(nem):
    """Given a running channel and a command printing more than SU_MAX_OUT (su.c:13)
    When an unprivileged caller runs it
    Then the caller gets exactly 512 KiB of output and the command's exit code
    """
    start_channel(nem)
    res = nem.run("root", "-c", "head -c 700000 /dev/zero | tr '\\0' x; exit 3", euid=1000, timeout=60)
    assert res.returncode == 3
    assert len(res.stdout) == 512 * 1024


@pytest.mark.parametrize("arg", ["1000", "--system"])
def test_interactive_root_with_a_uid_through_the_channel(nem, arg):
    """Given a running channel (02 section 3)
    When `nemesis root <uid>` or `--system` runs with no command
    Then it stays an interactive shell, as that uid
    """
    start_channel(nem)
    res = nem.run("root", arg, euid=1000, stdin="echo relayed\nexit\n", timeout=20)
    assert "relayed" in res.stdout
    assert "uid 1000" in nem.ids()


def test_root_with_words_and_no_dash_c_runs_them_through_the_channel(nem):
    """Given a running channel (usb.c:77)
    When `nemesis root echo hello there` runs with no -c
    Then the words run as one command and its output comes back
    """
    start_channel(nem)
    res = nem.run("root", "echo", "hello", "there", euid=1000)
    assert res.returncode == 0 and "hello there" in res.stdout


def test_root_joins_the_words_after_dash_c(nem):
    """Given `nemesis root -c` followed by several words (usb.c:60)
    When it runs
    Then the words form one command
    """
    res = nem.run("root", "-c", "echo", "joined", "words")
    assert res.returncode == 0 and "joined words" in res.stdout


def test_unreadable_descriptor_is_handled_as_invalid(nem):
    """Given a stick whose NEMESIS.data cannot be read (usb.c:491)
    When the USB Handler sees it with Android down
    Then it records the insertion as handled and runs nothing
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores the file mode")
    nem.android(up=False)
    usb = nem.stick(EXECUTE, {"NEMESIS.sh": marker_script(nem)})
    (usb / "NEMESIS.data").chmod(0)
    run_handler(nem)
    handled(nem, "reply=skip-ui")
    assert "skip UI invalid" in nem.log()
    assert not (nem.root / "ran").exists()


def test_execute_with_a_missing_script_reports_a_failure_dialog(nem):
    """Given a descriptor naming a script that is not on the stick (usb.c:351)
    When the user answers Yes
    Then the handler logs the missing file and shows the failure dialog
    """
    nem.answer_dialogs("yes")
    nem.stick(EXECUTE)
    run_handler(nem)
    assert wait_for(lambda: "nemesis_mode result_fail" in nem.calls())
    assert "execute missing" in nem.log()


def test_pid_paths_replace_the_slash(nem):
    install_service(nem)
    assert (nem.root / "data/nemesis/pids/my.tool+d").exists()
