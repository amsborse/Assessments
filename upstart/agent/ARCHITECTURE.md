# Architecture

One resource (`Item`), five verbs, one page. Small enough to read in a sitting and shaped so that
new fields, routes, or screens drop into obvious places.

## Repo structure

```
upstart/
├── AGENTS.md                 development + review rules for this folder
├── README.md                 setup, commands, API reference
├── agent/ARCHITECTURE.md     this file
├── backend/
│   ├── requirements.txt      pinned backend dependencies
│   ├── conftest.py           pytest fixture: TestClient on an in-memory database
│   ├── app/
│   │   ├── db.py             engine, session factory, Base, get_db dependency
│   │   ├── models.py         Item ORM model (id, name, note, done, created_at)
│   │   └── main.py           FastAPI app, request/response schemas, all /items routes
│   └── tests/test_items.py   API tests covering every route
└── frontend/
    ├── vite.config.ts        dev server + proxy of /items to the backend
    ├── index.html            page shell, mounts #root
    └── src/
        ├── main.tsx          React root
        ├── api.ts            typed client: the only module that calls fetch
        ├── App.tsx           page shell: header, brief, the two panels, all page state
        ├── ItemRow.tsx       one row — display mode and inline edit mode
        └── index.css         design tokens and all styles (no CSS framework)
```

## Request flow

```
App.tsx / ItemRow.tsx        user action (submit, checkbox, GET, PUT, DELETE)
  → api.ts request()         builds the fetch, returns [data, call] or throws ApiError
  → Vite proxy               /items/* → 127.0.0.1:8000 (same origin in the browser)
  → log_requests middleware  times the request; catches anything unhandled below it
  → FastAPI route
      ├ get_item dependency  loads the row or raises 404
      └ Pydantic schema      validates the body, else 422 with field detail
  → SQLAlchemy Session (get_db) → SQLite → commit
  → ItemOut JSON
  → log_requests             logs "<METHOD> <path> -> <status> (<ms>)"
  → App.send()               updates local state, records the call
  → the status line shows "<code> <METHOD> <path>", green on success, red on failure
```

State lives in `App.tsx` and is updated from each response rather than by refetching the list.
`items` backs the right panel and only ever changes from `GET /items`, a row's own response, or a
delete. `POST` writes to a separate `created`, which the left panel shows under the form — so the
two panels visibly differ until **GET all** is pressed. `DELETE` filters the row out and clears
`created` if it was that item.

### Timestamps

SQLite stores datetimes without their offset, so a value written as UTC reads back naive and a
browser would render it as local time — hours off. `ItemOut.stamp_utc` puts the offset back on the
way out, which is why every `created_at` ends in `Z`. One serializer covers every route because
they all return `ItemOut`.

### Logging and unhandled errors

`log_requests` in `main.py` wraps the whole chain, so one place covers every route. `HTTPException`
(404) and validation failures (422) are turned into responses by FastAPI below the middleware, so
they are logged with their status like any other call. Anything that escapes a route is logged with
its traceback and answered `500 {"detail": "Internal server error"}` — the same shape `api.ts`
already parses, so the UI status line shows it instead of a blank failure. Configuration is
`logging.basicConfig` at INFO; uvicorn's own access log stays as it is.

### PUT vs PATCH

`ItemReplace` (PUT) has defaults for every optional field, so `model_dump()` writes them all: an
omitted `note` becomes `NULL`. `ItemPatch` makes every field optional and uses
`model_dump(exclude_unset=True)`, so only the keys actually sent are written. The UI uses PUT for
the edit form and PATCH for the done checkbox.

## Important files

| File | Responsibility |
| --- | --- |
| `backend/app/main.py` | Routes, HTTP schemas, and the request log / error middleware. Add endpoints here; split into a router package only when this file stops fitting on a screen or two. |
| `backend/app/models.py` | Database shape. `Base.metadata.create_all` in `main.py` creates tables at startup. |
| `backend/app/db.py` | Engine and the `get_db` dependency — the single seam tests override. |
| `backend/conftest.py` | Swaps `get_db` for a fresh in-memory SQLite per test. Its location also puts `app` on `sys.path`. |
| `frontend/src/api.ts` | Every HTTP call, the `Item` type, and the 404/422 error parsing. Components never call `fetch`. |
| `frontend/src/App.tsx` | Layout (header, brief, both panels), page state (`items`, `created`, last `call`) and the `send()` helper that runs a call, records it, and flips the busy flag. It also holds the placeholder `Logo`. |
| `frontend/src/ItemRow.tsx` | Row rendering, id/created-at metadata, inline edit state, two-step delete. |
| `frontend/src/index.css` | Tokens (`--ink`, `--action`, …), light and dark themes, and every transition/keyframe. |
| `frontend/vite.config.ts` | The `/items` proxy — the reason no CORS middleware exists. |

## Tests

```bash
cd backend && pytest          # 13 tests: create, list, read, PUT replace, PATCH partial, delete,
                              # 404 on every single-item route, validation, UTC timestamps,
                              # the request log line and the 500 path
cd frontend && npm run build  # tsc type-check + production build
```

## Deliberate limits

- `create_all` at import instead of migrations — add Alembic when the schema must change in place.
  Until then, a changed model means deleting `backend/app.db`.
- No frontend test runner. The logic worth testing lives in `api.ts`; add Vitest when it grows.
- No pagination, search, or sorting — `GET /items` returns everything, newest first.
- After `POST` the right panel is deliberately stale until **GET all** — that contrast is the point
  of the two panels, not a refresh bug. Call `loadAll()` at the end of `addItem` to change it.
- The logo is a placeholder mark defined inline in `App.tsx`, not anyone's trademark.
- The dev proxy replaces CORS; a deployed frontend needs a real API base URL or CORS config.
- Optimistic UI is not used: every action waits for its response and disables the controls.
- Motion is CSS. The one exception is deletion: React cannot animate an unmount, so `App.deleteItem`
  marks the row `leaving` and drops it after a 180ms `setTimeout` matching the transition. If you
  change that duration, change both.
