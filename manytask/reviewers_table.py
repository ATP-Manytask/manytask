import gspread

from .glab import Student


REVIEWERS_COLUMNS = ("login", "name")


class ReviewersTable:
    def __init__(self, spreadsheet: gspread.Spreadsheet, title: str):
        try:
            self.worksheet = spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            self.worksheet = spreadsheet.add_worksheet(title, rows=100, cols=len(REVIEWERS_COLUMNS))
        header = self.worksheet.row_values(1)
        if not header:
            self.worksheet.update_cells([gspread.Cell(1, column, name)
                                         for column, name in enumerate(REVIEWERS_COLUMNS, 1)])
        elif tuple(header) != REVIEWERS_COLUMNS:
            raise ValueError(
                "Unexpected reviewers schema; refusing to overwrite it")

    def is_reviewer(self, student: Student) -> bool:
        return student.username in self.get_reviewers_logins()

    def make_reviewer(self, student: Student) -> None:
        if not self.is_reviewer(student):
            self.worksheet.append_row([student.username, student.name])

    def get_reviewers_logins(self) -> list[str]:
        return list(dict.fromkeys(login for login in self.worksheet.col_values(1)[1:] if login))
