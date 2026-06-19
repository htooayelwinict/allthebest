"""Integrated pi+hermes coding app: ai + agent + coding_agent + compaction + tui.

Capstone composition that wires the ported parity packages into one end-to-end
application, with no imports of external source packages.
"""

from __future__ import annotations

from typing import Optional

from appv22.ai.types import Model
from appv22.coding_agent.agent_session import AgentSession
from appv22.compaction.compressor import ContextCompressor
from appv22.compaction.timing import CompactionManager
from appv22.tui.interactive import InteractiveRenderer
from appv22.tui.terminal import ProcessTerminal, Terminal
from appv22.tui.tui import TUI


class CodingApp:
    """End-to-end app: AgentSession + hermes compaction (preflight) + tui rendering."""

    def __init__(
        self,
        *,
        cwd: str,
        model: Model,
        terminal: Optional[Terminal] = None,
        context_length: int = 32000,
        summarizer=None,
    ) -> None:
        self.cwd = cwd
        self.compressor = ContextCompressor(context_length=context_length, summarizer=summarizer)
        self.compaction = CompactionManager(self.compressor, summarizer=summarizer)
        self.session = AgentSession(
            cwd=cwd,
            model=model,
            transform_context=self._transform_context,
        )
        self.terminal = terminal or ProcessTerminal()
        self.tui = TUI(self.terminal)
        self.renderer = InteractiveRenderer(self.tui)
        self.session.agent.subscribe(self.renderer.handle_event)

    def _transform_context(self, messages, signal=None):
        # Hermes preflight timing-compaction phase.
        return self.compaction.maybe_compress_preflight(messages)

    def run_turn(self, prompt: str, stream_fn=None):
        return self.session.prompt(prompt, stream_fn=stream_fn)

    @property
    def messages(self):
        return self.session.messages
