from fastapi.testclient import TestClient

from forge import __version__


def test_livez_returns_200_with_version(client: TestClient) -> None:
    response = client.get("/livez")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert __version__ in body["message"]
    assert "Forge" in body["message"]
    assert "running" in body["message"]


def test_readyz_returns_200(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
