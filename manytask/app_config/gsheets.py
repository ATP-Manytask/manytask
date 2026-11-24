import base64
import json
from typing import Any

from pydantic import AnyUrl, BaseModel, field_validator


class AppWorksheetsNames(BaseModel):
    scoreboard: str
    events: str
    reviewers: str


class AppGSheetsConfig(BaseModel):
    base_url: AnyUrl
    credentials_base64: str  # base64 encoded json
    spreadsheet_id: str

    sheets: AppWorksheetsNames

    show_allscores: bool = True

    @field_validator("base_url")
    @classmethod
    def check_base_url(cls, data: AnyUrl) -> AnyUrl:
        if data.scheme != "https" or data.host is None:
            raise ValueError("base_url should be https")
        return data

    @property
    def credentials_json(self) -> Any:
        return json.loads(base64.decodebytes(self.credentials_base64.encode()))
