import secrets as secrets_lib

from pydantic import BaseModel


class AppSecretsConfig(BaseModel):
    student: str
    reviewer: str
    admin: str


class AppCredentialsConfig(BaseModel):
    secrets: AppSecretsConfig

    flask_token: str = secrets_lib.token_hex()
    tester_token: str
