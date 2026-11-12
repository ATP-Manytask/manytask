from pydantic import AnyUrl, BaseModel, field_validator


class AppGitlabConfig(BaseModel):
    base_url: AnyUrl
    admin_token: str

    course_group: str
    public_repo: str
    students_group: str

    default_branch: str = "main"

    application_id: str
    application_secret: str

    @property
    def course_public_repo(self) -> str:
        return f"{self.course_group}/{self.public_repo}"

    @property
    def course_students_group(self) -> str:
        return f"{self.course_group}/{self.students_group}"

    @field_validator("base_url")
    @classmethod
    def check_base_url(cls, data: str | None) -> str | None:
        if data is not None and not data.startswith("https://"):
            raise ValueError("base_url should be https")
        return data

    def relative_url(self, suffix: str) -> str:
        return f"{self.base_url}/{suffix}"