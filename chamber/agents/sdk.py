"""OpenAI Agents SDK adapter for evidence-bound agent execution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TypeVar

from agents import Agent, Runner
from chamber.agents.contracts import AgentValidationError

T = TypeVar("T")


@dataclass(frozen=True)
class OpenAIAgentsSdkRunner:
    """Thin runtime boundary around the OpenAI Agents SDK."""

    model: str = "gpt-5.4-mini"

    def run_structured(
        self,
        *,
        name: str,
        instructions: str,
        input_text: str,
        output_type: type[T],
    ) -> T:
        """Run one structured-output agent call."""

        if not os.environ.get("OPENAI_API_KEY"):
            raise AgentValidationError("OPENAI_API_KEY is required for live agent execution")

        agent = Agent(
            name=name,
            instructions=instructions,
            model=self.model,
            output_type=output_type,
        )
        result = Runner.run_sync(agent, input_text)
        final_output = result.final_output
        if not isinstance(final_output, output_type):
            raise AgentValidationError(
                f"agent {name!r} returned {type(final_output).__name__}, "
                f"expected {output_type.__name__}"
            )
        return final_output
