#!/usr/bin/env python3
"""Mutation run over the host suite, on throwaway copies of the repo.

Each mutant changes one spot, then runs the tests that should notice. A mutant the
tests still pass on has survived. python mutate.py [--jobs N] [--only SUBSTR] [--diff FILE] [--json FILE]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue

REPO = Path(__file__).resolve().parents[3]
MAX_PROCESSES = 3000

SHELL_OPS = [
    (r"\breturn 1\b", "return 0"),
    (r"\breturn 0\b", "return 1"),
    (r"\bexit 1\b", "exit 0"),
    (r"\bexit 0\b", "exit 1"),
    (r"\[ -s ", "[ ! -s "),
    (r"\[ ! -s ", "[ -s "),
    (r"\[ -f ", "[ ! -f "),
    (r"\[ ! -f ", "[ -f "),
    (r"\[ ! -x ", "[ -x "),
    (r"-eq 0\b", "-ne 0"),
    (r"-ne 0\b", "-eq 0"),
    (r"-lt ", "-ge "),
    (r"\b_rc=1\b", "_rc=0"),
    (r"\b_seen=1\b", "_seen=0"),
    (r"\b_got=1\b", "_got=0"),
    (r"\bif ! ", "if "),
    (r"\|\| return 1", "|| true"),
    (r"\|\| _rc=1", "|| true"),
    (r"&& return 0", "&& return 1"),
    (r"\bcp -f ", "true "),
    (r"\brm -rf ", "true "),
    (r"\brm -f ", "true "),
    (r"\bmkdir -p ", "true "),
    (r"\bsetprop ", "true "),
    (r"\bsettings (put|delete) ", "true "),
    (r"\bsys_ro\s*$", "true"),
    (r"\bsnapshot_all \|\| exit 1", "true"),
    (r"\bremove_backup_dir \|\| ", "true || "),
    (r"= replace\b", "!= replace"),
    (r"= apply\b", "!= apply"),
    (r"\b5555\b", "5556"),
    (r"ro\.adb\.secure 0", "ro.adb.secure 1"),
    (r"persist\.wifi\.state 3", "persist.wifi.state 4"),
    (r"support carplay", "support none"),
    (r"p2p_disabled=0", "p2p_disabled=1"),
    (r'"#\$\{_line\}"', '"${_line}"'),
    (r"/data/nemesis/backup/\?\*", "*"),
]
PY_OPS = [
    (r"if not meta\.is_file\(\)", "if meta.is_file()"),
    (r"if p\.is_file\(\)", "if True"),
    (r"ZIP_DEFLATED", "ZIP_STORED"),
    (r"\.as_posix\(\)", ".name"),
    (r"src\.parent / ", "src / "),
    (r"rglob", "glob"),
    (r'"w", compression', '"a", compression'),
    (r"\{src\.name\}\.zip", "{src.name}.tar"),
    (r"\bif not ", "if "),
    (r" > ", " >= "),
    (r" >= ", " > "),
    (r" != ", " == "),
    (r" == ", " != "),
    (r" and ", " or "),
    (r" or ", " and "),
]
# The generated wrapper only. Other util.c text needs a compiler, so it waits for the C layer.
WRAP_OPS = [
    (r"\*' remount'\*\)", "*' remountx'*)"),
    (r"\*'/system'\*\|", "*'/systemx'*|"),
    (r"\*' / '\*\|", "*' /x '*|"),
    (r"\*',ro '\*\|", ""),
    (r"nemesis remount ro", "nemesis remount rw"),
    (r"exec /system/xbin/nemesis remount rw", "exec /system/xbin/nemesis remount ro"),
    (r'exec /system/bin/mount \\"\$@\\"', "exit 0"),
]
C_OPS = [
    (r"\breturn -1;", "return 0;"),
    (r"\breturn 1;", "return 0;"),
    (r"\breturn 0;", "return 1;"),
    (r" != 0\b", " == 0"),
    (r" == 0\b", " != 0"),
    (r" \|\| ", " && "),
    (r" && ", " || "),
    (r"\bif \(!", "if ("),
    (r" < ", " <= "),
    (r" >= ", " > "),
]
HOOK_TESTS = ["test_hooks.py", "test_hook_faults.py"]
UPDATE_TESTS = ["test_native_update.py", "test_native_compat.py"]
NATIVE_TESTS = [*UPDATE_TESTS, "test_native_packages.py", "test_native_edges.py", "test_native_runtime.py", "test_native_properties.py"]
TARGETS = [
    ("Sources/tools/make_nemesis_package.py", PY_OPS, ["test_builder.py", "test_properties.py", "test_native_compat.py", "-k", "not adb and not uninstall and not install"], None),
    ("Sources/tools/make_update_zip.py", PY_OPS, ["test_native_update.py", "-k", "tool"], None),
    *[(f"Sources/native/{name}.c", C_OPS, UPDATE_TESTS, None) for name in ("meta", "update")],
    ("Sources/native/util.c", WRAP_OPS, ["test_mount_wrapper.py"], "static const char wrap[]"),
    ("Sources/packages/adb-tcp/scripts/common.sh", SHELL_OPS, HOOK_TESTS + ["test_properties.py"], None),
    ("Sources/packages/adb-tcp/scripts/post.sh", SHELL_OPS, HOOK_TESTS, None),
    ("Sources/packages/adb-tcp/scripts/pre.sh", SHELL_OPS, HOOK_TESTS, None),
    ("Sources/packages/adb-tcp/scripts/preun.sh", SHELL_OPS, HOOK_TESTS, None),
    ("Sources/packages/wireless-projection/scripts/common.sh", SHELL_OPS, HOOK_TESTS, None),
    ("Sources/packages/wireless-projection/scripts/post.sh", SHELL_OPS, HOOK_TESTS, None),
    ("Sources/packages/wireless-projection/scripts/pre.sh", SHELL_OPS, HOOK_TESTS, None),
    ("Sources/packages/wireless-projection/scripts/preun.sh", SHELL_OPS, HOOK_TESTS, None),
    *[(f"Sources/native/{name}.c", C_OPS, NATIVE_TESTS, None) for name in ("ini", "zip", "pkg", "svc", "usb", "su", "util", "main")],
]


def generate(only):
    for rel, ops, tests, region in TARGETS:
        if only and only not in rel:
            continue
        lines = (REPO / rel).read_bytes().decode("utf-8").split("\n")
        active = region is None
        for number, raw in enumerate(lines):
            line = raw.rstrip("\r")
            if region and region in line:
                active = True
                continue
            if region and active and line.strip() == "};" or region and active and "mkdir_p(NEMESIS_BINDIR" in line:
                active = False
            if not active or (rel.endswith(".sh") and line.lstrip().startswith("#")):
                continue
            # Comments, preprocessor lines and string continuations such as the mount wrapper.
            if ops is C_OPS and line.lstrip().startswith(("/*", "*", "//", "#", '"')):
                continue
            for pattern, repl in ops:
                for match in re.finditer(pattern, line):
                    changed = line[: match.start()] + repl + line[match.end() :]
                    yield {
                        "file": rel,
                        "line": number + 1,
                        "from": line.strip(),
                        "to": changed.strip(),
                        "new": changed,
                        "tests": tests,
                    }


def changed_lines(diff_text: str) -> set:
    """The (file, line) pairs a `git diff -U0` added or changed."""
    keep = set()
    current = None
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            current = raw[4:].strip()[2:] if raw[4:].startswith("b/") else None
        hunk = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", raw)
        if hunk and current:
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            keep.update((current, line) for line in range(start, start + count))
    return keep


def copy_repo(dest: Path) -> None:
    shutil.copytree(
        REPO,
        dest,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".hypothesis", "build-native", "ota_package.tar", "firmware", "AVNT+*", "Open+Source+*"
        ),
    )


def limit_resources() -> None:
    """A mutant can write or fork without end, and either can take the whole machine down."""
    import resource

    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))


def reap(marker: str) -> int:
    """Kills every process whose command line names this run's unit directory.

    Daemons under test detach on purpose, so ending pytest does not end them, and a mutant can
    leave them respawning. Repeats until none are left, because a respawner races the scan.
    """
    killed = 0
    for _ in range(20):
        found = 0
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                cmdline = Path(f"/proc/{name}/cmdline").read_bytes()
                if marker.encode() in cmdline:
                    os.kill(int(name), signal.SIGKILL)
                    found += 1
            except OSError:
                continue
        killed += found
        if not found:
            break
        time.sleep(0.1)
    return killed


def run_tests(root: Path, tests) -> int:
    # Short, because unit sockets live under it and sun_path holds 108 bytes.
    unit_tmp = Path(f"/tmp/nm{os.getpid()}-{root.name}")
    unit_tmp.mkdir(exist_ok=True)
    try:
        return run_pytest(root, tests, unit_tmp)
    finally:
        if os.name == "posix":
            reap(str(unit_tmp) + "/")
        shutil.rmtree(unit_tmp, ignore_errors=True)


def run_pytest(root: Path, tests, unit_tmp: Path) -> int:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", NEMESIS_UNIT_TMP=str(unit_tmp))
    cmd = [sys.executable, "-B", "-m", "pytest", "-x", "-q", "--basetemp", str(root / ".tmp"), *tests]
    try:
        return subprocess.run(
            cmd,
            cwd=str(root / "Sources/tests/unit"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=450,
            preexec_fn=limit_resources if os.name == "posix" else None,
        ).returncode
    except subprocess.TimeoutExpired:
        return 124


def run_mutant(mutant, pool: Queue) -> dict:
    root = pool.get()
    target = root / mutant["file"]
    original = target.read_bytes()
    try:
        lines = original.decode("utf-8").split("\n")
        eol = "\r" if lines[mutant["line"] - 1].endswith("\r") else ""
        lines[mutant["line"] - 1] = mutant["new"] + eol
        target.write_bytes("\n".join(lines).encode("utf-8"))
        rc = run_tests(root, mutant["tests"])
    finally:
        target.write_bytes(original)
        pool.put(root)
    result = {k: mutant[k] for k in ("file", "line", "from", "to")}
    result["status"] = "survived" if rc == 0 else "killed"
    print(f"{result['status']:9} {mutant['file']}:{mutant['line']}  {mutant['to'][:80]}", flush=True)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default=None)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--retest", type=Path, action="append", default=[], help="only the survivors in this results file")
    ap.add_argument("--tests", nargs="+", default=None, help="run these test files instead of the usual ones")
    ap.add_argument("--diff", type=Path, default=None, help="only the lines this `git diff -U0` output added or changed")
    args = ap.parse_args()
    mutants = list(generate(args.only))
    if args.diff:
        keep_lines = changed_lines(args.diff.read_text(encoding="utf-8", errors="replace"))
        mutants = [m for m in mutants if (m["file"], m["line"]) in keep_lines]
    if args.retest:
        # A survivor already passed the usual tests, so only new tests can catch it.
        keep = set()
        for path in args.retest:
            for row in json.loads(path.read_text(encoding="utf-8")):
                if row["status"] == "survived":
                    keep.add((row["file"], row["line"], row["to"]))
        mutants = [m for m in mutants if (m["file"], m["line"], m["to"]) in keep]
    if args.tests:
        for mutant in mutants:
            mutant["tests"] = args.tests
    print(f"{len(mutants)} mutants", flush=True)
    base = Path(tempfile.mkdtemp(prefix="nemesis-mut-"))
    try:
        pool: Queue = Queue()
        for index in range(args.jobs):
            root = base / str(index)
            copy_repo(root)
            pool.put(root)
        probe = pool.get()
        for tests in {tuple(m["tests"]) for m in mutants}:
            if run_tests(probe, list(tests)) != 0:
                print(f"baseline is not green for {tests}", file=sys.stderr)
                return 2
        pool.put(probe)
        with ThreadPoolExecutor(args.jobs) as ex:
            results = list(ex.map(lambda m: run_mutant(m, pool), mutants))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    survived = [r for r in results if r["status"] == "survived"]
    print(f"{len(results) - len(survived)}/{len(results)} killed, {len(survived)} survived")
    for item in survived:
        print(f"SURVIVED {item['file']}:{item['line']}\n  - {item['from']}\n  + {item['to']}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
