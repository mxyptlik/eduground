# Prompts

This package stores versioned prompt templates for the curriculum tutor system.

## Structure

- `tutor/`
- `quiz/`
- `citation/`
- `guardrail/`
- `policy/`

## Versioning rule

Each prompt family is stored as immutable versioned markdown. New behavior gets a new version file instead of overwriting an existing one.

## Runtime selection

`registry.json` declares the active version for each prompt family and policy mode. The API reads only registry-approved Markdown files at runtime. To revise behavior, add a new versioned file, register it, and move that family's `active` field to the new version; do not edit an existing version in place.
