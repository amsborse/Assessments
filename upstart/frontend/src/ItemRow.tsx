import { useState, type FormEvent } from "react";

import type { Item, ItemInput } from "./api";

type Props = {
  item: Item;
  onToggle: (item: Item) => void;
  onSave: (item: Item, input: ItemInput) => Promise<boolean>;
  onRefresh: (item: Item) => void;
  onDelete: (item: Item) => void;
  busy: boolean;
  leaving: boolean;
};

const created = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** One item: reads as a line, edits in place, and names the verb behind each action. */
export default function ItemRow({
  item,
  onToggle,
  onSave,
  onRefresh,
  onDelete,
  busy,
  leaving,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(item.name);
  const [note, setNote] = useState(item.note ?? "");
  const [armed, setArmed] = useState(false);

  function startEditing() {
    setName(item.name);
    setNote(item.note ?? "");
    setEditing(true);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    // PUT: the body carries every field, so an emptied note really is cleared.
    const saved = await onSave(item, { name, note: note.trim() || null, done: item.done });
    if (saved) setEditing(false);
  }

  if (editing) {
    return (
      <li className="row">
        <form onSubmit={save}>
          <input
            type="text"
            aria-label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoFocus
          />
          <input
            type="text"
            aria-label="Note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Note"
          />
          <button type="submit" className="primary" disabled={busy}>
            Save changes
          </button>
          <button type="button" className="ghost" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </form>
      </li>
    );
  }

  const createdAt = new Date(item.created_at);

  const classes = ["row", item.done && "is-done", leaving && "leaving"].filter(Boolean).join(" ");

  return (
    <li className={classes}>
      <input
        type="checkbox"
        checked={item.done}
        disabled={busy}
        onChange={() => onToggle(item)}
        aria-label={`Mark ${item.name} ${item.done ? "not done" : "done"}`}
      />
      <span className="body">
        <span className="name">{item.name}</span>
        {item.note && <div className="note">{item.note}</div>}
      </span>
      <span className="meta" title={`Created ${createdAt.toLocaleString()}`}>
        <span className="id">#{item.id}</span>
        <time dateTime={item.created_at}>{created.format(createdAt)}</time>
      </span>
      <span className="actions">
        <button type="button" className="verb" disabled={busy} onClick={() => onRefresh(item)}>
          GET
        </button>
        <button type="button" className="verb" disabled={busy} onClick={startEditing}>
          PUT
        </button>
        {armed ? (
          <>
            <button
              type="button"
              className="verb armed"
              disabled={busy}
              onClick={() => onDelete(item)}
            >
              Delete it
            </button>
            <button type="button" className="verb" onClick={() => setArmed(false)}>
              Keep
            </button>
          </>
        ) : (
          <button
            type="button"
            className="verb destructive"
            disabled={busy}
            onClick={() => setArmed(true)}
          >
            DELETE
          </button>
        )}
      </span>
    </li>
  );
}
