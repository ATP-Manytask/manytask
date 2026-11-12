import secrets

from pydantic import BaseModel


class AppCredentialsConfig(BaseModel):
    student_secret: str
    admin_secret: str

    flask_token: str = secrets.token_hex()
    tester_token: str
