# AGENTS.md — Development Rules

Rules for humans and AI agents working in this folder. The repository-wide rules in `../AGENTS.md`
also apply. Read `agent/ARCHITECTURE.md` before changing anything.

This folder is an interview boilerplate: one vertical slice, kept small enough to extend live in
60 minutes. Its value is that a reviewer can read all of it. Protect that.

## Workflow

Every change follows: **Understand → Plan → Implement → Verify → Self-review → Finish**.

1. **Understand** — Read the request and the code it touches. Reproduce bugs before fixing them.
   Ask when the requirement is ambiguous; do not guess at intent.
2. **Plan** — State the smallest change that meets the requirement and name the files to touch.
3. **Implement** — Small, focused diffs in the surrounding style. No drive-by refactors.
4. **Verify** — `pytest` passes and `npm run build` passes. Exercise the UI path by hand when it
   changed.
5. **Self-review** — Read the whole diff as the reviewer: correctness, validation, error handling,
   tests, docs. Delete debug and dead code.
6. **Finish** — Say what changed, how it was verified, and what was deliberately left out.

## Rules

- **Scope.** Build what was asked, nothing adjacent. No auth, Docker, caching, queues, search,
  pagination, or WebSockets until a requirement asks for them.
- **No speculative structure.** No interface with one implementation, no config for a value that
  never changes, no file created "for later". Routes live in `main.py` until there are enough to
  split.
- **Stack.** FastAPI + SQLAlchemy 2.0 (typed `Mapped[...]`) + SQLite; React 19 + TypeScript
  (`strict`) + Vite. Pin new backend dependencies in `requirements.txt`. Add a dependency only when
  a few lines cannot do the job.
- **Validation at the boundary.** Every request body is a Pydantic model; let FastAPI return 422.
  Never trust the client to have validated. Surface the API's message in the UI — never swallow a
  failed response or replace it with a generic string.
- **Verb semantics.** `PUT` replaces the whole resource (omitted fields reset to their default),
  `PATCH` changes only the fields present (`exclude_unset=True`), `DELETE` returns 204. A missing
  id is 404 from the shared `get_item` dependency, not a per-route check.
- **Database access.** Only through the `get_db` dependency, so tests can override it. No module
  level sessions, no session passed around by hand.
- **Frontend.** All HTTP goes through `src/api.ts` — components never call `fetch` and never build
  a URL. Keep state in the component that uses it; `useState`/`useEffect` are enough at this size.
  No state library, no data-fetching library, no CSS framework.
- **Styling.** Colors, spacing and radii come from the tokens at the top of `src/index.css`, and
  every token has a dark-mode value. Keyboard focus stays visible; no `outline: none`.
- **Motion.** CSS only — transitions plus the two keyframes in `index.css`, no animation library and
  no animation state in components. Animate `opacity`, `transform` and colors, never width, height
  or margins. Keep it under 200ms, only in response to a user action, and keep the
  `prefers-reduced-motion` block at the bottom of the file working.
- **Dialogs.** Never `window.confirm`/`alert` for destructive actions — arm the control in place
  (see the two-step delete in `ItemRow.tsx`).
- **Docs.** Update `README.md` when a command changes and `agent/ARCHITECTURE.md` when a component,
  boundary or flow changes. Never describe behavior the code does not have.
- **Secrets.** Never commit credentials, `.env` files, or `app.db`.
- **Shortcuts.** A deliberate simplification with a known ceiling gets a comment naming the ceiling
  and the upgrade path (see `create_all` in `app/main.py`).

## Testing

- Tests live in `backend/tests/` and go through the API with `TestClient` — that is what the
  reviewer cares about. Each test gets a fresh in-memory database from the `client` fixture.
- Test behavior that can break: status codes, persisted values, validation rejections. No
  placeholder tests, no asserting that a mock was called.
- **Regression rule:** reproduce the bug → write a failing test → fix the root cause → watch it
  pass. No fix without a test that would have caught it.
- The UI stays test-free while it is one component; if it grows logic, add Vitest and say so here.

## Commands

| Command | Purpose |
| --- | --- |
| `cd backend && uvicorn app.main:app --reload --port 8000` | Run the API |
| `cd backend && pytest` | Backend tests |
| `cd frontend && npm run dev` | Run the UI (proxies `/items` to the API) |
| `cd frontend && npm run build` | Type-check and production build |

Changing `app/models.py` means deleting `backend/app.db` — there are no migrations yet.
