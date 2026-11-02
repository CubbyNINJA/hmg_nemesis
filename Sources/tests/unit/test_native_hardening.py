"""Scenarios written for changes that mutation testing showed no test noticed.

Most drive a failure the system would not otherwise produce, through the fault files in native/shim.c.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from helpers import NATIVE, builder_cmd
from native_unit import make_zip, wait_for
from test_native_edges import start_channel
from test_native_update import HOOK, OLD_HOOK, installed, new_binary, unchanged, update_zip, tool, tool_args, tool_files  # noqa: F401

HERE = Path(__file__).resolve().parent


def build(folder, tmp_path):
    return subprocess.run(builder_cmd(folder, "-o", tmp_path / "o.zip"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def info(nem, text):
    return nem.run("pkg-info", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": text}))


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    return root


def manifest(folder, data):
    (folder / "NEMESIS.data").write_bytes(data if isinstance(data, bytes) else data.encode())


def run_with_no_stdin(nem, *args):
    """As a daemon with a closed stdin runs it, so the next file opened gets descriptor 0."""
    return subprocess.run(
        [str(nem.cli), *map(str, args)],
        cwd=str(nem.workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        preexec_fn=lambda: os.close(0),
        timeout=60,
    )


# --- Line counting in an error, in the builder and on the unit


LINE_CASES = [
    ('[nemesis]\n\nid=my.tool\nname="open\n', "line 4"),
    ('[nemesis]\r\n\r\nid=my.tool\r\nname="open\r\n', "line 4"),
    ('[nemesis]\r\rid=my.tool\rname="open\r', "line 4"),
    ('[nemesis]\nid=my.tool\nname="a\nb"\nnote="open\n', "line 5"),
    ('[nemesis]\nid=my.tool\nname="a\r\nb"\nnote="open\n', "line 5"),
    ('[nemesis]\nid=my.tool\nname="a\rb"\nnote="open\n', "line 5"),
]


@pytest.mark.parametrize("text, line", LINE_CASES)
def test_the_builder_names_the_right_line(folder, tmp_path, text, line):
    """Given an unterminated value after blank lines or a line break inside a quoted value (ini.c:95)
    When the builder reads it
    Then its message names the line the value starts on
    """
    manifest(folder, text)
    res = build(folder, tmp_path)
    assert res.returncode != 0
    assert f"{line}: unterminated" in res.stderr


@pytest.mark.parametrize("text, line", LINE_CASES)
def test_the_unit_names_the_same_line_as_the_builder(nem, text, line):
    res = info(nem, text)
    assert res.returncode != 0
    assert f"{line}: unterminated" in res.stderr


def test_the_builder_reads_a_file_that_ends_in_spaces(folder, tmp_path):
    manifest(folder, "[nemesis]\nid=my.tool\n   ")
    res = build(folder, tmp_path)
    assert res.returncode == 0, res.stderr


def test_the_builder_reports_a_value_cut_off_after_a_return(folder, tmp_path):
    manifest(folder, '[nemesis]\nid=my.tool\nname="a\r')
    res = build(folder, tmp_path)
    assert res.returncode != 0
    assert "unterminated" in res.stderr


def test_the_builder_says_what_is_wrong_with_an_id(folder, tmp_path):
    manifest(folder, "[nemesis]\nid=a b\n")
    assert "bad or missing id='a b'" in build(folder, tmp_path).stderr


def test_the_tool_leaves_out_a_recovery_script_that_does_not_exist(installed, tool_files):  # noqa: F811
    (tool_files / "recovery.sh").unlink()
    out = installed.workdir / "t.zip"
    assert tool("-o", out, "--version", "999.0", *tool_args(tool_files)).returncode == 0
    assert "core/install-recovery.sh" not in zipfile.ZipFile(out).namelist()


# --- Descriptor reading


@pytest.mark.parametrize(
    "text",
    [
        '  [nemesis]\n  id=my.tool\n',
        '[nemesis]\n  # x="never closed\nid=my.tool\n',
        '[nemesis]\n\t; x="never closed\nid=my.tool\n',
        "[nemesis]\nid=my.tool\n" + "x" * 2000 + "=v\n",
        "[nemesis]\n#" + "x" * 2000 + "\nid=my.tool\n",
    ],
    ids=["indented-section", "indented-hash", "tab-semicolon", "long-key", "long-comment"],
)
def test_ini_lines_with_leading_space_or_great_length_are_read(nem, text):
    res = info(nem, text)
    assert res.returncode == 0, res.stderr
    assert "id=my.tool\n" in res.stdout


def test_a_description_with_too_many_bytes_is_refused(nem):
    """Given a value short in characters but too long for its buffer, in stray continuation bytes (meta.c:165)"""
    raw = b"[nemesis]\nid=my.tool\nshort-desc=a" + b"\xa9" * 300 + b"\n"
    res = nem.run("pkg-info", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": raw}))
    assert res.returncode != 0
    assert "short-desc is too long" in res.stderr


def test_upstreams_metadata_checks_pass():
    """Given upstream's own host checks for meta.c, including meta_depends_on, which no product code calls"""
    compiler = os.environ.get("CC", "gcc")
    if not shutil.which(compiler):
        pytest.skip("needs a C compiler")
    target = Path(os.environ.get("NEMESIS_UNIT_TMP", "/tmp")) / f"meta_host_{os.getpid()}"
    subprocess.check_call(
        [compiler, "-std=gnu11", "-w", f"-I{NATIVE}", f"-I{HERE / 'native' / 'stubs'}", str(HERE.parent / "meta_host_test.c"), str(NATIVE / "meta.c"), str(NATIVE / "ini.c"), "-o", str(target)]
    )
    try:
        assert subprocess.run([str(target)], stdout=subprocess.DEVNULL).returncode == 0
    finally:
        target.unlink()


def device(nem, os_id="1.0", model="EV6"):
    (nem.root / "props" / "ro.build.product").write_text(os_id)
    (nem.root / "props" / "ro.mobis.targetdir").write_text(model)


@pytest.mark.parametrize("blank", [" ", "\t", "\n", " , ,\t"], ids=["space", "tab", "newline", "separators"])
def test_a_list_of_only_blanks_counts_as_missing(nem, blank):
    """Given an os list holding no items (04-Packages.md section 2: missing means any)"""
    device(nem)
    assert "compat=full" in nem.run("compat-check", blank, "none").stdout


def test_a_dependency_id_too_long_for_its_buffer_is_cut_short_safely(nem):
    res = nem.run("compat-check", "any", "any", "x" * 96)
    assert f"dep-missing {'x' * 95}\n" in res.stdout


def test_a_range_too_long_to_compare_never_matches(nem):
    """Given a range whose lower bound fills the 32-byte buffer, and an installed version inside it (meta.c:95)"""
    nem.run("install", make_zip(nem.workdir / "d.zip", {"NEMESIS.data": "[nemesis]\nversion=1.1\nid=dep\npackage-version=1\n"}))
    assert "compat=incompatible" in nem.run("compat-check", "any", "any", "dep@" + "0" * 32 + "-2").stdout


# --- Starting the staged binary


def test_a_unit_that_forbids_running_from_data_still_updates(installed):  # noqa: F811
    """Given a staged binary that SELinux will not let run, which says nothing about the binary (update.c:448)"""
    nem = installed
    nem.path("fail_exec").write_text("update/stage")
    res = nem.run("update", update_zip(nem))
    assert res.returncode == 0, res.stderr
    assert "test run of the new binary not permitted" in nem.log()


@pytest.mark.parametrize("fault", ["fail_fork", "fail_waitpid"])
def test_a_binary_that_cannot_be_tried_is_not_installed(installed, fault):  # noqa: F811
    nem = installed
    nem.path(fault).write_text("")
    res = nem.run("update", update_zip(nem))
    assert res.returncode == 8
    assert "does not run" in res.stderr
    assert unchanged(nem)


# --- Descriptor 0


def test_an_update_works_with_stdin_closed(installed):  # noqa: F811
    """Given a caller with no stdin, so files open as descriptor 0, which is a valid descriptor
    When an update replaces files, the hook, and stops a running watchdog
    Then all of it works
    """
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n" + OLD_HOOK)
    assert nem.run("watchdog").returncode == 0
    assert wait_for(nem.path("data/nemesis/watchdog.pid").exists)
    old = int(nem.path("data/nemesis/watchdog.pid").read_text().split()[0])
    res = run_with_no_stdin(nem, "update", "--no-reboot", update_zip(nem, files={"core/qb_hook.sh": HOOK}))
    assert res.returncode == 0, res.stderr
    assert nem.cli.read_bytes() == new_binary(nem)
    assert qb.read_text() == "stock\n" + HOOK
    assert not os.path.exists(f"/proc/{old}")


# --- Stopping daemons


def test_a_process_it_cannot_inspect_is_not_treated_as_a_daemon(installed):  # noqa: F811
    """Given another NEMESIS process whose command line cannot be read (pkg.c:1222)
    When an update stops the daemons
    Then that process is left alone
    """
    nem = installed
    handler = nem.daemon("usb-handler")
    assert wait_for(lambda: "usb-handler start" in nem.log())
    nem.path("fail_open").write_text("/cmdline")
    assert nem.run("update", "--no-reboot", update_zip(nem)).returncode == 0
    assert handler.poll() is None


def test_a_wipe_through_the_channel_outlives_the_watchdog_it_stops(nem):
    """Given a wipe asked for over the root channel, run by a worker forked from the watchdog (pkg.c:1216)
    When it stops every daemon
    Then it spares that worker, so the caller still gets the result
    """
    start_channel(nem)
    res = nem.run("wipe", "--confirm", "--no-reboot", euid=1000)
    assert res.returncode == 0, res.stderr
    assert "ok wiped" in res.stdout


# --- New defects found while writing these


def test_a_restore_puts_back_a_deleted_quickboot_file(installed):  # noqa: F811
    """Given a migration that deletes the QuickBoot file and then fails
    When the restore runs
    Then the file is back and the update reports that it restored
    """
    nem = installed
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n")
    res = nem.run("update", update_zip(nem, files={"scripts/migrate.sh": f'rm "{qb}"\nexit 1\n'}))
    assert res.returncode == 10, res.stdout
    assert qb.read_text() == "stock\n"


def test_a_hook_that_cannot_keep_its_owner_is_not_replaced(nem):
    """Given a QuickBoot file whose replacement cannot be given the right owner (pkg.c:1134)
    When the unit is wiped
    Then the file is left as it was, rather than replaced with the wrong owner
    """
    qb = nem.path("system/etc/qb_runtime.sh")
    text = "stock\n" + OLD_HOOK
    qb.write_text(text)
    nem.path("chown_fails").write_text("")
    nem.run("wipe", "--confirm", "--no-reboot")
    assert qb.read_text() == text


def test_a_wipe_that_cannot_remove_the_hook_says_so(nem):
    """Given a QuickBoot file whose hook cannot be removed (02 wipe)
    When the unit is wiped
    Then it exits 1 and says the wipe is incomplete, because the boot script still calls NEMESIS
    """
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n" + OLD_HOOK)
    nem.path("chown_fails").write_text("")
    res = nem.run("wipe", "--confirm", "--no-reboot")
    assert "NEMESIS_QB_HOOK" in qb.read_text()
    assert res.returncode == 1
    assert "wipe incomplete" in res.stdout


def test_a_stale_pid_only_watchdog_file_does_not_stop_the_watchdog(nem):
    """Given a watchdog pidfile with only a pid, which is the pid the new watchdog happens to get (util.c:574)
    When the watchdog starts and detaches
    Then a watchdog is running, rather than the copy seeing its own parent and exiting
    """
    pidfile = nem.path("data/nemesis/watchdog.pid")
    subprocess.Popen(
        ["sh", "-c", f'echo $$ > "{pidfile}"; exec "{nem.cli}" watchdog'],
        cwd=str(nem.workdir),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert wait_for(lambda: "watchdog start" in nem.log(), 6), nem.log()


# --- The boot token's failure paths


TOKENLESS = re.compile(r"^\d+ \d+\n$")


def pidfile_after_start(nem):
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=background\n"
    assert nem.run("install", nem.package(log_hooks=False, sections=sections, files={"svc/d.sh": "sleep 60\n"})).returncode == 0
    return nem.path("data/nemesis/pids/my.tool+d").read_text()


def test_with_no_random_source_a_pidfile_has_no_token(nem):
    nem.path("fail_open").write_text("/dev/urandom")
    assert TOKENLESS.match(pidfile_after_start(nem))


def test_when_the_token_cannot_be_linked_a_pidfile_has_no_token(nem):
    nem.path("fail_link").write_text("")
    assert TOKENLESS.match(pidfile_after_start(nem))
    assert list(nem.path("dev").glob(".nemesis-boot*")) == []


def test_when_the_token_cannot_be_written_a_pidfile_has_no_token(nem):
    dev = nem.path("dev")
    dev.chmod(0o555)
    try:
        assert TOKENLESS.match(pidfile_after_start(nem))
    finally:
        dev.chmod(0o755)


@pytest.mark.parametrize("text", ["abc\n" + "x" * 40, "\n" * 40, "x" * 31 + "\n" + "y" * 8], ids=["short-line", "only-newlines", "one-short"])
def test_a_token_file_with_a_short_first_line_is_not_used(nem, text):
    """Given a token file long enough overall, but whose first line is not a whole token (util.c:523)"""
    nem.path("dev/.nemesis-boot").write_text(text)
    assert TOKENLESS.match(pidfile_after_start(nem))


# --- usb-info


def test_usb_info_list_fails_when_the_descriptor_cannot_be_read(nem):
    nem.stick("[nemesis]\ntype=list\n")
    nem.path("fail_open").write_text("storage/usb0/NEMESIS.data")
    assert nem.run("usb-info", "--list").returncode == 1


