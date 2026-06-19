"""AgentSession composition root. Port of pi coding-agent core/sdk.ts + agent-session.ts (subset)."""

from __future__ import annotations

from typing import Callable, Optional

from appv22.agent.agent import Agent
from appv22.agent.types import AgentMessage
from appv22.ai.types import Message, Model
from appv22.coding_agent.system_prompt import BuildSystemPromptOptions, build_system_prompt
from appv22.coding_agent.tools import create_all_tools, create_all_tool_definitions


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Pass through ai Messages; drop UI-only/custom messages (pi convert_to_llm boundary)."""
    out: list[Message] = []
    for message in messages:
        if getattr(message, "role", None) in ("user", "assistant", "toolResult"):
            out.append(message)
    return out


class AgentSession:
    """Wires an agent.Agent with coding tools + a built system prompt."""

    def __init__(
        self,
        *,
        cwd: str,
        model: Model,
        tools=None,
        tool_definitions=None,
        convert_to_llm: Optional[Callable[[list[AgentMessage]], list[Message]]] = None,
        custom_prompt: str | None = None,
        append_system_prompt: str | None = None,
        transform_context=None,
    ) -> None:
        self.cwd = cwd
        self._tools = tools if tools is not None else create_all_tools(cwd)
        self._tool_definitions = tool_definitions if tool_definitions is not None else create_all_tool_definitions(cwd)
        self._convert_to_llm = convert_to_llm or default_convert_to_llm
        self._custom_prompt = custom_prompt
        self._append_system_prompt = append_system_prompt
        self.system_prompt = self._build_system_prompt()
        self.agent = Agent(
            system_prompt=self.system_prompt,
            model=model,
            convert_to_llm=self._convert_to_llm,
            tools=self._tools,
            transform_context=transform_context,
        )

    def _build_system_prompt(self) -> str:
        snippets = {d.name: d.prompt_snippet for d in self._tool_definitions if d.prompt_snippet}
        guidelines: list[str] = []
        for definition in self._tool_definitions:
            guidelines.extend(definition.prompt_guidelines)
        return build_system_prompt(
            BuildSystemPromptOptions(
                cwd=self.cwd,
                custom_prompt=self._custom_prompt,
                selected_tools=[d.name for d in self._tool_definitions],
                tool_snippets=snippets,
                prompt_guidelines=guidelines,
                append_system_prompt=self._append_system_prompt,
            )
        )

    def prompt(self, text: str, stream_fn=None) -> list[AgentMessage]:
        return self.agent.prompt(text, stream_fn=stream_fn)

    @property
    def messages(self) -> list[AgentMessage]:
        return self.agent.state.messages


def create_agent_session(
    *,
    cwd: str,
    model: Model,
    tools=None,
    convert_to_llm: Optional[Callable[[list[AgentMessage]], list[Message]]] = None,
) -> AgentSession:
    return AgentSession(cwd=cwd, model=model, tools=tools, convert_to_llm=convert_to_llm)
