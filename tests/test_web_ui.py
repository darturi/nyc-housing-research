from fastapi.testclient import TestClient

from app.main import app
from tests.retrieval_fixtures import TEST_PASSWORD, create_test_user


def test_root_redirects_unauthenticated_user_to_login():
    client = TestClient(app)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_app_redirects_unauthenticated_user_to_login():
    client = TestClient(app)

    response = client.get("/app", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_renders_form_and_assets():
    client = TestClient(app)

    response = client.get("/login")

    assert response.status_code == 200
    assert '<form id="login-form"' in response.text
    assert 'rel="icon" href="/static/favicon.svg"' in response.text
    assert 'href="/static/web.css"' in response.text
    assert 'src="/static/web.js"' in response.text


def test_authenticated_root_redirects_to_app():
    create_test_user()
    client = authenticated_client()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/app"


def test_authenticated_app_renders_workspace_shell():
    create_test_user()
    client = authenticated_client()

    response = client.get("/app")

    assert response.status_code == 200
    assert "admin@example.com" in response.text
    assert '<form id="query-form"' in response.text
    assert 'id="result-region"' in response.text
    assert 'rel="icon" href="/static/favicon.svg"' in response.text


def test_static_web_assets_are_served():
    client = TestClient(app)

    css_response = client.get("/static/web.css")
    js_response = client.get("/static/web.js")
    icon_response = client.get("/static/favicon.svg")

    assert css_response.status_code == 200
    assert "text/css" in css_response.headers["content-type"]
    assert js_response.status_code == 200
    assert "javascript" in js_response.headers["content-type"]
    assert icon_response.status_code == 200
    assert "image/svg+xml" in icon_response.headers["content-type"]


def authenticated_client() -> TestClient:
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    return client
