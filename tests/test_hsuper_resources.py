"""Resource rollback: a failure mid-topology must not leave half-built panes.

``create_resources`` builds workspace → two tabs → worker splits.  If any
step after the workspace fails, everything created so far is closed again —
the repo rule is that a failed launch owns its cleanup.
"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hsuper  # noqa: E402


class FakeHerdr:
    """Records every call and can fail on demand.

    Success responses carry the IDs the real CLI would return; a queued
    failure raises the UsageError the wrapper raises for a non-zero exit.
    """

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or set()
        self.counter = 0

    def _id(self, prefix):
        self.counter += 1
        return "%s%d" % (prefix, self.counter)

    def _maybe_fail(self, *args):
        self.calls.append(list(args))
        if tuple(args[:2]) in self.fail_on:
            raise hsuper.UsageError("herdr %s 失败: injected" % " ".join(args))

    def create_workspace(self, cwd, label):
        self._maybe_fail("workspace", "create")
        return "wT", self._id("p"), "t1"

    def create_tab(self, workspace_id, cwd, label):
        self._maybe_fail("tab", "create")
        return self._id("t"), self._id("p")

    def rename_pane(self, pane_id, label):
        self._maybe_fail("pane", "rename")

    def split_pane(self, parent_pane, direction, ratio=None):
        self._maybe_fail("pane", "split")
        return self._id("p")

    def close_pane(self, pane_id):
        self.calls.append(["pane", "close", pane_id])

    def close_tab(self, tab_id):
        self.calls.append(["tab", "close", tab_id])

    def close_workspace(self, workspace_id):
        self.calls.append(["workspace", "close", workspace_id])

    def calls_for(self, *words):
        return [c for c in self.calls if c[:len(words)] == list(words)]


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.state = hsuper.State.create(self.folder / "state.json", "r1",
                                         "sample", "任务", self.folder)

    def tearDown(self):
        self.directory.cleanup()

    def create(self, fake, plan=("ROOT", "down", 2)):
        with mock.patch.object(hsuper, "layout_plan",
                               return_value=(2, [plan] if plan else [])):
            return hsuper.create_resources(fake, self.state, self.folder,
                                           "sample", None, 1, "11")

    def test_success_records_the_ids(self):
        fake = FakeHerdr()
        workspace_id = self.create(fake)
        self.assertEqual(workspace_id, "wT")
        self.assertEqual(self.state.data["workspace_id"], "wT")
        self.assertEqual(len(fake.calls_for("tab", "create")), 2)

    def test_tab_failure_closes_the_workspace(self):
        fake = FakeHerdr(fail_on={("tab", "create")})
        workspace_id = self.create(fake)
        self.assertIsNone(workspace_id)
        self.assertEqual(fake.calls_for("workspace", "close"), [["workspace", "close", "wT"]])
        self.assertIsNone(self.state.data.get("workspace_id"))

    def test_split_failure_closes_panes_tabs_and_workspace(self):
        fake = FakeHerdr(fail_on={("pane", "split")})
        workspace_id = self.create(fake)
        self.assertIsNone(workspace_id)
        self.assertEqual(fake.calls_for("workspace", "close"), [["workspace", "close", "wT"]])
        # The supervisor pane was renamed before the split failed, and the
        # workers tab existed; every pane created before the failure is closed.
        self.assertTrue(fake.calls_for("pane", "rename"))
        created_tabs = [c for c in fake.calls if c[:2] == ["tab", "create"]]
        closed_tabs = fake.calls_for("tab", "close")
        self.assertEqual(len(closed_tabs), len(created_tabs))
        self.assertIsNone(self.state.data.get("workspace_id"))


if __name__ == "__main__":
    unittest.main()
