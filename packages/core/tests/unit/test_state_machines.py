import pytest
from hypothesis import given
from hypothesis import strategies as st

from opagent_core.operation import (
    OP_TRANSITIONS,
    IllegalOpTransition,
    Operation,
    OpStatus,
    project_job_status,
    sweep_orphans,
)
from opagent_core.state_machine import (
    JOB_TRANSITIONS,
    PLAN_TRANSITIONS,
    IllegalTransition,
    JobMachine,
    JobStatus,
    PlanStatus,
    auto_pause_check,
    complete_without_output,
    plan_transition,
    retry_failed_items,
)
from opagent_testing.clock import FakeClock

ALL_JOB = [None, *JobStatus]


@pytest.mark.property
@pytest.mark.tc("CORE-SM-002")
@given(src=st.sampled_from(ALL_JOB), dst=st.sampled_from(list(JobStatus)))
def test_core_sm_002_illegal_job_transitions(src, dst):
    if (src, dst) in JOB_TRANSITIONS:
        return
    m = JobMachine(src)
    with pytest.raises(IllegalTransition):
        m.to(dst)
    assert m.status == src


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-003")
def test_core_sm_003_cancelling_waits_for_leases():
    m = JobMachine(JobStatus.running)
    m.to(JobStatus.cancelling)
    with pytest.raises(IllegalTransition):
        m.to(JobStatus.cancelled, active_leases=2)
    assert m.status == JobStatus.cancelling
    m.to(JobStatus.cancelled, active_leases=0)
    assert m.status == JobStatus.cancelled


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-004")
def test_core_sm_004_retry_resets_only_failed_items():
    items = {"a": "failed", "b": "done", "c": "cached", "d": "failed", "e": "skipped"}
    m = JobMachine(JobStatus.failed)
    out = retry_failed_items(m, items)
    assert m.status == JobStatus.queued
    assert out == {"a": "pending", "b": "done", "c": "cached", "d": "pending", "e": "skipped"}


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-005")
def test_core_sm_005_label_only_completion_discards_plan():
    m = JobMachine(JobStatus.awaiting_approval)
    plan = complete_without_output(m, {"status": "draft"})
    assert m.status == JobStatus.completed
    assert plan == {"status": "draft", "discarded": True}


OP_EDGES = [
    (None, "queued"), ("queued", "waiting"), ("waiting", "running"), ("queued", "running"), ("running", "waiting"),
    ("running", "pausing"), ("pausing", "paused"), ("paused", "queued"), ("running", "cancelling"),
    ("cancelling", "cancelled"), ("queued", "cancelled"), ("paused", "cancelled"), ("running", "succeeded"),
    ("running", "failed"), ("failed", "queued"), ("running", "interrupted"), ("interrupted", "queued"),
    ("interrupted", "cancelled"),
]


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-006")
def test_core_sm_006_operation_table_matches_06():
    assert {(a and OpStatus(a), OpStatus(b)) for a, b in OP_EDGES} == set(OP_TRANSITIONS)


@pytest.mark.property
@pytest.mark.tc("CORE-SM-006")
@given(src=st.sampled_from([None, *OpStatus]), dst=st.sampled_from(list(OpStatus)))
def test_core_sm_006_operation_complement_illegal(src, dst):
    op = Operation(id="o", type="scan", status=src)
    if (src, dst) in OP_TRANSITIONS:
        op.transition(dst)
        assert op.status == dst
    else:
        with pytest.raises(IllegalOpTransition):
            op.transition(dst)
    with pytest.raises(IllegalOpTransition):
        Operation(id="p", type="scan", status=OpStatus.paused).transition(OpStatus.running)


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-007")
def test_core_sm_007_heartbeat_timeout_interrupts():
    clk = FakeClock()
    op = Operation(id="o", type="job.label", status=OpStatus.running, heartbeat_at=clk.now())
    fresh = Operation(id="f", type="scan", status=OpStatus.running, heartbeat_at=clk.now())
    clk.advance(61)
    fresh.heartbeat_at = clk.now()
    events = sweep_orphans([op, fresh], clk.now())
    assert op.status == OpStatus.interrupted and fresh.status == OpStatus.running
    assert [(e.op_id, e.kind) for e in events] == [("o", "OWNER_LOST")]
    op.transition(OpStatus.queued)
    assert op.status == OpStatus.queued


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-008")
def test_core_sm_008_job_projection():
    op = Operation(id="j1", type="job.label", status=OpStatus.running)
    upd = project_job_status(op, JobStatus.awaiting_review)
    assert op.status == OpStatus.succeeded
    assert any(link["label_key"] == "operation.link.review" for link in op.result["links"])
    assert upd.new_operation is None
    upd2 = project_job_status(op, JobStatus.applying)
    assert upd2.new_operation is not None and upd2.new_operation.type == "output.apply"
    assert upd2.new_operation.status == OpStatus.queued and upd2.new_operation.parent_id is None


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-009")
def test_core_sm_009_output_plan_states():
    s = PlanStatus.draft
    for nxt in (PlanStatus.approved, PlanStatus.applying, PlanStatus.applied, PlanStatus.undone):
        s = plan_transition(s, nxt)
    assert s == PlanStatus.undone
    s = plan_transition(plan_transition(plan_transition(PlanStatus.draft, PlanStatus.approved), PlanStatus.applying),
                        PlanStatus.partially_applied)
    assert plan_transition(s, PlanStatus.undoing) == PlanStatus.undoing
    with pytest.raises(IllegalTransition):
        plan_transition(PlanStatus.draft, PlanStatus.applied)
    assert (PlanStatus.applying, PlanStatus.partially_applied) in PLAN_TRANSITIONS


@pytest.mark.unit
@pytest.mark.tc("CORE-SM-010")
def test_core_sm_010_error_rate_auto_pause():
    m = JobMachine(JobStatus.running)
    r = auto_pause_check(m, samples=51, failures=11)
    assert r == "PAUSED_ERROR_RATE" and m.status == JobStatus.paused
    m2 = JobMachine(JobStatus.running)
    assert auto_pause_check(m2, samples=50, failures=50) is None and m2.status == JobStatus.running
    m3 = JobMachine(JobStatus.running)
    assert auto_pause_check(m3, samples=100, failures=20) is None  # exactly 20% does not exceed


@pytest.mark.property
@pytest.mark.tc("CORE-SM-011")
@given(steps=st.lists(st.tuples(st.floats(0, 1), st.booleans()), min_size=1, max_size=30))
def test_core_sm_011_seq_and_display_fraction(steps):
    op = Operation(id="o", type="scan", status=OpStatus.running)
    last_seq, last_disp = op.seq, op.display_fraction
    for frac, is_retry in steps:
        op.update_progress(frac, regressed=is_retry)
        assert op.seq > last_seq
        if not is_retry:
            assert op.display_fraction >= last_disp
        else:
            assert op.regressed is True or op.display_fraction >= last_disp
        last_seq, last_disp = op.seq, op.display_fraction
