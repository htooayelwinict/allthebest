from __future__ import annotations

from pathlib import Path
import queue
import tempfile
import unittest

from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.extensions.file_management.tools import mkdir, repo_snapshot, write_file
from appv22.runtime.agent_loop import AppV22AgentRuntime
from appv22.runtime.services import AppV22Services
from appv22.runtime.services import create_appv22_services
from appv22.state.models import AgentState, RequestEnvelope
from appv22.tools.broker import ToolBroker
from appv22.tools.definitions import ToolDefinition
from appv22.tools.registry import ToolRegistry
from appv22_ui.session import SessionStore
from appv22_ui.tui_app import AppV22Tui


class RuntimeProtectionTests(unittest.TestCase):
    def test_repo_snapshot_skips_symlinked_files_and_exact_protected_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            outside_secret = Path(outside) / "secret.txt"
            outside_secret.write_text("external secret", encoding="utf-8")
            (root / "safe.txt").write_text("safe", encoding="utf-8")
            (root / "secrets").write_text("local secret", encoding="utf-8")
            (root / "linked.txt").symlink_to(outside_secret)

            result = repo_snapshot({}, {"root_path": root})

        self.assertEqual(result["status"], "completed")
        self.assertIn("safe.txt", result["files"])
        self.assertNotIn("secrets", result["files"])
        self.assertNotIn("linked.txt", result["files"])
        self.assertNotIn("secrets", result["text_previews"])
        self.assertNotIn("linked.txt", result["text_previews"])

    def test_exact_protected_names_are_denied_for_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = mkdir({"path": "secrets"}, {"root_path": root})

        self.assertEqual(result["status"], "denied")
        self.assertIn("protected_path:secrets", result["errors"])

    def test_overwrite_policy_uses_active_request_not_reference_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("old", encoding="utf-8")
            context = {
                "root_path": root,
                "request": {
                    "user_goal": "[UI SESSION SUMMARY]\nUser previously said do not overwrite.\n[CURRENT USER REQUEST]\noverwrite note.txt",
                    "active_user_request": "overwrite note.txt",
                },
            }

            result = write_file({"path": "note.txt", "content": "new", "overwrite": True}, context)

            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["overwritten"])
            self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "new")

    def test_payload_ref_includes_arguments_to_avoid_semantic_collisions(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                "test.same_payload",
                "act",
                "low",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                {"type": "object", "properties": {"bytes_written": {"type": "integer"}}, "required": ["bytes_written"]},
                "test",
                "test",
            ),
            lambda _args, _context: {"status": "completed", "bytes_written": 3},
        )
        broker = ToolBroker(registry=registry, root_path=".")

        first = broker.execute("test.same_payload", {"path": "a.txt"}, active_tool_ids={"test.same_payload"})
        second = broker.execute("test.same_payload", {"path": "b.txt"}, active_tool_ids={"test.same_payload"})

        self.assertNotEqual(first["payload_ref"], second["payload_ref"])

    def test_failed_tool_results_emit_failed_event_and_reduce_into_state(self) -> None:
        captured: list[dict] = []
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services(),
            event_sink=captured.append,
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "test", "."))

        runtime._record_tool_result(
            state,
            {
                "tool_result_id": "toolres_failed",
                "tool_id": "test.tool",
                "status": "failed",
                "payload": {"errors": ["boom"]},
                "payload_ref": "",
                "evidence_refs": [],
                "arguments": {},
            },
        )

        self.assertEqual(captured[-1]["event_type"], "ToolCallFailed")
        self.assertIn("toolres_failed", state.tool_results)

    def test_tui_previous_result_uses_persisted_session_for_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_old",
                    "world_refs": {"world://file_management.repo_snapshot/latest": {"summary": "snapshot"}},
                    "context_summary": {"progress": ["snapshot"]},
                },
                conversation=[],
            )

            previous = app._previous_result()

        self.assertIsInstance(previous, dict)
        self.assertEqual(previous["session_id"], "sess_old")
        self.assertIn("world://file_management.repo_snapshot/latest", previous["world_refs"])

    def test_tui_interrupted_turn_does_not_persist_late_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.state.running = False
            app.state.mode = "INTERRUPTED"
            app.state.add_notice("turn interrupted")
            events: queue.Queue[tuple[str, object]] = queue.Queue()
            events.put(
                (
                    "result",
                    {
                        "status": "completed",
                        "reason": "tool_loop_completed",
                        "session_id": "sess_late",
                        "world_refs": {},
                        "context_summary": {},
                    },
                )
            )

            app._drain_events(events)

            self.assertIsNone(SessionStore(Path(tmp)).load())
            self.assertEqual(app.state.mode, "INTERRUPTED")
            self.assertIn("ignored", app.state.notice)

    def test_read_prompt_selects_read_tool_and_drops_stale_file_read_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output.md").write_text("hello", encoding="utf-8")
            provider = _CaptureProvider()
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=1)

            runtime.continue_run(
                {
                    "session_id": "sess_old",
                    "world_refs": {},
                    "context_summary": {
                        "open_risks": [
                            "file.read reported error: inactive_tool:file.read",
                            "file.read request was denied for argument keys ['path']; treat that denial as evidence.",
                        ]
                    },
                },
                "[CURRENT USER REQUEST]\nread that output.md",
                active_user_request="read that output.md",
            )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertEqual(provider.prompt["state"]["context_summary"]["open_risks"], [])

    def test_continue_run_resolves_open_risks_from_existing_world_refs_before_prompt(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {
                    "world://file_management.read_file/ok": {
                        "kind": "file_management.read_file",
                        "summary": "file_management.read_file result",
                    }
                },
                "context_summary": {
                    "open_risks": [
                        "file_management.read_file reported error: missing_file:cat.txt",
                        "file_management.read_file request was failed for argument keys ['path']; treat that failure as evidence.",
                    ],
                    "progress": ["file_management.read_file: file_management.read_file result"],
                },
            },
            "list files",
            active_user_request="list files",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertEqual(provider.prompt["state"]["context_summary"]["open_risks"], [])
        self.assertIn(
            "file_management.read_file: prior failed/denied tool risk resolved by later successful result",
            provider.prompt["state"]["context_summary"]["progress"],
        )

    def test_successful_tool_result_resolves_prior_same_tool_open_risks(self) -> None:
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services(),
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "test", "."))
        state.context_summary = {
            "open_risks": [
                "file_management.read_file reported error: missing_file:cat.txt",
                "file_management.read_file request was failed for argument keys ['path']; treat that failure as evidence.",
                "other_tool reported error: still_active",
            ],
            "progress": [],
        }

        runtime._record_tool_result(
            state,
            {
                "tool_result_id": "toolres_read",
                "tool_id": "file_management.read_file",
                "status": "completed",
                "payload": {"content": "cat", "bytes_read": 3, "path": "cat_poem.txt"},
                "payload_ref": "world://file_management.read_file/test",
                "evidence_refs": ["world://file_management.read_file/test"],
                "arguments": {"path": "cat_poem.txt"},
            },
        )

        self.assertEqual(state.context_summary["open_risks"], ["other_tool reported error: still_active"])
        self.assertIn(
            "file_management.read_file: prior failed/denied tool risk resolved by later successful result",
            state.context_summary["progress"],
        )

    def test_payloadless_observe_world_ref_does_not_suppress_rehydration(self) -> None:
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services_with_registry(),
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "list files", "."))
        state.world_refs = {
            "world://file_management.repo_snapshot/latest": {
                "kind": "file_management.repo_snapshot",
                "arguments": {},
                "summary": "file_management.repo_snapshot result",
            }
        }

        exists = runtime._tool_call_evidence_already_exists(state, "file_management.repo_snapshot", {})

        self.assertFalse(exists)

    def test_malformed_tool_call_guidance_is_progress_not_persisted_open_risk(self) -> None:
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services(),
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "test", "."))
        decision = type("Decision", (), {"payload": {}, "kind": "tool_call"})()
        resolved = type("Resolved", (), {"tool_ids": ("file_management.read_file",)})()

        runtime._handle_tool_call(state, decision, resolved)

        self.assertEqual(state.context_summary["open_risks"], [])
        self.assertIn(
            "Malformed tool_call decision was missing payload.tool_id; treated as turn-local provider repair feedback. Continue from selected tools or existing evidence.",
            state.context_summary["progress"],
        )


def _unused_services() -> AppV22Services:
    return AppV22Services(
        root_path=Path("."),
        provider=object(),
        extension_registry=object(),
        tool_registry=object(),
        broker=object(),
        context_selector=object(),
        prompt_builder=object(),
        gateway_guard=object(),
        compressor=object(),
    )


def _unused_services_with_registry() -> AppV22Services:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "file_management.repo_snapshot",
            "observe",
            "low",
            {"type": "object", "properties": {}},
            {"type": "object", "properties": {}},
            "test",
            "test",
        ),
        lambda _args, _context: {"status": "completed"},
    )
    return AppV22Services(
        root_path=Path("."),
        provider=object(),
        extension_registry=object(),
        tool_registry=registry,
        broker=object(),
        context_selector=object(),
        prompt_builder=object(),
        gateway_guard=object(),
        compressor=object(),
    )


class _CaptureProvider:
    def __init__(self) -> None:
        self.prompt = None

    def decide(self, prompt):
        self.prompt = prompt
        return {"kind": "finalize", "payload": {"assistant_message": "captured"}}


if __name__ == "__main__":
    unittest.main()
