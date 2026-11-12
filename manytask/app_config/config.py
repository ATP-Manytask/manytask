from pydantic import BaseModel, field_validator

from .credentials import AppCredentialsConfig
from .gitlab import AppGitlabConfig
from .gsheets import AppGSheetsConfig
from .mount import AppMountConfig


class AppConfig(BaseModel):
    """Manytask application configuration."""

    version: int

    credentials: AppCredentialsConfig
    gitlab: AppGitlabConfig
    gsheets: AppGSheetsConfig
    mount: AppMountConfig


    @field_validator("version")
    @classmethod
    def check_version(cls, data: int) -> int:
        if data != 1:
            raise ValueError(f"Only version 1 is supported for {cls.__name__}")
        return data