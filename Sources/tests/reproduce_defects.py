#!/usr/bin/env python3
"""Reproduces each NEMESIS defect against one source tree and writes what happened as JSON.

usage: reproduce_defects.py <tree> <out.json> [only-id]
Run once on the tree before the fixes and once on the tree after. NEMESIS_SANITIZE=1 is for G22 alone.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

TREE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(TREE / "Sources/tests/unit"))

import native_unit  # noqa: E402
from helpers import builder_cmd  # noqa: E402
from native_unit import executable, make_zip, wait_for  # noqa: E402
from test_native_update import HOOK, OLD_HOOK, new_binary, update_zip  # noqa: E402,F401

CASES = {}


def case(id_, setup, command):
    def wrap(fn):
        CASES[id_] = {"setup": setup, "command": command, "run": fn}
        return fn
    return wrap


def first(text, pattern=None):
    lines = [l for l in text.splitlines() if l.strip()]
    if pattern:
        lines = [l for l in lines if re.search(pattern, l)]
    return lines[0] if lines else ""


def outcome(res, pattern=None):
    message = first(res.stderr, pattern) or first(res.stdout, pattern)
    return f"exit {res.returncode}" + (f": {message}" if message else "")


def answer_in_turn(nem, *replies):
    answers = nem.root / "answers"
    answers.write_text("".join(f"{r}\n" for r in replies))
    session = nem.root / "data/nemesis/usb-session"
    executable(nem.bin("am"), f'echo "am $*" >> "{nem.root}/calls"\n(sleep 1; mkdir -p "{session}"; head -1 "{answers}" > "{session}/reply"; sed -i 1d "{answers}") >/dev/null 2>&1 &\n')


def info(nem, members):
    return nem.run("pkg-info", make_zip(nem.workdir / "p.zip", members))


def builder(tmp, files, *args):
    root = Path(tmp) / "pkg"
    root.mkdir()
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root, subprocess.run(builder_cmd(root, "-o", Path(tmp) / "o.zip", *args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


@case("G25", "my.tool is installed. A stick holds a new copy marked for another OS. The user taps Yes, then No on the risk dialog.", "insert stick, Yes, No")
def g25(nem):
    (nem.root / "props/ro.build.product").write_text("1.0")
    nem.run("install", nem.package(log_hooks=False, files={"v": "1"}))
    answer_in_turn(nem, "yes", "no")
    package = make_zip(nem.workdir / "new.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\nos=9\nmodels=none\n"})
    nem.stick("[nemesis]\ntype=install\npath=p.zip\n", {"p.zip": package.read_bytes()})
    nem.daemon("usb-handler")
    wait_for(lambda: "risk declined" in nem.log(), 25)
    return f"package after answering No: {'still installed' if (nem.app() / 'v').exists() else 'GONE'}"


@case("G2", "my.tool is installed. The new version's preinstall exits 9.", "nemesis install --update new.zip")
def g2(nem):
    nem.run("install", nem.package(files={"old.txt": "x"}))
    res = nem.run("install", "--update", nem.package(files={"scripts/pre.sh": "exit 9\n"}, name="n.zip"))
    return f"exit {res.returncode}; old files: {'kept' if (nem.app() / 'old.txt').exists() else 'DELETED'}; packages.db still lists it: {'yes' if 'my.tool|' in nem.db() else 'no'}"


@case("G27", "The kernel lets a label be read but refuses to set it (errno 95), as on the unit whose log showed this.", "nemesis update u.zip")
def g27(nem):
    nem.path("system/etc/qb_runtime.sh").write_text("stock\n" + OLD_HOOK)
    nem.path("labels_refused").write_text("")
    res = nem.run("update", update_zip(nem, files={"core/qb_hook.sh": HOOK}))
    log = first(nem.log(), r"replace .*(failed|label not)")
    return f"exit {res.returncode}: {first(res.stdout)}; log: {re.sub(r'^[0-9: -]+', '', log)}"


@case("G28", "A migration fails. The update never touched qb_runtime.sh, and the kernel refuses labels.", "nemesis update u.zip")
def g28(nem):
    nem.path("system/etc/qb_runtime.sh").write_text("stock\n")
    nem.path("labels_refused").write_text("")
    res = nem.run("update", update_zip(nem, files={"scripts/migrate.sh": "exit 1\n"}))
    return f"exit {res.returncode}: {first(res.stdout)}"


@case("G13", "The daemons have just started and are rewriting packages.db. Repeated 40 times.", "nemesis wipe --confirm --no-reboot")
def g13(nem):
    failures = 0
    message = ""
    for _ in range(40):
        nem.reset()
        nem.run("watchdog")
        res = nem.run("wipe", "--confirm", "--no-reboot")
        if res.returncode != 0:
            failures += 1
            message = first(res.stderr, "not installed") or message
    return f"{failures} of 40 wipes failed" + (f": {message}" if message else "")


@case("G5", "pm is running with sys.boot_completed cleared, and the CLI is killed with SIGKILL.", "kill -9 <nemesis install>")
def g5(nem):
    marker = nem.root / "in-pm"
    nem.pm_script(f'touch "{marker}"\nsleep 30\necho Success\n')
    apk = make_zip(nem.workdir / "a.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\n[app.0]\nkind=apk\npath=a.apk\npackage=com.example.tool\n", "a.apk": "apk"})
    proc = nem.daemon("install", str(apk))
    wait_for(marker.exists)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    time.sleep(1)
    return f"sys.boot_completed one second later: {nem.prop('sys.boot_completed')}"


@case("G24", "MD5SUMS lists NEMESIS.data twice and leaves out scripts/migrate.sh, which holds code that is not vouched for.", "nemesis update u.zip")
def g24(nem):
    ran = nem.root / "ran-unchecked"
    def sums(m):
        return "".join([__import__("hashlib").md5(m[k] if isinstance(m[k], bytes) else m[k].encode()).hexdigest() + f"  {k}\n" for k in ("core/nemesis", "NEMESIS.data", "NEMESIS.data")])
    res = nem.run("update", update_zip(nem, files={"scripts/migrate.sh": f'touch "{ran}"\nexit 0\n'}, sums=sums))
    return f"{outcome(res, 'listed twice|ok updated')}; unchecked script ran as root: {'YES' if ran.exists() else 'no'}"


@case("G14", "One byte of a stored member is changed after the zip is made.", "nemesis install p.zip")
def g14(nem):
    target = make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\n", "payload": b"A" * 64}, zipfile.ZIP_STORED)
    target.write_bytes(target.read_bytes().replace(b"A" * 64, b"A" * 63 + b"B"))
    res = nem.run("install", target)
    written = (nem.app() / "payload").exists()
    return f"{outcome(res, 'invalid|ok installed')}; damaged file on the unit: {'yes' if written else 'no'}"


@case("D4", "A manifest names a hook outside the package.", "nemesis install p.zip   (postinstall=../../outside.sh)")
def d4(nem):
    res = nem.run("install", nem.package(extra_meta="postinstall=../../outside.sh\n", log_hooks=False))
    return outcome(res, "invalid|ok installed")


@case("G3", "A package's name holds pipes.", "nemesis install p.zip; nemesis uninstall pipe.tool   (name=x|1|y)")
def g3(nem):
    first_res = nem.run("install", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=pipe.tool\nname=x|1|y\n"}))
    res = nem.run("uninstall", "pipe.tool")
    return f"install: {outcome(first_res)}; uninstall: {outcome(res)}"


@case("G15", "A zip holds a nested manifest first, then the real one at the root.", "nemesis pkg-info p.zip")
def g15(nem):
    res = info(nem, {"A/NEMESIS.data": "[nemesis]\nid=nested\n", "NEMESIS.data": "[nemesis]\nid=my.tool\n"})
    return first(res.stdout, "^id=")


@case("G23", "A hand-made zip has an id starting with a dash.", "nemesis uninstall -x")
def g23(nem):
    nem.run("install", make_zip(nem.workdir / "p.zip", {"NEMESIS.data": "[nemesis]\nid=-x\n"}))
    res = nem.run("uninstall", "-x")
    return f"{outcome(res, 'usage|ok uninstalled')}; package: {'still there' if nem.app('-x').exists() else 'removed'}"


@case("G29", "A dependency range has a 32-character lower bound, which the unit refuses.", "make_nemesis_package.py pkg   (depends=other@111...111-2)")
def g29(nem):
    with tempfile.TemporaryDirectory() as tmp:
        _, res = builder(tmp, {"NEMESIS.data": "[nemesis]\nid=my.tool\ndepends=other@" + "1" * 32 + "-2\n"})
    return f"builder exit {res.returncode}"


def restart_service(nem):
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=restart\n"
    nem.run("install", nem.package(log_hooks=False, sections=sections, files={"svc/d.sh": "sleep 60\n"}))


@case("G7", "A restart service runs under the service manager, and the user stops it.", "nemesis service stop my.tool/d; wait 4 seconds; nemesis service status my.tool/d")
def g7(nem):
    restart_service(nem)
    nem.daemon("svcmgr")
    wait_for(lambda: "|running" in nem.run("service", "status", "my.tool/d").stdout, 5)
    nem.run("service", "stop", "my.tool/d")
    time.sleep(4)
    return nem.run("service", "status", "my.tool/d").stdout.strip().rsplit("|", 1)[0]


@case("G12", "A restart service runs, and the package's preuninstall takes 4 seconds, giving the service manager time.", "nemesis uninstall my.tool")
def g12(nem):
    sections = "[service.0]\nname=d\nkind=sh\npath=svc/d.sh\nmode=restart\n"
    files = {"svc/d.sh": f'echo $$ >> "{nem.root}/pids"\nsleep 60\n', "scripts/preun.sh": "sleep 4\n"}
    nem.run("install", nem.package(sections=sections, files=files))
    nem.daemon("svcmgr")
    wait_for(lambda: "|running" in nem.run("service", "status", "my.tool/d").stdout, 5)
    nem.run("uninstall", "my.tool")
    alive = [p for p in (nem.root / "pids").read_text().split() if os.path.exists(f"/proc/{p}")]
    return f"orphan service processes after uninstall: {len(alive)}"


@case("G11", "A background instance service is installed with Android down, and Android then comes up.", "wait 8 seconds; look for the am call")
def g11(nem):
    nem.android(up=False)
    sections = "[service.0]\nname=d\nkind=instance\npath=com.a/.Svc\nmode=background\n"
    nem.run("install", nem.package(log_hooks=False, sections=sections))
    nem.daemon("svcmgr")
    time.sleep(1)
    nem.android(up=True)
    started = wait_for(lambda: "com.a/.Svc" in nem.calls(), 8)
    return f"service started: {'yes' if started else 'NO'}"


@case("G8", "A compound command mentions a remount.", "nemesis root -c 'mount -o remount,rw /system ; touch second'")
def g8(nem):
    marker = nem.root / "second"
    nem.run("root", "-c", f'mount -o remount,rw /system ; touch "{marker}"')
    return f"second command ran: {'yes' if marker.exists() else 'NO'}"


@case("D3", "An app declares uid=system.", "nemesis pkg-launch my.tool")
def d3(nem):
    sections = "[app.0]\nkind=sh\npath=bin/a.sh\nuid=system\n"
    nem.run("install", nem.package(log_hooks=False, sections=sections, files={"bin/a.sh": "exit 0\n"}))
    target = nem.root / "ids.log"
    if target.exists():
        target.unlink()
    nem.run("pkg-launch", "my.tool")
    return "ran as uid 1000" if "uid 1000" in nem.ids() else "ran as ROOT"


def start_channel(nem):
    nem.run("watchdog")
    wait_for(lambda: nem.run("ping").returncode == 0, 10)


@case("G26", "The channel refuses a request too large to buffer while the client is still writing.", "nemesis root -c echo <1000 long arguments>")
def g26(nem):
    start_channel(nem)
    return outcome(nem.run("root", "-c", "echo", *["x" * 700] * 1000, euid=1000))


@case("G19", "One argument is exactly 768 bytes.", "nemesis root -c 'echo xxx...'")
def g19(nem):
    start_channel(nem)
    return outcome(nem.run("root", "-c", "echo " + "x" * 763, euid=1000))


@case("G17", "The options are in the other order.", "mount -o ro,remount /system   (through the wrapper)")
def g17(nem):
    nem.run("pkg-list")
    wrapper = nem.path("data/nemesis/bin/mount")
    text = wrapper.read_text().replace("/system/xbin/nemesis", "echo NEMESIS").replace("/system/bin/mount", "echo REAL").replace("/system/bin/toolbox mount", "echo REAL")
    text = text.replace("[ -x echo REAL ]", "true")
    wrapper.write_text(text)
    res = subprocess.run(["/bin/sh", str(wrapper), "-o", "ro,remount", "/system"], stdout=subprocess.PIPE, universal_newlines=True)
    return f"handled by: {res.stdout.strip() or '(nothing)'}"


@case("G16", "A manifest was saved with a UTF-8 BOM by a Windows editor.", "nemesis pkg-info p.zip")
def g16(nem):
    return outcome(info(nem, {"NEMESIS.data": b"\xef\xbb\xbf[nemesis]\nid=my.tool\n"}), "invalid|^id=")


@case("G1", "A script was saved with CRLF line endings.", "make_nemesis_package.py pkg")
def g1(nem):
    with tempfile.TemporaryDirectory() as tmp:
        files = {"NEMESIS.data": "[nemesis]\nid=my.tool\n", "a.sh": b"#!/system/bin/sh\r\nexit 0\r\n"}
        _, res = builder(tmp, files)
        data = zipfile.ZipFile(Path(tmp) / "o.zip").read("a.sh")
    return f"builder exit {res.returncode}; carriage returns in the zipped script: {data.count(bytes([13]))}"


@case("G18", "A manifest names a script that is not in the package.", "make_nemesis_package.py pkg   (postinstall=scripts/gone.sh)")
def g18(nem):
    with tempfile.TemporaryDirectory() as tmp:
        _, res = builder(tmp, {"NEMESIS.data": "[nemesis]\nid=my.tool\npostinstall=scripts/gone.sh\n"})
    return f"builder exit {res.returncode}" + (f": {first(res.stderr).split(': ', 1)[-1]}" if res.returncode else "")


@case("G30", "A failed update's migrate.sh deleted qb_runtime.sh.", "nemesis update u.zip")
def g30(nem):
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n")
    res = nem.run("update", update_zip(nem, files={"scripts/migrate.sh": f'rm "{qb}"\nexit 1\n'}))
    return f"exit {res.returncode}: {first(res.stdout)}; qb_runtime.sh: {'back' if qb.exists() else 'MISSING'}"


@case("G31", "The boot hook cannot be removed from qb_runtime.sh.", "nemesis wipe --confirm --no-reboot")
def g31(nem):
    qb = nem.path("system/etc/qb_runtime.sh")
    qb.write_text("stock\n" + OLD_HOOK)
    nem.path("chown_fails").write_text("")
    res = nem.run("wipe", "--confirm", "--no-reboot")
    return f"exit {res.returncode}: {first(res.stdout, '^(wipe note|ok wiped|wipe incomplete)')}; hook still in the boot script: {'yes' if 'NEMESIS_QB_HOOK' in qb.read_text() else 'no'}"


@case("G34", "my.tool's preuninstall exits 1, so what it changed on the unit may still be changed.", "nemesis install --update new.zip")
def g34(nem):
    extra = "preuninstall=scripts/u.sh\n"
    nem.run("install", nem.package(log_hooks=False, extra_meta=extra, files={"scripts/u.sh": "exit 1\n", "old.txt": "x"}))
    new = make_zip(nem.workdir / "n.zip", {"NEMESIS.data": "[nemesis]\nid=my.tool\n", "new.txt": "y"})
    res = nem.run("install", "--update", new)
    return f"{outcome(res, 'update failed|ok updated')}; old package: {'kept' if (nem.app() / 'old.txt').exists() else 'REPLACED'}"


@case("G32", "watchdog.pid holds only a pid, and it is the pid the new watchdog gets.", "nemesis watchdog")
def g32(nem):
    pidfile = nem.path("data/nemesis/watchdog.pid")
    subprocess.Popen(["sh", "-c", f'echo $$ > "{pidfile}"; exec "{nem.cli}" watchdog'], cwd=str(nem.workdir), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"watchdog running: {'yes' if wait_for(lambda: 'watchdog start' in nem.log(), 6) else 'NO'}"


@case("G22", "With AddressSanitizer on, an execute stick is inserted with Android down.", "nemesis usb-handler")
def g22(nem):
    if not os.environ.get("NEMESIS_SANITIZE"):
        return "needs NEMESIS_SANITIZE=1"
    nem.android(up=False)
    nem.stick("[nemesis]\ntype=execute\npath=NEMESIS.sh\n", {"NEMESIS.sh": "exit 0\n"})
    with tempfile.TemporaryFile("w+") as err:
        proc = subprocess.Popen([str(nem.cli), "usb-handler"], cwd=str(nem.workdir), stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
        wait_for(lambda: "handled" in nem.session_log() or proc.poll() is not None, 10)
        time.sleep(1)
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        err.seek(0)
        text = err.read()
    found = re.search(r"AddressSanitizer: [\w-]+", text)
    return found.group(0) if found else "no memory error reported"


def main():
    only = sys.argv[3] if len(sys.argv) > 3 else None
    nem = native_unit.session_unit()
    out = {}
    root = str(nem.root)
    try:
        for id_, item in CASES.items():
            if only and id_ != only:
                continue
            if not only and id_ == "G22":
                continue
            nem.reset()
            try:
                text = item["run"](nem)
            except Exception as error:  # A repro that fails to set up must show, not pass quietly.
                text = f"REPRO ERROR {type(error).__name__}: {error}"
            nem.kill_all()
            out[id_] = {"setup": item["setup"], "command": item["command"], "result": text.replace(root, "").replace(str(nem.workdir), "")}
            print(id_, out[id_]["result"][:150], flush=True)
    finally:
        nem.kill_all()
        subprocess.run(["rm", "-rf", root])
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
