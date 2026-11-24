from __future__ import annotations

from functools import cached_property
from typing import Any

import gspread
from authlib.integrations.requests_client import AssertionSession
from google.auth.credentials import AnonymousCredentials


class GoogleDocApi:
    def __init__(
        self,
        base_url: str,
        gdoc_credentials: dict[str, Any],
        spreadsheet_id: str,
    ):
        self._url = base_url
        self._gdoc_credentials = gdoc_credentials
        self._spreadsheet_id = spreadsheet_id

        self._assertion_session = self._create_assertion_session()

    def _create_assertion_session(self) -> AssertionSession:
        """Create AssertionSession to auto refresh access to google api"""
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = self._gdoc_credentials

        header = {"alg": "RS256"}
        if key_id := credentials.get("private_key_id", None):
            header["kid"] = key_id

        # Google puts scope in payload
        claims = {"scope": " ".join(scopes)}
        return AssertionSession(
            token_endpoint=credentials["token_uri"],
            issuer=credentials["client_email"],
            subject=None,
            audience=credentials["token_uri"],
            grant_type=AssertionSession.JWT_BEARER_GRANT_TYPE,
            scope=" ".join(scopes),
            claims=claims,
            key=credentials["private_key"],
            header=header,
        )

    @cached_property
    def spreadsheet(self) -> gspread.Spreadsheet:
        client = gspread.Client(AnonymousCredentials(), session=self._assertion_session)
        return client.open_by_key(self._spreadsheet_id)

    def get_worksheet(self, title: str) -> gspread.Worksheet:
        return self.spreadsheet.worksheet(title)

    def get_spreadsheet_url(self, sheet_id: int) -> str:
        return f"{self._url}/spreadsheets/d/{self._spreadsheet_id}#gid={sheet_id}"
