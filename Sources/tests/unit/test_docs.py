"""Docs against source constants, and the doc/code rulings D1, D2, D5, D6."""
from __future__ import annotations

import re

import pytest

from helpers import NATIVE, REPO, SOURCES, doc

HEADER = (NATIVE / "nemesis.h").read_text(encoding="utf-8")
ALLOWED_DOCS = [
    "AGENTS.md",
    "SCOPE.md",
    "README.md",
    "02-Debugging-Execution-Interface.md",
    "04-Packages.md",
    "05-Run-Scripts.md",
    "Packages/README.md",
    "docs/creating-a-package.md",
    "Sources/usb_bundle/README.md",
    "Sources/usb_bundle/DEVICE_TEST.md",
]


def define(name: str) -> str:
    return re.search(r"#define %s (.+)" % name, HEADER).group(1).strip().strip('"')


@pytest.mark.parametrize("name", ALLOWED_DOCS)
def test_relative_links_resolve(name):
    path = REPO / name
    links = re.findall(r"\]\(([^)#\s]+)", path.read_text(encoding="utf-8"))
    broken = [l for l in links if not l.startswith("http") and not (path.parent / l).exists()]
    assert not broken


@pytest.mark.parametrize("name", [n for n in ALLOWED_DOCS if n != "AGENTS.md"])
def test_out_of_scope_docs_are_not_linked(name):
    # AGENTS.md section 1: 01 and 03 are not part of this checkout.
    text = (REPO / name).read_text(encoding="utf-8")
    assert not re.search(r"\]\([^)]*0[13]-[A-Za-z-]+\.md", text)


def test_privileged_path_matches_the_cli_reference():
    assert define("NEMESIS_PATH") in doc("02-Debugging-Execution-Interface.md")
    assert define("NEMESIS_PATH") in doc("AGENTS.md")


@pytest.mark.parametrize(
    "name",
    ["PACKAGES_DB", "SERVICES_DB", "NEMESIS_LOGFILE", "BIN_PATH", "MANAGER_APK", "SU_SOCK_DEV", "SU_SOCK_DATA", "MANAGER_MAIN"],
)
def test_on_unit_paths_are_the_documented_ones(name):
    assert define(name) in doc("AGENTS.md")


def test_documented_limits_match_the_source():
    assert define("RESTART_LIMIT") == "5" and "5 rapid deaths" in doc("04-Packages.md")
    assert define("USB_PROMPT_SEC") == "60" and "**60 seconds**" in doc("05-Run-Scripts.md")
    assert define("LOCKED_ID") == "usb-handler"


def test_setup_press_constants_match_the_documented_behaviour():
    src = (SOURCES / "manager/src/com/nemesis/manager/SetupPress.java").read_text(encoding="utf-8")
    assert "GAP_MS = 1500" in src and "1.5 seconds" in doc("02-Debugging-Execution-Interface.md")
    assert "DEBOUNCE_MS = 200" in src and "200 ms" in doc("02-Debugging-Execution-Interface.md")
    assert "NEED = 3" in src and "Three presses" in doc("02-Debugging-Execution-Interface.md")


def test_every_documented_cli_command_is_dispatched():
    main = (NATIVE / "main.c").read_text(encoding="utf-8")
    reference = doc("02-Debugging-Execution-Interface.md")
    for op in ["install", "uninstall", "pkg-list", "pkg-info", "pkg-launch", "root", "remount", "service", "log", "wipe", "watchdog", "svcmgr", "usb-handler"]:
        assert f'!strcmp(op, "{op}")' in main
        assert f"nemesis {op}" in reference or f"| {op}" in reference


def test_manager_version_matches_the_apk_check():
    manifest = (SOURCES / "manager/AndroidManifest.xml").read_text(encoding="utf-8")
    check = (SOURCES / "tests/check_manager_apk.py").read_text(encoding="utf-8")
    assert 'android:versionCode="16"' in manifest and "versionCode 16" in check
    assert 'android:versionName="1.15"' in manifest and '"1.15"' in check


@pytest.mark.parametrize("name", ["AGENTS.md", "04-Packages.md"])
def test_d1_traversal_member_is_documented_as_rejecting_the_package(name):
    text = doc(name)
    assert "are skipped" not in text and "are not applied" not in text
    assert "rejects the whole package" in text


@pytest.mark.parametrize("name", ["04-Packages.md", "docs/creating-a-package.md"])
def test_d2_usb_update_is_documented_as_uninstall_first(name):
    text = doc(name)
    assert "update prompt" not in text
    assert "uninstall it first" in text


def test_d4_nemesis_data_paths_are_not_documented_as_rejecting_absolute():
    assert "absolute paths and `..` are rejected" not in doc("AGENTS.md")


def test_d5_unplugging_is_documented_as_not_ending_the_wait():
    text = doc("05-Run-Scripts.md")
    assert "(or the USB is gone)" not in text
    assert "does not end the wait" in text


@pytest.mark.parametrize("name", ["AGENTS.md", "docs/creating-a-package.md"])
def test_d6_postinstall_failure_is_documented_as_staying_installed(name):
    text = doc(name)
    assert "from a hook fails the install" not in text
    assert "stays installed" in text
