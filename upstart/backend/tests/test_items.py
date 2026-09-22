"""API tests for the item CRUD slice."""

import logging

import pytest
from fastapi.testclient import TestClient


def create(client: TestClient, **fields: object) -> dict:
    payload = {"name": "Coffee", **fields}
    response = client.post("/items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_returns_stored_row(client: TestClient) -> None:
    item = create(client, note="Beans for Monday")

    assert item["id"] > 0
    assert item["name"] == "Coffee"
    assert item["note"] == "Beans for Monday"
    assert item["done"] is False
    assert item["created_at"]


def test_created_items_are_listed_newest_first(client: TestClient) -> None:
    create(client, name="Coffee")
    create(client, name="Tea")

    items = client.get("/items").json()

    assert [item["name"] for item in items] == ["Tea", "Coffee"]


def test_list_is_empty_without_items(client: TestClient) -> None:
    assert client.get("/items").json() == []


def test_read_one_item(client: TestClient) -> None:
    item = create(client)

    assert client.get(f"/items/{item['id']}").json() == item


def test_blank_name_is_rejected(client: TestClient) -> None:
    assert client.post("/items", json={"name": "   "}).status_code == 422
    assert client.post("/items", json={}).status_code == 422
    assert client.get("/items").json() == []


def test_put_replaces_every_field(client: TestClient) -> None:
    item = create(client, note="Beans for Monday", done=True)

    replaced = client.put(f"/items/{item['id']}", json={"name": "Tea"}).json()

    # Fields the body leaves out fall back to their defaults.
    assert replaced["name"] == "Tea"
    assert replaced["note"] is None
    assert replaced["done"] is False
    assert replaced["created_at"] == item["created_at"]


def test_patch_changes_only_the_fields_sent(client: TestClient) -> None:
    item = create(client, note="Beans for Monday")

    patched = client.patch(f"/items/{item['id']}", json={"done": True}).json()

    assert patched["done"] is True
    assert patched["name"] == "Coffee"
    assert patched["note"] == "Beans for Monday"


def test_patch_rejects_a_blank_name(client: TestClient) -> None:
    item = create(client)

    assert client.patch(f"/items/{item['id']}", json={"name": " "}).status_code == 422
    assert client.get(f"/items/{item['id']}").json()["name"] == "Coffee"


def test_delete_removes_the_item(client: TestClient) -> None:
    item = create(client)

    assert client.delete(f"/items/{item['id']}").status_code == 204
    assert client.get(f"/items/{item['id']}").status_code == 404
    assert client.get("/items").json() == []


def test_missing_item_is_404_on_every_route(client: TestClient) -> None:
    assert client.get("/items/999").status_code == 404
    assert client.put("/items/999", json={"name": "Tea"}).status_code == 404
    assert client.patch("/items/999", json={"done": True}).status_code == 404
    assert client.delete("/items/999").status_code == 404


def test_created_at_is_serialized_as_utc(client: TestClient) -> None:
    from datetime import datetime

    created_at = create(client)["created_at"]

    # The browser must not read a UTC timestamp as local time.
    assert datetime.fromisoformat(created_at).utcoffset().total_seconds() == 0


def test_request_is_logged(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="upstart"):
        create(client)

    assert "POST /items -> 201" in caplog.text


def test_unexpected_error_is_logged_and_returns_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(**_fields: object) -> None:
        raise RuntimeError("database on fire")

    monkeypatch.setattr("app.main.Item", explode)

    with caplog.at_level(logging.ERROR, logger="upstart"):
        response = client.post("/items", json={"name": "Coffee"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "database on fire" in caplog.text
