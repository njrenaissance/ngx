from fastapi.testclient import TestClient


def test_root_redirects_to_docs(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


def test_root_followed_lands_on_docs(client: TestClient) -> None:
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    # Swagger UI HTML contains this canonical title string.
    assert "Swagger UI" in response.text
