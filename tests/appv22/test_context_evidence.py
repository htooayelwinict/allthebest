import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.context.evidence import ContextEvidence


def test_context_evidence_reads_raw_world_refs() -> None:
    prompt = {
        "world": {
            "world_refs": {
                "world://repo_snapshot/latest": {
                    "ref_id": "world://repo_snapshot/latest",
                    "kind": "file_management.repo_snapshot",
                    "summary": "file_management.repo_snapshot result",
                }
            }
        },
        "messages": [],
    }

    evidence = ContextEvidence.from_prompt(prompt)

    assert evidence.has_ref("world://repo_snapshot/latest")
    assert evidence.has_kind("file_management.repo_snapshot")
    assert evidence.refs == ("world://repo_snapshot/latest",)
    assert evidence.kinds == ("file_management.repo_snapshot",)


def test_context_evidence_reads_compacted_summary_refs() -> None:
    prompt = {
        "world": {"world_refs": {}},
        "messages": [
            {
                "role": "system",
                "name": "context_summary",
                "summary": {
                    "evidence_refs": ["world://repo_snapshot/latest"],
                    "progress": ["file_management.repo_snapshot result"],
                },
            }
        ],
    }

    evidence = ContextEvidence.from_prompt(prompt)

    assert evidence.has_ref("world://repo_snapshot/latest")
    assert not evidence.has_kind("file_management.repo_snapshot")


def test_context_evidence_deduplicates_refs_across_layers() -> None:
    prompt = {
        "world": {
            "world_refs": {
                "world://repo_snapshot/latest": {
                    "ref_id": "world://repo_snapshot/latest",
                    "kind": "file_management.repo_snapshot",
                }
            }
        },
        "messages": [
            {
                "role": "system",
                "name": "context_summary",
                "summary": {"evidence_refs": ["world://repo_snapshot/latest"]},
            }
        ],
    }

    evidence = ContextEvidence.from_prompt(prompt)

    assert evidence.refs == ("world://repo_snapshot/latest",)
    assert evidence.kinds == ("file_management.repo_snapshot",)


def test_context_evidence_ignores_malformed_summary_without_crashing() -> None:
    prompt = {
        "world": {"world_refs": "not-a-dict"},
        "messages": [
            {"role": "system", "name": "context_summary", "summary": "not-a-dict"},
            {"role": "system", "name": "context_summary", "summary": {"evidence_refs": "not-a-list"}},
        ],
        "state": {"context_summary": "not-a-dict"},
    }

    evidence = ContextEvidence.from_prompt(prompt)

    assert evidence.refs == ()
    assert evidence.kinds == ()
