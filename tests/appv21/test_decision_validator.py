import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.runtime import rejections


def test_rejection_constants_are_stable_strings() -> None:
    assert rejections.MISSING_EVIDENCE == "missing_evidence"
    assert rejections.UNSUPPORTED_DECISION == "unsupported_decision"
    assert rejections.UNSAFE_TOOL == "unsafe_tool"
    assert rejections.INVALID_MUTATION == "invalid_mutation"
    assert rejections.STALE_PLAN == "stale_plan"
    assert rejections.VERIFICATION_FAILED == "verification_failed"
    assert rejections.REPEATED_LOOP == "repeated_loop"
