"""mutate.py --diff, which limits a run to the lines a change touched (docs/testing.md)."""
from __future__ import annotations

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


def test_a_diff_selects_exactly_the_added_and_changed_lines():
    """Given a -U0 diff with a changed range, a one-line change, a deletion and a deleted file
    When its lines are read
    Then only the new file's added and changed lines are selected
    """
    file = "Sources/native/ini.c"
    assert mutate.changed_lines(DIFF) == {(file, 10), (file, 11), (file, 12), (file, 41)}
