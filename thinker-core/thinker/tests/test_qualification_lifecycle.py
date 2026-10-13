"""Task scenarios for immutable leases, runtime drift and explicit requalification."""

from datetime import datetime, timedelta

import pytest

from thinker.qualification import describe_target, require_pass, run_suite
from thinker.qualification_state import assert_applicable, record_drift
from thinker.qualification_store import read
from thinker.tests.test_qualification import provider, setup


def qualify(thinker, call):
    return run_suite(
        thinker,
        call.call_id,
        mode="LIVE_PROBE",
        authorize_target=describe_target(thinker, call)["key"],
    )


def test_expiry_runtime_cannot_renew_and_history_immutable(tmp_path):
    thinker, call, _, ql = setup(tmp_path)
    with provider():
        first = qualify(thinker, call)
        before = read(thinker.qualification_root, "records", first["record_id"])
        thinker.chat_ql({**ql, "routing": {"call_id": call.call_id}})
        assert read(thinker.qualification_root, "records", first["record_id"]) == before
        expiry = datetime.fromisoformat(first["expires_at"])
        assert_applicable(
            thinker.qualification_root, first["target"], first, expiry - timedelta(microseconds=1)
        )
        with pytest.raises(ValueError, match="STALE"):
            assert_applicable(thinker.qualification_root, first["target"], first, expiry)
        with pytest.raises(ValueError, match="STALE"):
            assert_applicable(thinker.qualification_root, first["target"], {"status": "PASS"})


def test_runtime_quarantine_requires_fresh_authorized_suite(tmp_path):
    thinker, call, _, ql = setup(tmp_path)
    with provider():
        first = qualify(thinker, call)
    before = read(thinker.qualification_root, "records", first["record_id"])
    with provider("FAIL"):
        response = thinker.chat_ql({**ql, "routing": {"call_id": call.call_id}})
    assert response.qualification["application_status"] == "FAIL"
    with pytest.raises(ValueError, match="QUARANTINED"):
        require_pass(thinker, call)
    with provider() as http:
        with pytest.raises(ValueError, match="QUARANTINED"):
            thinker.chat_ql({**ql, "routing": {"call_id": call.call_id}})
        http.assert_not_called()
        fresh = qualify(thinker, call)
        assert require_pass(thinker, call)["record_id"] == fresh["record_id"]
    assert read(thinker.qualification_root, "records", first["record_id"]) == before
    # Drift arriving after a suite began invalidates even a newly written PASS.
    record_drift(
        thinker.qualification_root,
        first["target"],
        "SUSPECT",
        response.qualification["execution_ref"],
        "LATE_DRIFT",
    )
    with pytest.raises(ValueError, match="QUARANTINED"):
        require_pass(thinker, call)
    assert len(list((thinker.qualification_root / "records").glob("*.json"))) == 2
