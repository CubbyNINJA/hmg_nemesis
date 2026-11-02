"""mutate.py --diff, which limits a run to the lines a change touched (docs/testing.md)."""
from __future__ import annotations

import os
import subprocess

import pytest

import mutate

DIFF = """diff --git a/Sources/native/ini.c b/Sources/native/ini.c
--- a/Sources/native/ini.c
+++ b/Sources/native/ini.c
@@ -10,2 +10,3 @@ context
-old
+new
+new
+new
@@ -40 +41 @@
-old
+new
@@ -50,3 +52,0 @@
-gone
-gone
-gone
diff --git a/Sources/native/gone.c b/Sources/native/gone.c
--- a/Sources/native/gone.c
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
-gone
"""


@pytest.mark.skipif(os.name != "posix" or not mutate.isolation_works(), reason="needs user and PID namespaces")
def test_a_mutant_cannot_signal_anything_outside_its_run():
    """Given a process started the way mutate.py starts a mutant's tests
    When it tries to signal this process (a mutant of stop_daemons once killed a whole session)
    Then it cannot reach it, but it still runs as this user, so permission tests stay valid
    """
    script = f'kill -0 {os.getpid()} 2>/dev/null && echo reached; id -u'
    res = subprocess.run(mutate.isolated(["sh", "-c", script]), stdout=subprocess.PIPE, universal_newlines=True)
    assert res.stdout.split() == [str(os.getuid())]


def test_a_diff_selects_exactly_the_added_and_changed_lines():
    """Given a -U0 diff with a changed range, a one-line change, a deletion and a deleted file
    When its lines are read
    Then only the new file's added and changed lines are selected
    """
    file = "Sources/native/ini.c"
    assert mutate.changed_lines(DIFF) == {(file, 10), (file, 11), (file, 12), (file, 41)}
