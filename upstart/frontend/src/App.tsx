import { useEffect, useState, type FormEvent } from "react";

import ItemRow from "./ItemRow";
import { ApiError, api, type Call, type Item, type ItemInput } from "./api";

/** Placeholder mark — swap in the real logo file when presenting. */
function Logo() {
  return (
    <svg className="logo" viewBox="0 0 32 32" role="img" aria-label="Upstart">
      <rect width="32" height="32" rx="8" fill="var(--action)" />
      <path
        d="M10 21V13a6 6 0 0 1 12 0v8"
        fill="none"
        stroke="var(--card)"
        strokeWidth="2.75"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function App() {
  const [items, setItems] = useState<Item[]>([]);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [call, setCall] = useState<Call | null>(null);
  const [busy, setBusy] = useState(false);
  // What POST last returned. It stays on the left until "GET all" refreshes the right.
  const [created, setCreated] = useState<Item | null>(null);
  // Counts calls so the status line replays its flash, and marks the row on its way out.
  const [calls, setCalls] = useState(0);
  const [leavingId, setLeavingId] = useState<number | null>(null);

  /** Run one API call, report it on the wire, and say whether it succeeded. */
  async function send<T>(work: () => Promise<[T, Call]>, then: (result: T) => void) {
    setBusy(true);
    try {
      const [result, completed] = await work();
      then(result);
      setCall(completed);
      setCalls((n) => n + 1);
      return true;
    } catch (error) {
      if (error instanceof ApiError) setCall(error.call);
      else setCall({ method: "", path: "", status: 0, message: "Backend unreachable" });
      setCalls((n) => n + 1);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const loadAll = () => send(api.list, setItems);

  useEffect(() => {
    void loadAll();
  }, []);

  const replaceInList = (updated: Item) =>
    setItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));

  async function addItem(event: FormEvent) {
    event.preventDefault();
    const ok = await send(
      () => api.create({ name, note: note.trim() || null, done: false }),
      setCreated,
    );
    if (ok) {
      setName("");
      setNote("");
    }
  }

  const saveItem = (item: Item, input: ItemInput) =>
    send(() => api.replace(item.id, input), replaceInList);

  // PATCH carries the one field that changed; the rest of the row is untouched.
  const toggleItem = (item: Item) =>
    void send(() => api.patch(item.id, { done: !item.done }), replaceInList);

  const refreshItem = (item: Item) => void send(() => api.read(item.id), replaceInList);

  const deleteItem = (item: Item) =>
    void send(
      () => api.remove(item.id),
      () => {
        // Let the row finish fading (.row.leaving, 0.18s) before dropping it.
        setLeavingId(item.id);
        setTimeout(() => {
          setItems((current) => current.filter((row) => row.id !== item.id));
          setCreated((shown) => (shown?.id === item.id ? null : shown));
          setLeavingId(null);
        }, 180);
      },
    );

  return (
    <main className="page">
      <header className="masthead">
        <div className="brand">
          <Logo />
          <h1>Upstart AI Assisted round</h1>
        </div>
        <div className="wire" key={calls} data-state={wireState(call)}>
          <span className="status">{call ? `${call.status || "—"}` : "…"}</span>
          <span>{call ? `${call.method} ${call.path}` : "GET /items"}</span>
          <span className="detail">{call?.message}</span>
        </div>
      </header>

      <section className="brief">
        <h2>Currently implemented</h2>
        <p>
          One form writing to one SQLite table, with every request it takes to read and change that
          table already wired up. Each control below names the request it sends.
        </p>
        <dl>
          <div>
            <dt>The form</dt>
            <dd>A required name, 1–100 characters and trimmed, plus an optional note up to 500.</dd>
          </div>
          <div>
            <dt>What is stored</dt>
            <dd>id, name, note, done, created_at — SQLite via SQLAlchemy, timestamps in UTC.</dd>
          </div>
          <div>
            <dt>Endpoints</dt>
            <dd>GET and POST on /items; GET, PUT, PATCH and DELETE on /items/&#123;id&#125;.</dd>
          </div>
          <div>
            <dt>When it fails</dt>
            <dd>422 names the field that was rejected, 404 an unknown id. Both show in the line above.</dd>
          </div>
        </dl>
      </section>

      <div className="panels">
        <section className="panel">
          <div className="panel-head">
            <h2>Add item</h2>
          </div>
          <form className="compose" onSubmit={addItem}>
            <input
              type="text"
              aria-label="Item name"
              placeholder="What is it?"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <input
              type="text"
              aria-label="Note"
              placeholder="Note (optional)"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
            <button type="submit" className="primary" disabled={busy}>
              Add item
            </button>
          </form>

          {created ? (
            <div className="created" key={created.id}>
              <span className="label">Returned by POST</span>
              <span className="name">{created.name}</span>
              {created.note && <span className="note">{created.note}</span>}
              <span className="meta">
                #{created.id} · {new Date(created.created_at).toLocaleString()}
              </span>
            </div>
          ) : (
            <p className="hint">The item POST returns shows up here.</p>
          )}
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>
              All items <span className="count">{items.length}</span>
            </h2>
            <button type="button" className="verb" disabled={busy} onClick={() => void loadAll()}>
              GET all
            </button>
          </div>

          {items.length === 0 ? (
            <p className="empty">Nothing stored yet. Add the first item on the left.</p>
          ) : (
            <ul className="items">
              {items.map((item) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  busy={busy}
                  leaving={item.id === leavingId}
                  onToggle={toggleItem}
                  onSave={saveItem}
                  onRefresh={refreshItem}
                  onDelete={deleteItem}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}

function wireState(call: Call | null) {
  if (!call) return "idle";
  return call.status >= 200 && call.status < 300 ? "ok" : "error";
}
