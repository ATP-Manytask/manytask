import base64
from unittest.mock import Mock

import pytest
import yaml

from manytask import main


@pytest.mark.parametrize("debug", [False, True])
def test_create_app_loads_yaml_and_uses_its_tester_token(tmp_path, monkeypatch, debug):
    config = yaml.safe_load((main.PROJECT_ROOT / ".app.config.example.yaml").read_text(encoding="utf-8"))
    config["credentials"] = {
        "student_secret": "students",
        "admin_secret": "admins",
        "flask_token": "session-key",
        "tester_token": "yaml-token",
    }
    config["gitlab"].update(admin_token="admin-token", application_id="42", application_secret="oauth-secret")
    config["gsheets"].update(
        credentials_base64=base64.b64encode(b'{"client_email": "test@example.com"}').decode(),
        spreadsheet_id="sheet-id",
    )
    config["mount"] = {"cache": str(tmp_path / "cache"), "solutions": str(tmp_path / "solutions")}
    config_path = tmp_path / "app.config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    gitlab = Mock()
    gsheets = Mock()
    monkeypatch.setattr(main.glab, "GitLabApi", gitlab)
    monkeypatch.setattr(main.gdoc, "GoogleDocApi", gsheets)
    monkeypatch.setattr(main.logging.config, "dictConfig", Mock())
    monkeypatch.setattr(main, "APPLICATION_CONFIG", config_path)
    monkeypatch.setenv("TESTER_TOKEN", "env-token")
    monkeypatch.chdir(tmp_path)

    app = main.create_app(debug=debug, test=True)

    assert app.testing
    assert app.secret_key == "session-key"
    assert app.oauth.gitlab.authorize_url == "https://gitlab.com/oauth/authorize"
    assert gitlab.call_args.kwargs["course_public_repo"] == "course-name/public-[year]-[fall/spring]"
    assert gsheets.call_args.kwargs["base_url"] == "https://docs.google.com"
    assert gsheets.call_args.kwargs["gdoc_credentials"] == {"client_email": "test@example.com"}

    client = app.test_client()
    assert client.get("/api/score", headers={"Authorization": "Bearer env-token"}).status_code == 403
    assert client.get("/api/score", headers={"Authorization": "   "}).status_code == 403
    response = client.post(
        "/api/update_config",
        headers={"Authorization": "Bearer yaml-token"},
        data=(main.PROJECT_ROOT / ".manytask.example.yml").read_bytes(),
    )
    assert response.status_code == 200
    assert app.course.config is not None
