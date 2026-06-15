from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.2"))

from scripts.live_appv22_complex_vague_file_management_probe import build_report


def test_probe_report_contains_full_matrix(tmp_path):
    (tmp_path / "README.md").write_text("# probe\n", encoding="utf-8")
    result = {
        "status": "completed",
        "events": [
            {"event_type": "DecisionProposed", "payload": {"kind": "tool_call"}},
            {
                "event_type": "ToolCallCompleted",
                "payload": {"tool_id": "file_management.repo_snapshot"},
            },
            {
                "event_type": "MutationApplied",
                "payload": {"receipt_id": "mut_workspace_cleanup"},
            },
            {
                "event_type": "VerificationRecorded",
                "payload": {"verification_id": "verify_1"},
            },
        ],
    }

    report = build_report(
        repo=tmp_path,
        result=result,
        provider=None,
        prompt="Can you clean this mess up safely and keep a record?",
    )

    assert report["status"] == "completed"
    assert report["user_prompt"] == "Can you clean this mess up safely and keep a record?"
    assert report["provider"] is None
    assert report["totals"]["events"] == 4
    assert report["totals"]["decisions"] == 1
    assert report["totals"]["tool_calls"] == 1
    assert report["totals"]["mutation_receipts"] == 1
    assert report["totals"]["verification_receipts"] == 1
    assert report["costs"] == {"model_calls": 0, "total_tokens": 0, "cost": 0.0}
    assert report["event_order"] == [
        "DecisionProposed",
        "ToolCallCompleted",
        "MutationApplied",
        "VerificationRecorded",
    ]
    assert report["files"] == ["README.md"]
