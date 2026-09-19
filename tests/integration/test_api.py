import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assessments.api.app import create_app
from assessments.config import Settings
from assessments.errors import PolicyViolationError


@pytest.fixture
def app() -> FastAPI:
    app = create_app(Settings(_env_file=None))

    @app.get("/_test/policy")
    async def policy() -> None:
        raise PolicyViolationError("navigation to evil.example is not allowed")

    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/_test/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(response.headers["x-request-id"]) == 32


def test_valid_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/healthz", headers={"x-request-id": "trace-abc.1"})

    assert response.headers["x-request-id"] == "trace-abc.1"


@pytest.mark.parametrize("bad_id", ["x" * 65, "has space", 'inj"ect'])
def test_malformed_incoming_request_id_is_replaced(client: TestClient, bad_id: str) -> None:
    response = client.get("/healthz", headers={"x-request-id": bad_id})

    assert response.headers["x-request-id"] != bad_id


def test_app_error_maps_to_status_and_code(client: TestClient) -> None:
    response = client.get("/_test/policy", headers={"x-request-id": "r1"})

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "policy_violation",
            "message": "navigation to evil.example is not allowed",
            "request_id": "r1",
        }
    }


def test_unknown_route_uses_error_shape(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_request_validation_error_uses_error_shape(client: TestClient) -> None:
    response = client.get("/_test/items/not-an-int")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["details"][0]["loc"] == ["path", "item_id"]


def test_unhandled_exception_is_generic_500_with_request_id(client: TestClient) -> None:
    response = client.get("/_test/crash", headers={"x-request-id": "r2"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error", "request_id": "r2"}
    }
