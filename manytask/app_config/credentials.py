import secrets
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppSecretsConfig(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    student: str = Field(min_length=1)
    admin: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_distinct_secrets(self) -> Self:
        if len({self.student, self.reviewer, self.admin}) != 3:
            raise ValueError("Student, reviewer and admin registration secrets must be different")
        return self

    def role_for(self, secret: str) -> Literal["admin", "reviewer", "student"] | None:
        supplied = secret.encode()
        if secrets.compare_digest(supplied, self.admin.encode()):
            return "admin"
        if secrets.compare_digest(supplied, self.reviewer.encode()):
            return "reviewer"
        if secrets.compare_digest(supplied, self.student.encode()):
            return "student"
        return None


class AppCredentialsConfig(BaseModel):
    secrets: AppSecretsConfig
    flask_token: str = Field(min_length=1)
    tester_token: str = Field(min_length=1)
