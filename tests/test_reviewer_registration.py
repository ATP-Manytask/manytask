from unittest.mock import Mock

import gitlab
import pytest
from flask import Flask

from manytask import web
from manytask.app_config import AppSecretsConfig
from manytask.glab import GitLabApi
from manytask.reviewers_table import ReviewersTable
from tests.sheets import Workbook, student


def test_reviewer_enrollment_uses_next_row_and_is_repeatable():
    table = ReviewersTable(Workbook(), "review team")
    table.make_reviewer(student("alice"))
    table.make_reviewer(student("alice"))
    assert table.worksheet.rows == [["login", "name"], ["alice", student("alice").name]]
    assert table.get_reviewers_logins() == ["alice"]


def test_registration_secrets_select_distinct_roles():
    config = AppSecretsConfig(student="one", reviewer="révision", admin="three")
    assert config.role_for("révision") == "reviewer"
    assert config.role_for("invalid") is None
    with pytest.raises(ValueError, match="different"):
        AppSecretsConfig(student="same", reviewer="same", admin="three")


def test_existing_gitlab_member_is_promoted_without_downgrading_an_owner():
    api = GitLabApi("https://gitlab.example", "token", "", "", "")
    group = Mock()
    api._get_group_by_name = Mock(return_value=group)
    alice = student("alice")
    group.members_all.get.return_value.access_level = gitlab.const.AccessLevel.DEVELOPER
    group.members.create.side_effect = gitlab.GitlabCreateError(response_code=409)
    member = group.members.get.return_value
    member.access_level = gitlab.const.AccessLevel.DEVELOPER
    api.make_reviewer(alice)
    assert member.access_level == gitlab.const.AccessLevel.MAINTAINER
    member.save.assert_called_once()

    group.reset_mock()
    group.members_all.get.return_value.access_level = gitlab.const.AccessLevel.OWNER
    api.make_reviewer(alice)
    group.members.create.assert_not_called()
    assert api.is_admin(alice.id)


def test_gitlab_grant_failure_is_reported():
    api = GitLabApi("https://gitlab.example", "token", "", "", "")
    group = Mock()
    api._get_group_by_name = Mock(return_value=group)
    group.members_all.get.side_effect = gitlab.GitlabGetError(response_code=404)
    group.members.create.side_effect = gitlab.GitlabCreateError(response_code=403)
    with pytest.raises(gitlab.GitlabCreateError):
        api.make_admin(student("alice"))


@pytest.mark.parametrize("role", ["student", "reviewer", "admin"])
def test_create_project_grants_requested_role_and_updates_session(role):
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(web.bp)
    course = app.course = Mock()
    course.secrets = AppSecretsConfig(student="s", reviewer="r", admin="a")
    course.reviewers_table = ReviewersTable(Workbook(), "course reviewers")
    alice = student("alice")
    course.gitlab_api.get_authenticated_student.return_value = alice
    client = app.test_client()
    with client.session_transaction() as session:
        session["gitlab"] = dict(version=web.SESSION_VERSION, username="alice", user_id=alice.id,
                                 repo="alice", oauth_access_token="token", course_admin=False, course_reviewer=False)
    response = client.post("/create_project", data={"secret": getattr(course.secrets, role)})
    assert response.status_code == 302
    course.gitlab_api.create_project.assert_called_once_with(alice)
    assert course.gitlab_api.make_admin.called == (role == "admin")
    assert course.gitlab_api.make_reviewer.called == (role == "reviewer")
    assert course.reviewers_table.is_reviewer(alice) == (role == "reviewer")
    with client.session_transaction() as session:
        assert session["gitlab"]["course_admin"] == (role == "admin")
        assert session["gitlab"]["course_reviewer"] == (role == "reviewer")
