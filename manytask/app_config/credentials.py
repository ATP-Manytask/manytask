from pydantic import BaseModel, Field


class AppCredentialsConfig(BaseModel):
    student_secret: str = Field(min_length=1)
    admin_secret: str = Field(min_length=1)

    flask_token: str = Field(min_length=1)
    tester_token: str = Field(min_length=1)
