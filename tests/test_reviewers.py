from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from manytask.config import ManytaskConfig, ManytaskReviewerMode, ManytaskTaskConfig


def make_config_data(*tasks: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "settings": {
            "course_name": "course",
            "gitlab_base_url": "https://gitlab.test",
            "public_repo": "course/public",
            "students_group": "course/students",
        },
        "ui": {"task_url_template": "https://gitlab.test/$GROUP_NAME/$TASK_NAME"},
        "deadlines": {
            "timezone": "Europe/Moscow",
            "schedule": [
                {
                    "group": "group",
                    "enabled": False,
                    "start": "2000-01-01 00:00",
                    "end": "2100-01-01 00:00",
                    "tasks": list(tasks),
                }
            ],
        },
    }

class TestReviewerConfig:
    @pytest.mark.parametrize(
        "data, mode, login",
        [
            ({}, ManytaskReviewerMode.ANY, None),
            ({"reviewer_mode": "any"}, ManytaskReviewerMode.ANY, None),
            ({"reviewer_mode": "none"}, ManytaskReviewerMode.NONE, None),
            ({"reviewer_mode": "concrete", "reviewer_login": "teacher"}, ManytaskReviewerMode.CONCRETE, "teacher"),
            # login can be any string, even the same as some mode
            ({"reviewer_mode": "concrete", "reviewer_login": "none"}, ManytaskReviewerMode.CONCRETE, "none"),
        ],
    )
    def test_reviewer_mode(self, data: dict[str, Any], mode: ManytaskReviewerMode, login: str | None) -> None:
        task = ManytaskTaskConfig(task="task", score=10, **data)
        assert task.reviewer_mode == mode
        assert task.reviewer_login == login
        assert task.has_reviewer == (mode != ManytaskReviewerMode.NONE)

    @pytest.mark.parametrize(
        "data",
        [
            {"reviewer_mode": "concrete"},
            {"reviewer_mode": "concrete", "reviewer_login": ""},
            {"reviewer_mode": "any", "reviewer_login": "teacher"},
            {"reviewer_mode": "none", "reviewer_login": "teacher"},
            {"reviewer_login": "teacher"},
            {"reviewer_mode": "teacher"},
        ],
    )
    def test_invalid_reviewer(self, data: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ManytaskTaskConfig(task="task", score=10, **data)


def test_get_concrete_reviewer_logins() -> None:
    config = ManytaskConfig(
        **make_config_data(
            {"task": "a", "score": 1},
            {"task": "b", "score": 1, "reviewer_mode": "none"},
            {"task": "c", "score": 1, "reviewer_mode": "concrete", "reviewer_login": "teacher"},
            {"task": "d", "score": 1, "reviewer_mode": "concrete", "reviewer_login": "teacher"},
            {"task": "e", "score": 1, "reviewer_mode": "concrete", "reviewer_login": "other"},
        )
    )
    assert config.get_concrete_reviewer_logins() == {"teacher", "other"}
