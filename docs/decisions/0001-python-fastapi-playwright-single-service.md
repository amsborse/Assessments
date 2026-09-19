# 0001 — Single Python process: FastAPI + Playwright, custom agent loop

- Status: accepted
- Date: 2026-09-18

## Context

The system drives legacy web applications through a real browser using a computer-use model,
exposes an operator console for human takeover of the *live* session, and stores capability
artifacts and run evidence. It is a small system that must be easy to run and reason about.

## Decision

- **One Python 3.12 process** hosts the catalog API, operator console, discovery loop and replay
  engine on one asyncio loop, with Chromium driven in-process by Playwright. No services/queues.
- **Custom observe → decide → act loop** (not an agent framework or a hosted agent runtime), so
  every action passes our policy and control guard and produces evidence we control.
- **Stateless model decisions:** each step is one request with a cached prefix; no transcript.
- **Local filesystem storage:** capabilities as JSON under `catalog/` (in git), evidence under
  `data/runs/`.

## Consequences

- Handoff is a function call on the same page object — no cross-process session sharing.
- One process = one scaling unit; concurrent sessions scale by running more workers, each owning
  its sessions (the `LiveSession` boundary), when needed.
- The image carries Chromium (~1.3 GB uncompressed).
