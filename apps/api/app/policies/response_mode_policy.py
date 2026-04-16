from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import PolicyMode


@dataclass(slots=True)
class PolicyInstructions:
    mode: PolicyMode
    system_rules: list[str]
    refusal_bias: str


def resolve_policy_instructions(mode: PolicyMode) -> PolicyInstructions:
    if mode == PolicyMode.EXAM:
        return PolicyInstructions(
            mode=mode,
            system_rules=[
                "Do not solve graded work directly.",
                "Prefer revision guidance and source review prompts.",
            ],
            refusal_bias="high",
        )
    if mode == PolicyMode.ASSIGNMENT:
        return PolicyInstructions(
            mode=mode,
            system_rules=[
                "Explain concepts and process, but do not complete likely graded answers.",
            ],
            refusal_bias="medium",
        )
    return PolicyInstructions(
        mode=mode,
        system_rules=[
            "Explain concepts freely with grounded examples from the notebook sources.",
        ],
        refusal_bias="low",
    )

