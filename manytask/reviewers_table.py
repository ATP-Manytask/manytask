import gspread

from .glab import Student
from .spreadsheet import update_cells_request


REVIEWERS_COLUMNS = ("login", "name")


class ReviewersTable:
    def __init__(self, worksheet: gspread.Worksheet):
        self.worksheet = worksheet
        header = self.worksheet.row_values(1)
        if not header:
            self.worksheet.update_cells([gspread.Cell(1, column, name)
                                         for column, name in enumerate(REVIEWERS_COLUMNS, 1)])
        elif tuple(header) != REVIEWERS_COLUMNS:
            raise ValueError(
                "Unexpected reviewers schema; refusing to overwrite it")

    def is_reviewer(self, student: Student) -> bool:
        if student.course_reviewer:
            return True
        result = student.username in self.get_reviewers_logins()
        student.course_reviewer = result
        return result

    def make_reviewer(self, student: Student) -> None:
        if self.is_reviewer(student):
            return

        requests = []
        requests.append({"appendDimension": {"sheetId": self.worksheet.id, "dimension": "ROWS", "length": 1}})
        requests.append(
            update_cells_request(self.worksheet.id, self.worksheet.row_count + 1, 1, [student.username, student.name])
        )
        self.worksheet.spreadsheet.batch_update({"requests": requests})

        student.course_reviewer = True

    def get_reviewers_logins(self) -> list[str]:
        rows = self.worksheet.get_values()
        return [row[0] for row in rows[1:]]
