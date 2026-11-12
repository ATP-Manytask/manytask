from pydantic import BaseModel, Field


class AppSecretsConfig(BaseModel):
    student: str = Field(min_length=1)
    admin: str = Field(min_length=1)


class AppCredentialsConfig(BaseModel):
    secrets: AppSecretsConfig
    flask_token: str = Field(min_length=1)
    tester_token: str = Field(min_length=1)
