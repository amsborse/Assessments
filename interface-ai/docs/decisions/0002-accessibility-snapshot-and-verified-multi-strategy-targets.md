# 0002 — Perceive via accessibility-style snapshot; record verified multi-strategy targets

- Status: accepted
- Date: 2026-09-18

## Context

Targets are legacy surfaces: framesets, table layouts, labels in neighbouring cells, no ids or
test ids; desktop apps later. Replay must be deterministic without a model and must survive
per-tenant relabelling. Options: (a) screenshot + coordinates (vendor computer-use tool);
(b) raw DOM/CSS selectors written by the model; (c) an accessibility-style snapshot with element
refs, converted to locators by the system.

## Decision

(c). An injected script builds a snapshot across all frames (roles, accessible names including
adjacent-cell labels, table rows) with refs; a redacted screenshot accompanies it. When the model
acts on a ref, the surface derives candidate strategies (role+name, label, form-field name, table
cell by row key × column header, text, CSS path), **verifies each resolves uniquely to that same
element**, and records the verified ones in robustness order. Reads are addressed semantically
(table cell / label) rather than by ref.

## Consequences

- Artifacts are replayable and reviewable; fallback use at replay is a measurable drift signal.
- The same strategy vocabulary maps onto desktop accessibility trees (UIA/AX).
- Surfaces with no accessibility information (canvas, remote desktop) need an additional
  pixel/OCR strategy kind; coordinates alone are never recorded as the primary target.
