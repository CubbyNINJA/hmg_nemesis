"""Generated inputs against the real CLI: id and path rules, INI, zip, and the package database.

Each property states a rule from 04-Packages.md or 05-Run-Scripts.md. Hypothesis is optional so the
suite still runs on stdlib Python plus pytest, and these skip without it. Runs are derandomized, so
a failure replays. Set NEMESIS_SANITIZE=1 to build with ASan and UBSan, which turns silent memory errors in
the zip fuzz into crashes.
"""
from __future__ import annotations

import re
import zipfile

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from native_unit import make_zip, wait_for, zip_bytes  # noqa: E402

COMMON = dict(
    derandomize=True,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
ID_OK = re.compile(r"[A-Za-z0-9._-]{1,64}")
WORD = st.text("abcXYZ019_-", min_size=1, max_size=8)
PATH_METACHARS = set(";|&`$'\"()<>")


def id_is_valid(value: str) -> bool:
    # 04-Packages.md: max 64 chars of letters, digits, . _ -; no leading dot, no "..".
    return bool(ID_OK.fullmatch(value)) and not value.startswith(".") and ".." not in value


def path_is_valid(value: str) -> bool:
    # 05-Run-Scripts.md section 2.
    return bool(value) and value[0] not in "/\\" and ".." not in value and not (set(value) & PATH_METACHARS)


def manifest(id_: str) -> str:
    return f"[nemesis]\nid={id_}\n"


@settings(max_examples=60, **COMMON)
@given(
    st.one_of(
        st.text("abzAZ09._-/|;$~+", max_size=12),
        st.sampled_from(["x" * 64, "x" * 65, ".", "..", "a..", "a.b", "-", "_"]),
    )
)
def test_an_id_is_accepted_exactly_when_the_documented_rule_allows_it(nem, value):
    """Given any id
    When a package with it is installed
    Then it installs if and only if the rule allows it, and a refused id leaves nothing behind
    """
    nem.reset()
    res = nem.run("install", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": manifest(value)}))
    lines = nem.run("pkg-list").stdout.splitlines()
    if id_is_valid(value):
        assert res.returncode == 0, res.stderr
        assert any(line.startswith(f"{value}|") for line in lines)
        assert nem.app(value).is_dir()
    else:
        assert res.returncode != 0
        assert not any(line.startswith(f"{value}|") for line in lines)


NOISE = st.sampled_from(["", "   ", "# comment", "; comment", "not a pair", "[other]\nid=wrong\n[nemesis]"])
PADDING = st.sampled_from(["", " ", "\t", "  "])


@settings(max_examples=60, **COMMON)
@given(
    WORD.filter(id_is_valid),
    st.lists(WORD, min_size=1, max_size=3).map(" ".join),
    st.lists(NOISE, max_size=4),
    PADDING,
    PADDING,
    st.sampled_from(["\n", "\r\n"]),
)
def test_a_manifest_parses_the_same_through_noise_padding_and_line_endings(nem, id_, name, noise, pad_key, pad_val, eol):
    """Given a manifest with comments, junk lines, other sections, padding and either line ending
    When pkg-info reads it
    Then it reports the id and name that were written
    """
    nem.reset()
    lines = ["[nemesis]", *noise, f"{pad_key}id{pad_val}={pad_val}{id_}{pad_val}", *noise, f"name={pad_val}{name}"]
    text = eol.join("\n".join(lines).split("\n")) + eol
    res = nem.run("pkg-info", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": text}))
    assert res.returncode == 0, res.stderr
    assert f"id={id_}\n" in res.stdout
    assert f"name={name}\n" in res.stdout


def without_prefix_conflicts(names):
    """A path cannot be both a file and a directory."""
    return [n for n in names if not any(other.startswith(n + "/") for other in names)]


TREE = st.dictionaries(
    st.lists(WORD, min_size=1, max_size=3).map("/".join),
    st.binary(max_size=4096),
    max_size=8,
)


@settings(max_examples=40, **COMMON)
@given(TREE, st.sampled_from([zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED]))
def test_any_tree_installs_byte_for_byte(nem, tree, method):
    """Given any tree of files, stored or deflated
    When it is installed
    Then every file lands under the package directory with the same bytes
    """
    nem.reset()
    keep = without_prefix_conflicts(list(tree))
    members = {"NEMESIS.data": manifest("my.tool"), **{n: tree[n] for n in keep}}
    res = nem.run("install", make_zip(nem.workdir / "p.zip", members, method))
    assert res.returncode == 0, res.stderr
    for name in keep:
        assert (nem.app() / name).read_bytes() == tree[name]


@settings(max_examples=30, **COMMON)
@given(TREE, st.sampled_from(["../x", "a/../../x", "a/..", "..", "/abs", "a/b/../../../x"]))
def test_one_unsafe_member_rejects_the_whole_package(nem, tree, bad):
    """Given a package with one member whose path escapes (04-Packages.md, D1)
    When it is installed
    Then nothing is installed and nothing is written outside the staging directory
    """
    nem.reset()
    keep = without_prefix_conflicts(list(tree))
    members = {"NEMESIS.data": manifest("my.tool"), **{n: tree[n] for n in keep}, bad: b"escaped"}
    assert nem.run("install", make_zip(nem.workdir / "p.zip", members)).returncode != 0
    assert "my.tool" not in nem.db()
    assert not nem.app().exists()


@settings(max_examples=25, **COMMON)
@given(st.lists(WORD.filter(id_is_valid), min_size=1, max_size=5, unique=True), st.data())
def test_the_package_list_tracks_installs_and_uninstalls(nem, ids, data):
    """Given several packages
    When they are installed and some are uninstalled
    Then pkg-list shows exactly the rest, each with the fields it was installed with
    """
    nem.reset()
    for id_ in ids:
        text = f"[nemesis]\nid={id_}\nname=N {id_}\ntypes=application\n"
        assert nem.run("install", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": text})).returncode == 0
    removed = data.draw(st.lists(st.sampled_from(ids), unique=True))
    for id_ in removed:
        assert nem.run("uninstall", id_).returncode == 0
    # The locked USB handler is seeded on every unit, so it is always listed too.
    listed = [line for line in nem.run("pkg-list").stdout.splitlines() if not line.startswith("usb-handler|")]
    expected = {f"{i}|N {i}|0|application" for i in ids if i not in removed}
    assert set(listed) == expected


@settings(max_examples=10, **COMMON)
@given(st.text("abz09_.-/;|&`$'\"()<>\\", min_size=1, max_size=6))
def test_a_descriptor_path_runs_exactly_when_the_documented_rule_allows_it(nem, rel):
    """Given any path in a NEMESIS.data with Android down
    When the USB Handler sees it
    Then a valid path runs and an invalid one never does
    """
    nem.reset()
    nem.android(up=False)
    marker = nem.root / "ran"
    usb = nem.stick(f"[nemesis]\ntype=execute\npath={rel}\n")
    target = usb / rel
    can_exist = path_is_valid(rel) and not rel.endswith("/") and "//" not in rel and rel not in (".", "-")
    if path_is_valid(rel) and not can_exist:
        return
    if can_exist:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f'touch "{marker}"\n')
        except OSError:
            return
    daemon = nem.daemon("usb-handler")
    try:
        assert wait_for(lambda: "handled" in nem.session_log(), 15), nem.log()
    finally:
        daemon.terminate()
        daemon.wait()
    assert marker.exists() == path_is_valid(rel)


BASE_ZIP = zip_bytes(
    {
        "NEMESIS.data": "[nemesis]\nid=my.tool\nname=My Tool\n",
        "bin/tool.sh": "#!/system/bin/sh\nexit 0\n",
        "files/payload": b"payload " * 200,
    }
)


@settings(max_examples=300, **COMMON)
@given(
    st.lists(st.tuples(st.integers(0, len(BASE_ZIP) - 1), st.integers(0, 255)), max_size=8),
    st.integers(16, len(BASE_ZIP)),
)
def test_a_damaged_zip_never_crashes_the_cli(nem, flips, keep):
    """Given a package zip with random bytes changed and its tail possibly cut off
    When pkg-info and install read it
    Then both exit normally, and do not die by signal or hang
    """
    data = bytearray(BASE_ZIP)
    for position, value in flips:
        data[position] = value
    target = nem.workdir / "p.zip"
    target.write_bytes(bytes(data[:keep]))
    assert nem.run("pkg-info", target, timeout=20).returncode >= 0
    assert nem.run("install", target, timeout=20).returncode >= 0
    nem.run("uninstall", "my.tool", timeout=20)
