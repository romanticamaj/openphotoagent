"""Q-030 (回歸): a stopped worker lets go of its database files.

Windows cannot delete a file that is still open. The journal and the scan cache stayed open for the life of the
object, so anything that stops a worker and then touches its folder failed with WinError 32 (all 83 e2e cases, whose
per-test reset removes it). A worker's process exiting releases them; an in-process stop has to do it itself."""

import shutil

import pytest
from opagent_worker.agent import WorkerAgent


@pytest.mark.unit
@pytest.mark.tc("Q-030")
def test_q_030_close_releases_the_worker_folder(sandbox_tree, tmp_path):
    home = tmp_path / "w"
    w = WorkerAgent(object(), roots=sandbox_tree.roots, home=home, worker_id="w1")
    w.close()
    w.close()  # stopping twice is not an error
    shutil.rmtree(home)
    assert not home.exists()
