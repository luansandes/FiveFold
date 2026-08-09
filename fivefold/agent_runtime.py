from __future__ import annotations

import json
from typing import Any

from fivefold.audit import content_hash
from fivefold.config import Settings
from fivefold.contracts import Envelope, Stage
from fivefold.prompts import AGENTS_BY_STAGE, system_prompt
from fivefold.site_builder import sanitize_site, validate_site


class AgentRuntimeError(RuntimeError):
    pass


def validate_agent_output_schemas() -> None:
    """Fail before a live call if any of the five output contracts is not strict."""
    from agents.agent_output import AgentOutputSchema

    for definition in AGENTS_BY_STAGE.values():
        AgentOutputSchema(definition.output_model, strict_json_schema=True).json_schema()


class AgentRuntime:
    """Runs the five configured model agents against the live OpenAI connection."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(
        self,
        stage: Stage,
        prospect: dict[str, Any],
        inputs: dict[str, dict[str, Any]],
        attempt: int,
        revision_feedback: str | None,
    ) -> Envelope:
        context = {
            "prospect": prospect,
            "upstream_artifacts": inputs,
            "attempt": attempt,
            "revision_feedback": revision_feedback,
            "constraints": {
                "location": "Dublin, Ireland",
                "testimonial_policy": "themes and placeholders only",
                "outreach": "human only",
                "domain_operations": "research only; never purchase or associate",
            },
        }
        if not self.settings.openai_api_key:
            raise AgentRuntimeError("OPENAI_API_KEY is required for agent execution")
        try:
            from agents import Agent, ModelSettings, Runner

            definition = AGENTS_BY_STAGE[stage]
            agent = Agent(
                name=f"{definition.archetype}: {definition.name}",
                instructions=system_prompt(stage),
                model=self.settings.openai_model,
                output_type=definition.output_model,
                model_settings=ModelSettings(reasoning={"effort": "medium"}),
            )
            result = await Runner.run(
                agent,
                "Complete your assigned stage from this trusted application context. "
                "Return only the configured structured output.\n\n"
                + json.dumps(context, sort_keys=True, default=str),
                max_turns=6,
            )
            output = result.final_output
            if stage == Stage.MAKER:
                sanitize_site(output.artifact.html, output.artifact.css)
                output.artifact.validation = validate_site(output.artifact.html, output.artifact.css)
                output.artifact.artefact_hash = content_hash(
                    output.artifact.html + output.artifact.css
                )
            return output
        except Exception as exc:  # the workflow applies bounded retry policy
            raise AgentRuntimeError(f"Agent {stage.value} failed: {exc}") from exc
