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


def test_context_evidence_falls_back_to_world_ref_key() -> None:
    evidence = ContextEvidence.from_prompt(
        {
            "world": {"world_refs": {"world://fallback/latest": {"kind": "fallback.kind"}}},
            "messages": [],
        }
    )

    assert evidence.refs == ("world://fallback/latest",)
    assert evidence.kinds == ("fallback.kind",)


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


def test_context_evidence_reads_state_context_summary_refs() -> None:
    evidence = ContextEvidence.from_prompt(
        {
            "world": {"world_refs": {}},
            "messages": [],
            "state": {"context_summary": {"evidence_refs": ["world://state/latest"]}},
        }
    )

    assert evidence.refs == ("world://state/latest",)
    assert evidence.kinds == ()


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


def test_context_evidence_ignores_non_dict_prompt_without_crashing() -> None:
    assert ContextEvidence.from_prompt(None).refs == ()
    assert ContextEvidence.from_prompt(["not", "a", "prompt"]).refs == ()
    assert ContextEvidence.from_prompt("not-a-prompt").refs == ()
