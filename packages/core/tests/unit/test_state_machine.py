import pytest
from opagent_core.state_machine import JOB_TRANSITIONS, transition
from opagent_core.state_machine import JobStatus as S

# docs/02 §4.2 — all 19 edges (including creation [*] -> queued)
EDGES = [
    (None, S.queued),
    (S.queued, S.running),
    (S.running, S.paused),
    (S.paused, S.running),
    (S.running, S.awaiting_review),
    (S.awaiting_review, S.planning),
    (S.running, S.planning),
    (S.planning, S.awaiting_approval),
    (S.awaiting_approval, S.applying),
    (S.applying, S.completed),
    (S.awaiting_approval, S.completed),
    (S.running, S.failed),
    (S.queued, S.cancelled),
    (S.running, S.cancelling),
    (S.paused, S.cancelled),
    (S.cancelling, S.cancelled),
    (S.completed, S.undoing),
    (S.undoing, S.undone),
    (S.failed, S.queued),
]


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-001")
def test_core_sm_001_edge_table_matches_spec():
    assert len(EDGES) == 19
    assert set(EDGES) == set(JOB_TRANSITIONS)


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-001")
@pytest.mark.parametrize(("src", "dst"), EDGES)
def test_core_sm_001_legal_transition_emits_job_status(src, dst):
    ev = transition(src, dst)
    assert ev.kind == "job.status"
    assert (ev.from_status, ev.to_status) == (src, dst)
