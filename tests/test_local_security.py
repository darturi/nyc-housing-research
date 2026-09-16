from fastapi.testclient import TestClient

from app.cli.main import main
from app.local_app import create_local_app
from app.workspace.context import WorkspaceContext


def _app(tmp_path):
    root = tmp_path / "workspace"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    context = WorkspaceContext.from_options(root, environment={})
    return create_local_app(context)


def _exchange(client: TestClient, app):
    return client.post(
        "/api/v1/session/exchange",
        headers={"Origin": "http://127.0.0.1"},
        json={"launch_token": app.state.launch_token},
    )


def test_sensitive_routes_require_one_use_launch_exchange(tmp_path, capsys) -> None:
    app = _app(tmp_path)
    capsys.readouterr()
    first_client = TestClient(app, base_url="http://127.0.0.1")
    second_client = TestClient(app, base_url="http://127.0.0.1")

    assert first_client.get("/api/v1/status").status_code == 401
    exchanged = _exchange(first_client, app)
    assert exchanged.status_code == 200
    assert "nyc_housing_session" in exchanged.headers["set-cookie"]
    assert "HttpOnly" in exchanged.headers["set-cookie"]
    assert first_client.get("/api/v1/status").status_code == 200
    assert _exchange(second_client, app).status_code == 401


def test_feature_spec_local_session_alias_is_one_use(tmp_path, capsys) -> None:
    app = _app(tmp_path)
    capsys.readouterr()
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.post(
        "/api/v1/local-session",
        headers={"Origin": "http://127.0.0.1"},
        json={"launch_token": app.state.launch_token},
    )

    assert response.status_code == 200
    assert client.get("/api/v1/status").json()["mode"] == "local"


def test_host_origin_and_csrf_are_enforced_before_routing(tmp_path, capsys) -> None:
    app = _app(tmp_path)
    capsys.readouterr()
    client = TestClient(app, base_url="http://127.0.0.1")

    assert client.get("/", headers={"Host": "attacker.invalid"}).status_code == 400
    assert (
        client.post(
            "/api/v1/session/exchange",
            json={"launch_token": app.state.launch_token},
        ).status_code
        == 403
    )
    exchanged = _exchange(client, app)
    assert exchanged.status_code == 200
    assert (
        client.post(
            "/api/v1/unknown-state-route",
            headers={"Origin": "http://127.0.0.1"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/unknown-state-route",
            headers={
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": exchanged.json()["csrf_token"],
            },
        ).status_code
        == 404
    )


def test_application_restart_invalidates_old_browser_session(tmp_path, capsys) -> None:
    app = _app(tmp_path)
    capsys.readouterr()
    client = TestClient(app, base_url="http://127.0.0.1")
    assert _exchange(client, app).status_code == 200
    assert client.get("/api/v1/status").status_code == 200

    context = WorkspaceContext.from_options(tmp_path / "workspace", environment={})
    restarted = create_local_app(context)
    restarted_client = TestClient(restarted, base_url="http://127.0.0.1")
    restarted_client.cookies.update(client.cookies)
    assert restarted_client.get("/api/v1/status").status_code == 401
