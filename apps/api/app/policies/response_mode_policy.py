from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import PolicyMode
from app.services.prompt_library import load_policy_prompt


@dataclass(slots=True)
class PolicyInstructions:
    mode: PolicyMode
    system_rules: list[str]
    refusal_bias: str


def resolve_policy_instructions(mode: PolicyMode) -> PolicyInstructions:
    policy_prompt = load_policy_prompt(mode)
    if mode == PolicyMode.EXAM:
        return PolicyInstructions(
            mode=mode,
            system_rules=[policy_prompt.text],
            refusal_bias="high",
        )
    if mode == PolicyMode.ASSIGNMENT:
        return PolicyInstructions(
            mode=mode,
            system_rules=[policy_prompt.text],
            refusal_bias="medium",
        )
    return PolicyInstructions(
        mode=mode,
        system_rules=[policy_prompt.text],
        refusal_bias="low",
    )
