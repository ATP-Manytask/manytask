from __future__ import annotations

from typing import Any

from cachelib import SimpleCache
from gspread import Cell as GCell

from manytask.config import ManytaskDeadlinesConfig, ManytaskReviewerMode, ManytaskTaskConfig, TaskReviewStatus
from manytask.gdoc import PublicAccountsSheetOptions, RatingTable, TaskColumns
from manytask.glab import Student


class FakeWorksheet:
    """In-memory replacement of gspread.Worksheet, supports only methods used by RatingTable"""

    id = 0

    def __init__(self, rows: list[list[Any]] | None = None) -> None:
        self.cells: dict[tuple[int, int], Any] = {}
        for row_index, row in enumerate(rows or [], start=1):
            for col_index, value in enumerate(row, start=1):
                if value != "":
                    self.cells[(row_index, col_index)] = value

    def _row_len(self, row: int) -> int:
        return max([c for r, c in self.cells if r == row], default=0)

    def cell(self, row: int, col: int) -> GCell:
        value = self.cells.get((row, col))
        return GCell(row, col, None if value is None else str(value))

    def row_values(self, row: int, **kwargs: Any) -> list[Any]:
        return [self.cells.get((row, col), "") for col in range(1, self._row_len(row) + 1)]

    def col_values(self, col: int, **kwargs: Any) -> list[Any]:
        rows = max([r for r, c in self.cells if c == col], default=0)
        return [self.cells.get((row, col), "") for row in range(1, rows + 1)]

    def get_values(self) -> list[list[Any]]:
        rows = max([r for r, _ in self.cells], default=0)
        cols = max([c for _, c in self.cells], default=0)
        return [[str(self.cells.get((r, c), "")) for c in range(1, cols + 1)] for r in range(1, rows + 1)]

    def update_cells(self, cells: list[GCell], **kwargs: Any) -> None:
        for cell in cells:
            value = cell.value
            if isinstance(value, str) and value.startswith("'"):
                value = value[1:]  # user_entered input option drops leading quote
            if value is None or value == "":
                self.cells.pop((cell.row, cell.col), None)
            else:
                self.cells[(cell.row, cell.col)] = value

    def resize(self, **kwargs: Any) -> None:
        pass

    def format(self, *args: Any, **kwargs: Any) -> None:
        pass

STUDENT = Student(id=1, username="student", name="Student", repo="https://gitlab/student")
STUDENT_ROW = PublicAccountsSheetOptions.STUDENTS_START_ROW


def make_table(tasks: list[tuple[str, bool]]) -> tuple[RatingTable, FakeWorksheet]:
    """Create table with given (task name, has reviewer column) tasks and single student"""
    header: list[Any] = ["", "login", "name"]
    subheader: list[Any] = ["", "", ""]
    for name, has_reviewer in tasks:
        header += [name, ""] + ([""] if has_reviewer else [])
        subheader += ["score", "status"] + (["reviewer"] if has_reviewer else [])
    student: list[Any] = ["", STUDENT.username, STUDENT.name]
    ws = FakeWorksheet([[], [], header, subheader, student])
    return RatingTable(ws, SimpleCache()), ws  # type: ignore[arg-type]

def store(table: RatingTable, task: ManytaskTaskConfig, review: TaskReviewStatus) -> RatingTable.SubmissionStatus:
    return table.store_score(STUDENT, task, lambda _: task.score, review)

class TestTasksColumns:
    def test_parse_mixed_layout(self) -> None:
        table, ws = make_table([("a", True), ("b", False), ("c", True)])
        assert table._get_tasks_columns() == {
            "a": TaskColumns(score=4, has_reviewer=True),
            "b": TaskColumns(score=7, has_reviewer=False),
            "c": TaskColumns(score=9, has_reviewer=True),
        }

    def test_sync_columns(self) -> None:
        table, ws = make_table([("a", True)])
        deadlines = ManytaskDeadlinesConfig.model_validate({
            "timezone": "Europe/Moscow",
            "schedule": [
                {
                    "group": "group",
                    "start": "2000-01-01 00:00",
                    "end": "2100-01-01 00:00",
                    "tasks": [
                        {"task": "a", "score": 1},
                        {"task": "b", "score": 1, "reviewer_mode": "none"},
                        {"task": "c", "score": 1, "reviewer_mode": "concrete", "reviewer_login": "teacher"},
                    ],
                }
            ],
        })
        table.sync_columns(deadlines)

        assert table._get_tasks_columns() == {
            "a": TaskColumns(score=4, has_reviewer=True),
            "b": TaskColumns(score=7, has_reviewer=False),
            "c": TaskColumns(score=9, has_reviewer=True),
        }
        assert ws.row_values(PublicAccountsSheetOptions.SUBHEADER_ROW)[3:] == [
            "score", "status", "reviewer", "score", "status", "score", "status", "reviewer",
        ]

    def test_gather_worksheet_data(self) -> None:
        table, ws = make_table([("a", False), ("b", True)])
        ws.cells.update({(STUDENT_ROW, 4): "10", (STUDENT_ROW, 5): "+", (STUDENT_ROW, 6): "5", (STUDENT_ROW, 8): "t"})
        # NB: _gather_worksheet_data skips the first students row
        ws.cells.update({(STUDENT_ROW + 1, c): v for (r, c), v in ws.cells.items() if r == STUDENT_ROW})
        [user_data] = table._gather_worksheet_data()
        assert user_data["tasks"] == {"a": ("10", "+", ""), "b": ("5", "", "t")}

class TestStoreScore:
    def test_none_reviewer_accepts_on_pass(self) -> None:
        task = ManytaskTaskConfig(task="a", score=10, reviewer_mode=ManytaskReviewerMode.NONE)
        table, ws = make_table([("a", False), ("b", True)])
        table.update_reviewers_list(["reviewer"])

        status = store(table, task, TaskReviewStatus.SOLVED_WITH_MR)

        assert status.score == 10
        assert status.review == f"'{TaskReviewStatus.ACCEPTED.value}"
        assert status.reviewer is None
        # nothing is written to the next task columns
        assert (STUDENT_ROW, 6) not in ws.cells
        assert table.get_reviews(STUDENT.username)["a"].status == TaskReviewStatus.ACCEPTED
        assert table.get_scores(STUDENT.username) == {"a": 10}

    def test_none_reviewer_with_legacy_reviewer_column(self) -> None:
        task = ManytaskTaskConfig(task="a", score=10, reviewer_mode=ManytaskReviewerMode.NONE)
        table, ws = make_table([("a", True)])
        table.update_reviewers_list(["reviewer"])

        status = store(table, task, TaskReviewStatus.SOLVED_WITH_MR)

        assert status.review == f"'{TaskReviewStatus.ACCEPTED.value}"
        assert status.reviewer is None
        assert (STUDENT_ROW, 6) not in ws.cells

    def test_concrete_reviewer(self) -> None:
        task = ManytaskTaskConfig(
            task="a", score=10, reviewer_mode=ManytaskReviewerMode.CONCRETE, reviewer_login="teacher"
        )
        table, ws = make_table([("a", True)])
        table.update_reviewers_list(["reviewer"])

        status = store(table, task, TaskReviewStatus.SOLVED_WITH_MR)

        assert status.review == f"'{TaskReviewStatus.SOLVED_WITH_MR.value}"
        assert status.reviewer == "teacher"
        assert ws.cells[(STUDENT_ROW, 6)] == "teacher"
        # round-robin queue is untouched
        assert table._get_reviewers_queue() == ["reviewer"]

    def test_concrete_reviewer_without_merge_request(self) -> None:
        task = ManytaskTaskConfig(
            task="a", score=10, reviewer_mode=ManytaskReviewerMode.CONCRETE, reviewer_login="teacher"
        )
        table, ws = make_table([("a", True)])

        status = store(table, task, TaskReviewStatus.SOLVED)

        assert status.reviewer is None
        assert (STUDENT_ROW, 6) not in ws.cells

    def test_any_reviewer(self) -> None:
        task = ManytaskTaskConfig(task="a", score=10)
        table, ws = make_table([("a", True)])
        table.update_reviewers_list(["r1", "r2"], check_order=False)

        status = store(table, task, TaskReviewStatus.SOLVED_WITH_MR)

        assert status.review == f"'{TaskReviewStatus.SOLVED_WITH_MR.value}"
        assert status.reviewer == "r1"
        assert table._get_reviewers_queue() == ["r2", "r1"]
