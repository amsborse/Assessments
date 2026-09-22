# Upstart — full-stack CRUD boilerplate

A working vertical slice, ready to extend under interview time pressure:
**page → form → request → validation → SQLite → list**, with all five verbs wired up.
React + TypeScript on the front, FastAPI + SQLAlchemy on the back, pytest for the API.

Prerequisites: Python 3.11+ and Node 20+.

## Backend (http://127.0.0.1:8000)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate      # Windows; macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The SQLite file `backend/app.db` is created on first start. Interactive API docs:
http://127.0.0.1:8000/docs

## Frontend (http://localhost:5173)

In a second terminal, with the backend running:

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/items` to the backend, so the browser sees one origin and the app needs no CORS
configuration.

Once both have been set up, `.\dev.ps1` (Windows) starts them together, each in its own window.

## Tests

```bash
cd backend
.venv/Scripts/activate      # Windows; macOS/Linux: source .venv/bin/activate
pytest                      # 13 API tests, each on a fresh in-memory database
```

```bash
cd frontend
npm run build               # tsc type-check + production build
```

## API

An item is `{ id, name, note, done, created_at }`. `name` is required (1–100 characters, trimmed);
`note` is optional (up to 500 characters); `done` defaults to `false`. `created_at` is always
returned in UTC with an explicit offset, so the browser renders it in local time.

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| `GET` | `/items` | — | `200` all items, newest first |
| `POST` | `/items` | `{"name", "note"?, "done"?}` | `201` the created item |
| `GET` | `/items/{id}` | — | `200` one item, `404` if unknown |
| `PUT` | `/items/{id}` | full item | `200` replaced — **fields left out reset to their default** |
| `PATCH` | `/items/{id}` | any subset | `200` updated — only the fields sent change |
| `DELETE` | `/items/{id}` | — | `204`, `404` if unknown |

Invalid bodies return `422` with FastAPI's field-level detail, which the UI displays as-is. An
unhandled server error returns `500 {"detail": "Internal server error"}` with the traceback in the
API log. Every request logs one line there — `POST /items -> 201 (10 ms)` — alongside uvicorn's own
access log.

```bash
curl -X POST http://127.0.0.1:8000/items -H "Content-Type: application/json" -d '{"name":"Coffee","note":"Beans for Monday"}'
curl -X PATCH http://127.0.0.1:8000/items/1 -H "Content-Type: application/json" -d '{"done":true}'
curl -X DELETE http://127.0.0.1:8000/items/1
```

## What the page does

Under the title sits a status line reporting the last call and its code, then **Currently
implemented** — the form's rules, what is stored, the endpoints, and what failure looks like. Below
that, two panels side by side:

**Left — Add item.** `POST /items`, then the item the response handed back is held under the form,
so you can see exactly what `POST` returned.

**Right — All items.** The stored collection, newest first, with a **GET all** button in its header
that re-runs `GET /items`. The right panel does not refresh itself after a POST; pressing GET all is
what brings the new row over.

Per row: the **checkbox** sends `PATCH /items/{id}` with just `done`; **GET** refetches that one row;
**PUT** opens an inline edit whose save sends every field, so clearing the note really clears it;
**DELETE** arms first and "Delete it" confirms, with no blocking browser dialog. Each row carries its
id and creation time next to the buttons.

Every control names the request it sends, and failures show in the status line with the API's own
message. The logo is a neutral placeholder — drop in the real asset before presenting.

Structure and request flow: [`agent/ARCHITECTURE.md`](agent/ARCHITECTURE.md).
Rules for changing this folder: [`AGENTS.md`](AGENTS.md).
