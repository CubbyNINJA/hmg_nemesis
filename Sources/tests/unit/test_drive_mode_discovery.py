"""The drive-mode discovery stick (docs/drive-mode-memory-package.md, Phase 1).

What matters most is that it can only watch, so that is what most of these check.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import SOURCES

STICK = SOURCES / "examples" / "usb-stick-drive-mode-discovery"
SCRIPT = STICK / "NEMESIS.sh"
ANALYZER = STICK / "analyze.py"
# Anything here could change the unit or the car. None of it may run.
FORBIDDEN = [
    "setprop", "input", "sendevent", "am", "pm", "settings", "service", "svc", "nemesis", "reboot",
    "rm", "mv", "cp", "dd", "mount", "umount", "chmod", "chown", "ln", "stop", "start", "cmd", "log",
]
SHELL = os.environ.get("NEMESIS_TEST_SH") or shutil.which("mksh") or shutil.which("sh")


def code_lines():
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def test_the_script_names_no_command_that_could_change_anything():
    """Given the script as shipped
    When its code is read, leaving out comments
    Then no command that sets, sends, removes or remounts appears at all
    """
    code = "\n".join(code_lines())
    for word in FORBIDDEN:
        assert not re.search(rf"(?<![\w./-]){word}(?![\w./-])", code), word


def test_the_script_writes_only_inside_its_own_folder():
    for line in code_lines():
        for target in re.findall(r">>?\s*(\S+)", line):
            assert target.startswith('"$OUT/') or target.startswith("/dev/null") or target.startswith("&1"), line


def test_the_script_does_not_clear_the_log_or_read_the_radio_buffer():
    code = "\n".join(code_lines())
    assert "logcat -c" not in code
    assert "radio" not in code


def test_the_stick_uses_a_plain_execute_descriptor_and_no_emergency_file():
    """Given the stick. It must only run after the user taps Yes, so it has no NEMESIS.emergency"""
    assert (STICK / "NEMESIS.data").read_text() == "[nemesis]\nversion=1.0\ntype=execute\npath=NEMESIS.sh\n"
    assert not (STICK / "NEMESIS.emergency").exists()


def test_shipped_files_use_lf():
    for path in (SCRIPT, STICK / "NEMESIS.data", ANALYZER):
        assert b"\r" not in path.read_bytes()


def stub(folder: Path, name: str, body: str) -> None:
    target = folder / name
    target.write_text("#!/bin/sh\n" + body)
    target.chmod(0o755)


@pytest.fixture
def unit(tmp_path):
    """A stick and a fake toolbox. The forbidden commands exist, and record it if anything runs them."""
    if os.name != "posix" or not SHELL:
        pytest.skip("needs a POSIX shell")
    stick = tmp_path / "stick"
    stick.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    called = tmp_path / "called"
    for word in FORBIDDEN:
        stub(tools, word, f'echo "{word} $*" >> "{called}"\nexit 0\n')
    counter = tmp_path / "count"
    stub(
        tools,
        "getprop",
        f'n=$(cat "{counter}" 2>/dev/null || echo 0); echo $((n + 1)) > "{counter}"\n'
        'echo "[ro.build.product]: [1.0]"\necho "[ro.serialno]: [ABC123]"\n'
        'echo "[persist.sys.fake.airplane]: [0]"\necho "[vendor.car.drive_mode]: [eco]"\n'
        'if [ "$n" -ge 10 ]; then echo "[vendor.other]: [x]"; fi\n',
    )
    stub(tools, "logcat", 'echo "$*" > logcat-args\necho "11-02 D/Vehicle: DRIVE_MODE changed AA:BB:CC:DD:EE:FF"\nexec /bin/sleep 300\n')
    stub(tools, "ps", 'echo "root 1 init"\necho "root 200 micomd"\n')
    stub(tools, "sleep", "exit 0\n")
    stub(tools, "date", 'echo "12:00:00"\n')
    stub(tools, "ls", 'echo "listing $*"\n')
    stub(tools, "sync", "exit 0\n")
    stub(tools, "mkdir", '/bin/mkdir "$@"\n')
    text = SCRIPT.read_text(encoding="utf-8")
    lines = [f'export PATH="{tools}"' if line.startswith("export PATH=") else line for line in text.splitlines()]
    script = stick / "NEMESIS.sh"
    script.write_text("\n".join(lines) + "\n")
    return {"stick": stick, "script": script, "called": called, "tools": tools, "root": tmp_path}


def run(unit):
    return subprocess.run([SHELL, str(unit["script"])], cwd=str(unit["stick"]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)


def result_folder(unit) -> Path:
    found = list(unit["stick"].glob("drive-mode-discovery-*"))
    assert len(found) == 1
    return found[0]


def test_a_run_changes_nothing_and_saves_to_the_stick(unit):
    """Given a fake unit where every command that could change something would be recorded
    When the script runs
    Then it exits 0, none of them ran, and everything it wrote is in one new folder
    """
    res = run(unit)
    assert res.returncode == 0, res.stderr
    assert not unit["called"].exists(), unit["called"].read_text()
    assert sorted(p.name for p in unit["stick"].iterdir() if p.name not in ("NEMESIS.sh", "logcat-args"))[0].startswith("drive-mode-discovery-")
    folder = result_folder(unit)
    assert len(list((folder / "props").glob("*.txt"))) == 60
    assert (folder / "before-ps.txt").exists() and (folder / "after-ps.txt").exists()
    assert "DRIVE_MODE" in (folder / "logcat.txt").read_text()


def test_the_log_is_stopped_when_the_capture_ends(unit):
    run(unit)
    alive = subprocess.run(["pgrep", "-f", str(unit["tools"])], stdout=subprocess.PIPE, universal_newlines=True).stdout
    assert not alive.strip()


def test_a_command_that_fails_does_not_stop_the_capture(unit):
    """Given a unit where ls and ps fail, as /efs may not exist or may refuse"""
    stub(unit["tools"], "ls", 'echo "ls: no such file" >&2\nexit 1\n')
    stub(unit["tools"], "ps", "exit 1\n")
    assert run(unit).returncode == 0
    assert len(list((result_folder(unit) / "props").glob("*.txt"))) == 60


def test_a_stick_it_cannot_write_to_fails_at_once(unit):
    stub(unit["tools"], "mkdir", "exit 1\n")
    assert run(unit).returncode == 1


def analyze(folder):
    return subprocess.run([sys.executable, str(ANALYZER), str(folder)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def capture(tmp_path, samples, log=""):
    folder = tmp_path / "drive-mode-discovery-1"
    (folder / "props").mkdir(parents=True)
    index = []
    for number, props in enumerate(samples):
        (folder / "props" / f"{number}.txt").write_text("".join(f"[{k}]: [{v}]\n" for k, v in props.items()))
        index.append(f"{number} 12:00:{number:02d}")
    (folder / "times.txt").write_text("\n".join(index) + "\n")
    (folder / "logcat.txt").write_text(log)
    return folder


def test_the_summary_shows_when_a_property_changed_with_likely_names_first(tmp_path):
    """Given samples where a mode property and an unrelated one change
    When the capture is analyzed
    Then each shows its values and times, and the likely one is listed first and starred
    """
    base = {"zz.unrelated": "1", "vendor.car.drive_mode": "eco", "ro.build.product": "1.0"}
    later = dict(base, **{"vendor.car.drive_mode": "sport", "zz.unrelated": "2"})
    res = analyze(capture(tmp_path, [base, base, later]))
    assert res.returncode == 0, res.stderr
    assert "* vendor.car.drive_mode: 12:00:00=eco, 12:00:02=sport" in res.stdout
    assert res.stdout.index("vendor.car.drive_mode") < res.stdout.index("zz.unrelated")


def test_the_summary_compares_every_sample_in_number_order(tmp_path):
    """Given more than 10 samples, where a text sort would put 10 before 2"""
    samples = [{"mode.a": "x"} for _ in range(12)]
    samples[11] = {"mode.a": "y"}
    assert "12:00:00=x, 12:00:11=y" in analyze(capture(tmp_path, samples)).stdout


def test_the_summary_masks_identifiers(tmp_path):
    """Given a serial property that changes, and a log line with a MAC address and a VIN"""
    samples = [{"ro.serialno": "ABC123", "persist.mode": "a"}, {"ro.serialno": "XYZ789", "persist.mode": "b"}]
    log = "D/Drive: mode for AA:BB:CC:DD:EE:FF on KNDC3DLC5N5012345\n"
    out = analyze(capture(tmp_path, samples, log)).stdout
    for secret in ("ABC123", "XYZ789", "AA:BB:CC:DD:EE:FF", "KNDC3DLC5N5012345"):
        assert secret not in out
    assert "<mac>" in out and "<vin?>" in out


def test_the_summary_says_plainly_when_nothing_changed(tmp_path):
    assert "None. Either the button was not pressed" in analyze(capture(tmp_path, [{"a": "1"}, {"a": "1"}])).stdout


def test_the_analyzer_explains_a_capture_that_never_finished(tmp_path):
    folder = tmp_path / "empty"
    (folder / "props").mkdir(parents=True)
    assert "Did the capture finish" in analyze(folder).stdout


def test_the_analyzer_refuses_a_folder_that_is_not_there(tmp_path):
    assert analyze(tmp_path / "absent").returncode == 1


def test_the_analyzer_does_not_treat_model_or_usb_as_a_lead(tmp_path):
    samples = [{"ro.product.model": "a", "sys.usb.config": "x"}, {"ro.product.model": "b", "sys.usb.config": "y"}]
    out = analyze(capture(tmp_path, samples)).stdout
    assert "* ro.product.model" not in out and "* sys.usb.config" not in out
