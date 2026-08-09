from fivefold.agent_runtime import validate_agent_output_schemas
from fivefold.contracts import STAGE_ORDER, Stage
from fivefold.prompts import AGENT_DEFINITIONS, system_prompt


def test_exactly_five_unique_agents() -> None:
    assert len(AGENT_DEFINITIONS) == 5
    assert [item.stage for item in AGENT_DEFINITIONS] == STAGE_ORDER
    assert len({item.name for item in AGENT_DEFINITIONS}) == 5
    assert len({item.personality for item in AGENT_DEFINITIONS}) == 5
    assert len({item.output_model for item in AGENT_DEFINITIONS}) == 5


def test_every_prompt_contains_common_safety_boundary() -> None:
    for stage in Stage:
        prompt = system_prompt(stage)
        assert "Never contact a prospect" in prompt
        assert "purchase or associate a domain" in prompt
        assert "A human performs all real communication" in prompt


def test_all_five_agent_outputs_are_strict_json_schemas() -> None:
    validate_agent_output_schemas()
