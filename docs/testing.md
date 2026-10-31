# Running the tests

Everything here runs by hand on a host machine, with no head unit. There is no CI, so run the suite yourself before you share a change.

To add a test, see section 10 of [AGENTS.md](../AGENTS.md).

## Quick start

Use Linux, or WSL2 on Windows. Clone into the Linux filesystem, not `/mnt/c`, because Windows paths ignore case and can turn scripts into CRLF.

```sh
sudo apt-get install -y gcc zlib1g-dev mksh unzip python3-pytest python3-hypothesis python3-coverage gcovr
git clone https://github.com/xwtk/hmg_nemesis.git
cd hmg_nemesis
NEMESIS_REQUIRE_NATIVE=1 NEMESIS_TEST_SH=/usr/bin/mksh python3 -B -m pytest Sources/tests/unit
```

This takes a few minutes. Run it this way because:

- `NEMESIS_REQUIRE_NATIVE=1` makes a missing compiler a failure. Without it, the C tests skip and the run still looks clean.
- `NEMESIS_TEST_SH=/usr/bin/mksh` runs the hooks under the unit's shell.
- `-B` keeps Python from writing bytecode files.

To run less:

```sh
python3 -B -m pytest Sources/tests/unit/test_builder.py    # one file
python3 -B -m pytest Sources/tests/unit -k remount         # tests matching a word
```

## Reading the result

| Result | Meaning |
|---|---|
| `passed` | The behaviour holds. |
| `xfailed` | A known bug. The test states the correct behaviour, and the reason names the bug, such as `G8`. These do not fail the run. |
| `skipped` | The test could not run here. The reason says why. One test always skips unless you run under the sanitizers. |
| `XPASS(strict)`, shown as a failure | A known bug no longer happens. Remove its `known_bug` marker. |

## What needs what

| Tests | Needs | Without it |
|---|---|---|
| Builder, manifests, catalog, docs | Python 3.8+, pytest | Cannot run |
| Hooks, mount wrapper | A POSIX `sh`; `mksh` matches the unit | Skip |
| C tests (`test_native_*.py`) | Linux, `gcc`, zlib headers | Skip |
| Hook fault tests (`test_hook_faults.py`) | Linux or WSL, not Git Bash | Skip |
| Generated inputs (`test_native_properties.py`) | Hypothesis | Skip |

On Windows, run `python -B -m pytest Sources/tests/unit` from a Git Bash terminal, and the C and fault tests skip. From PowerShell the hook tests skip as well, because `sh` is not on `PATH`.

## Coverage

The target is 90% or better for each of the builder and the native code. Nothing enforces it, so read the numbers yourself. Hooks are not counted, because mutation testing checks them instead.

```sh
export NEMESIS_COVERAGE=1 NEMESIS_REQUIRE_NATIVE=1 NEMESIS_TEST_SH=/usr/bin/mksh
export NEMESIS_NATIVE_BUILD=$PWD/build-native
export COVERAGE_FILE=$PWD/.coverage COVERAGE_RCFILE=$PWD/Sources/tests/unit/.coveragerc
python3 -B -m pytest Sources/tests/unit
python3 -m coverage combine
python3 -m coverage report --show-missing
gcovr --root Sources/native --object-directory build-native build-native --txt
```

Delete `build-native` and the `.coverage*` files before you measure again. Otherwise the old counts carry over. Run the variables in a subshell or a new terminal, so they do not affect later runs.

## Memory checks

The sanitizers turn memory errors in the C code into crashes that a test can see, including in the fuzz of damaged zips.

```sh
mkdir -p "$HOME/san"
export NEMESIS_SANITIZE=1 NEMESIS_REQUIRE_NATIVE=1 NEMESIS_TEST_SH=/usr/bin/mksh
export ASAN_OPTIONS=abort_on_error=1:detect_leaks=0:log_path=$HOME/san/asan
export UBSAN_OPTIONS=abort_on_error=1:print_stacktrace=1:log_path=$HOME/san/ubsan
python3 -B -m pytest Sources/tests/unit/test_native_packages.py Sources/tests/unit/test_native_runtime.py Sources/tests/unit/test_native_properties.py
ls "$HOME/san"
```

Read the reports in `$HOME/san`, not only the test result. Until G22 is fixed, about 20 USB handler tests fail here, all from `usb.c:566`. Any report from a different place is a new bug.

## Mutation testing

`mutate.py` breaks the code one small change at a time, then checks that a test notices. A change no test notices has **survived**, which points to a missing test unless the change cannot affect behaviour.

Run it from the repo root. It works on throwaway copies, so your files do not change.

```sh
export NEMESIS_REQUIRE_NATIVE=1 NEMESIS_TEST_SH=/usr/bin/mksh
python3 -B Sources/tests/unit/mutate.py --only make_nemesis_package   # one target, a few seconds
python3 -B Sources/tests/unit/mutate.py --only scripts/ --jobs 3 --json hooks.json
python3 -B Sources/tests/unit/mutate.py --only Sources/native/ini.c --jobs 3 --json ini.json
```

| Option | Meaning |
|---|---|
| `--only TEXT` | Only targets whose path contains `TEXT`. |
| `--jobs N` | Mutants to run at once. The default is 4. |
| `--json FILE` | Writes every result to `FILE`, once the run finishes. |

It exits with 0 if everything was caught, 1 if anything survived, and 2 if the tests already fail before any change, in which case it writes no JSON. An `--only` that matches nothing also exits 0, so check the `N mutants` count on the first line.

- **Set `NEMESIS_REQUIRE_NATIVE=1` for C targets.** Otherwise a machine with no compiler skips the C tests, and every mutant appears to survive.
- **Allow plenty of time for C targets.** There are over 1,000 mutants, and a full run takes many hours. Run one file at a time, so an interruption loses at most that file. A PC restart or sleep interrupts the run.
- **A mutant can misbehave.** Each test run is limited to 256 MB per file and 450 seconds, and its leftover processes are killed afterwards. New processes also fail once your user has 3,000, so don't run this as root, where that limit doesn't apply.

Mutation testing is not part of the normal test run, because of how long it takes.

## Other checks

| Command | What it checks | Needs |
|---|---|---|
| `bash Sources/tests/pkg_live_snapshot.sh` | Install and uninstall hooks in a private root, across several scenarios | Linux with user namespaces (`unshare`), `python3`, `unzip`. Uses `mksh` if installed, otherwise `bash` |
| `python3 Sources/tests/setup_press_test.py` | The Manager's triple-press logic | A JDK that accepts `-source 1.7`; JDK 17 works |
| `python3 Sources/tests/check_manager_apk.py` | The built `NEMESIS.apk` | A built APK and `aapt` |

These cannot run from a plain checkout, because the files they need are not shipped:

- `Sources/tests/emu_nemesis_flow.py` needs a QEMU image of the unit.
- `Sources/tests/pkg_carplay_session.sh` needs stock firmware files.

Behaviour that only hardware can prove is listed in [DEVICE_TEST.md](../Sources/usb_bundle/DEVICE_TEST.md).

## Settings

| Variable | Effect |
|---|---|
| `NEMESIS_REQUIRE_NATIVE=1` | Fails instead of skipping when the compiler or zlib headers are missing |
| `NEMESIS_TEST_SH=PATH` | The shell for running hooks. The default is `mksh` if installed, otherwise `sh` |
| `NEMESIS_COVERAGE=1` | Builds the C code and runs the builder with coverage on |
| `NEMESIS_NATIVE_BUILD=DIR` | Keeps the C build in `DIR`, which coverage needs. The default is a temp directory |
| `NEMESIS_SANITIZE=1` | Builds the C code with ASan and UBSan |
| `NEMESIS_PROP_SEED=N` | Replays a failure in `test_properties.py` |
| `CC=NAME` | The compiler for the C tests. The default is `gcc` |
