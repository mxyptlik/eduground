# ADR 004: Prompt Versioning

## Status

Accepted

## Context

Tutor, quiz, citation, guardrail, and policy prompts are part of product behavior and need safe promotion.

## Decision

Store prompts as immutable versioned markdown files under `packages/prompts`.

## Consequences

- Prompt changes are reviewable and testable.
- Regression testing can pin exact versions.
- Behavior drift is easier to audit and roll back.

