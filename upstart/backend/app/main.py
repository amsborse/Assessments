"""FastAPI app: CRUD over items — the slice the UI drives end to end."""

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.models import Item

# ponytail: create_all is fine for a single-table boilerplate; add Alembic the
# first time the schema has to change without dropping the file.
Base.metadata.create_all(engine)

app = FastAPI(title="Upstart Items")

Name = Field(min_length=1, max_length=100)
Note = Field(default=None, max_length=500)


class ItemCreate(BaseModel):
    """POST /items body."""

    name: str = Name
    note: str | None = Note
    done: bool = False

    model_config = ConfigDict(str_strip_whitespace=True)


class ItemReplace(BaseModel):
    """PUT /items/{id} body — fields left out are reset to their default."""

    name: str = Name
    note: str | None = Note
    done: bool = False

    model_config = ConfigDict(str_strip_whitespace=True)


class ItemPatch(BaseModel):
    """PATCH /items/{id} body — only the fields present are changed."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Note
    done: bool | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class ItemOut(BaseModel):
    id: int
    name: str
    note: str | None
    done: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at")
    def stamp_utc(self, value: datetime) -> datetime:
        # SQLite stores datetimes without their offset, so the UTC we wrote goes
        # back on here. Without it the browser reads the value as local time.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def get_item(item_id: int, db: Session = Depends(get_db)) -> Item:
    """Load an item or raise 404 — shared by every single-item route."""
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.get("/items", response_model=list[ItemOut])
def list_items(db: Session = Depends(get_db)) -> list[Item]:
    return list(db.scalars(select(Item).order_by(Item.id.desc())))


@app.post("/items", response_model=ItemOut, status_code=201)
def create_item(payload: ItemCreate, db: Session = Depends(get_db)) -> Item:
    item = Item(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.get("/items/{item_id}", response_model=ItemOut)
def read_item(item: Item = Depends(get_item)) -> Item:
    return item


@app.put("/items/{item_id}", response_model=ItemOut)
def replace_item(
    payload: ItemReplace,
    item: Item = Depends(get_item),
    db: Session = Depends(get_db),
) -> Item:
    for field, value in payload.model_dump().items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@app.patch("/items/{item_id}", response_model=ItemOut)
def update_item(
    payload: ItemPatch,
    item: Item = Depends(get_item),
    db: Session = Depends(get_db),
) -> Item:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item: Item = Depends(get_item), db: Session = Depends(get_db)) -> Response:
    db.delete(item)
    db.commit()
    return Response(status_code=204)
