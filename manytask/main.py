import logging
import logging.config
import logging.handlers
import os
from pathlib import Path

import yaml
from authlib.integrations.flask_client import OAuth
from cachelib import FileSystemCache
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from . import app_config, course, gdoc, glab, solutions


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APPLICATION_CONFIG = PROJECT_ROOT / "app.config.yaml"


class CustomFlask(Flask):
    course: course.Course
    oauth: OAuth
    app_config: app_config.AppConfig


def create_app(
    *, debug: bool | None = None, test: bool = False, config_path: str | Path | None = None
) -> CustomFlask:
    app = CustomFlask(__name__)

    if debug is not None:
        app.debug = debug

    with open(config_path if config_path is not None else APPLICATION_CONFIG, encoding="utf-8") as app_yaml:
        app.app_config = app_config.AppConfig.model_validate(yaml.safe_load(app_yaml))

    app.testing = os.environ.get("TESTING", str(test)).lower() in {"true", "1", "yes"}
    app.secret_key = app.app_config.credentials.flask_token

    # oauth
    app.oauth = OAuth(app)
    app.oauth.register(
        name="gitlab",
        client_id=app.app_config.gitlab.application_id,
        client_secret=app.app_config.gitlab.application_secret,
        authorize_url=app.app_config.gitlab.relative_url("oauth/authorize"),
        access_token_url=app.app_config.gitlab.relative_url("oauth/token"),
        userinfo_endpoint=app.app_config.gitlab.relative_url("oauth/userinfo"),
        jwks_uri=app.app_config.gitlab.relative_url("oauth/discovery/keys"),
        client_kwargs={
            "scope": "openid email profile read_user",
            "code_challenge_method": "S256",
        },
    )

    # logging
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s - "
                    "process-%(process)d:%(thread)d app in %(filename)s : %(message)s",
                },
                "access": {
                    "format": "%(message)s",
                },
            },
            "handlers": {
                "console": {
                    "level": "INFO",
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                },
                "general_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "default",
                    "filename": "/var/log/general.log",
                    "maxBytes": 10 * 1000,
                    "backupCount": 2,
                    "delay": "True",
                },
                "error_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "default",
                    "filename": "/var/log/error.log",
                    "maxBytes": 10 * 1000,
                    "backupCount": 2,
                    "delay": "True",
                },
                "access_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "access",
                    "filename": "/var/log/access.log",
                    "maxBytes": 10 * 1000,
                    "backupCount": 2,
                    "delay": "True",
                },
            },
            "loggers": {
                "gunicorn.error": {
                    "handlers": ["console"] if app.debug else ["console", "error_file"],
                    "level": "INFO",
                    "propagate": False,
                },
                "gunicorn.access": {
                    "handlers": (["console"] if app.debug else ["console", "access_file"]),
                    "level": "INFO",
                    "propagate": False,
                },
            },
            "root": {
                "level": "DEBUG" if app.debug else "INFO",
                "handlers": ["console"] if app.debug else ["console", "general_file"],
            },
        }
    )

    # cache
    cache = FileSystemCache(app.app_config.mount.cache, threshold=0, default_timeout=0)

    # api objects
    gitlab_api = glab.GitLabApi(
        base_url=app.app_config.gitlab.base_url.encoded_string().rstrip("/"),
        admin_token=app.app_config.gitlab.admin_token,
        course_group=app.app_config.gitlab.course_group,
        course_public_repo=app.app_config.gitlab.course_public_repo,
        course_students_group=app.app_config.gitlab.course_students_group,
        default_branch=app.app_config.gitlab.default_branch,
    )
    gdoc_api = gdoc.GoogleDocApi(
        base_url=app.app_config.gsheets.base_url.encoded_string().rstrip("/"),
        gdoc_credentials=app.app_config.gsheets.credentials_json,
        spreadsheet_id=app.app_config.gsheets.spreadsheet_id,
    )
    solutions_api = solutions.SolutionsApi(
        base_folder=app.app_config.mount.solutions,
    )

    # read VERSION file to get a version
    manytask_version = ""
    try:
        with (PROJECT_ROOT / "VERSION").open(encoding="utf-8") as f:
            manytask_version = f.read().strip()
    except FileNotFoundError:
        pass

    # create course
    app.course = course.Course(
        gdoc_api,
        gitlab_api,
        solutions_api,
        app.app_config.credentials.secrets,
        app.app_config.gsheets.sheets,
        app.app_config.gsheets.show_allscores,
        cache,
        manytask_version=manytask_version,
        debug=app.debug,
    )

    # for https support
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)  # type: ignore

    # routes
    from . import api, web

    app.register_blueprint(api.bp)
    app.register_blueprint(web.bp)

    logger = logging.getLogger(__name__)

    # debug updates
    if app.course.debug:
        with (PROJECT_ROOT / ".manytask.example.yml").open(encoding="utf-8") as f:
            debug_manytask_config_data = yaml.load(f, Loader=yaml.SafeLoader)
        app.course.store_config(debug_manytask_config_data)

    logger.info("Init success")

    return app
