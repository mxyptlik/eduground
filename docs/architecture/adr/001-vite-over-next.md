# ADR 001: Vite Over Next.js

## Status

Accepted

## Context

The architecture package mentioned both Next.js and Vite for the web client. The MVP scaffold needs one clear default.

## Decision

Use Vite + React + TypeScript for `apps/web`.

## Consequences

- Faster greenfield bootstrap.
- Simpler client-first architecture.
- Server rendering is deferred until it is a proven requirement.

