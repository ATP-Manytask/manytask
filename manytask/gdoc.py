from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import islice
from typing import Any, Callable, Iterable

import gspread
from authlib.integrations.requests_client import AssertionSession
from cachelib import BaseCache
from google.auth.credentials import AnonymousCredentials
from gspread import Cell as GCell
from gspread.utils import ValueInputOption, ValueRenderOption, a1_to_rowcol, rowcol_to_a1

from .config import (
    ManytaskConfig,
    ManytaskDeadlinesConfig,
    ManytaskReviewerMode,
    ManytaskTaskConfig,
    TaskReviewInfo,
    TaskReviewStatus,
)
from .course import get_current_time
from .glab import Student


logger = logging.getLogger(__name__)


GROUP_ROW_FORMATTING = {
    "backgroundColor": {
        "red": 182.0 / 255.0,
        "green": 215.0 / 255.0,
        "blue": 168.0 / 255.0,
    },
    "borders": {
        "bottom": {
            "style": "SOLID",
        },
    },
    "textFormat": {
        "fontFamily": "Amatic SC",
        "fontSize": 24,
        "bold": True,
    },
}

HEADER_ROW_FORMATTING = {
    "backgroundColor": {
        "red": 217.0 / 255.0,
        "green": 234.0 / 255.0,
        "blue": 211.0 / 255.0,
    },
    "borders": {
        "bottom": {
            "style": "SOLID",
        },
    },
    "textFormat": {
        "fontFamily": "Comfortaa",
        "fontSize": 10,
        "bold": True,
    },
}


# NB: numeration start with 1
@dataclass
class PublicAccountsSheetOptions:
    GROUPS_ROW: int = 1
    MAX_SCORES_ROW: int = 2
    HEADER_ROW: int = 3
    SUBHEADER_ROW: int = 4
    STUDENTS_START_ROW: int = 5

    GITLAB_COLUMN: int = 1
    LOGIN_COLUMN: int = 2
    NAME_COLUMN: int = 3
    TASK_SCORES_START_COLUMN: int = 4

    # task occupies score and status columns, plus reviewer column if the task is reviewed
    COLUMNS_PER_TASK: int = 3
    COLUMNS_PER_TASK_WITHOUT_REVIEWER: int = 2

    SCORE_SUBHEADER: str = "score"
    STATUS_SUBHEADER: str = "status"
    REVIEWER_SUBHEADER: str = "reviewer"

    @staticmethod
    def columns_per_task(has_reviewer: bool) -> int:
        if has_reviewer:
            return PublicAccountsSheetOptions.COLUMNS_PER_TASK
        return PublicAccountsSheetOptions.COLUMNS_PER_TASK_WITHOUT_REVIEWER


@dataclass
class TaskColumns:
    # NB: numeration start with 1
    score: int
    has_reviewer: bool

    @property
    def status(self) -> int:
        return self.score + 1

    @property
    def reviewer(self) -> int | None:
        return self.score + 2 if self.has_reviewer else None

    @property
    def last(self) -> int:
        return self.score + PublicAccountsSheetOptions.columns_per_task(self.has_reviewer) - 1


class LoginNotFound(KeyError):
    pass


class TaskNotFound(KeyError):
    pass


class GoogleDocApi:
    def __init__(
        self,
        base_url: str,
        gdoc_credentials: dict[str, Any],
        public_worksheet_id: str,
        public_scoreboard_sheet: int,
        cache: BaseCache,
    ):
        """
        :param base_url:
        :param gdoc_credentials:
        :param public_worksheet_id:
        :param public_scoreboard_sheet:
        :param cache:
        """
        self._url = base_url
        self._gdoc_credentials = gdoc_credentials
        self._public_worksheet_id = public_worksheet_id
        self._public_scoreboard_sheet = public_scoreboard_sheet

        self._assertion_session = self._create_assertion_session()

        self._public_scores_sheet = self._get_sheet(public_worksheet_id, public_scoreboard_sheet)
        self._cache = cache

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

    def _get_sheet(
        self,
        worksheet_id: str,
        sheet_id: int,
    ) -> gspread.Worksheet:
        gs: gspread.Client = gspread.Client(AnonymousCredentials(), session=self._assertion_session)
        worksheet: gspread.Spreadsheet = gs.open_by_key(worksheet_id)
        return worksheet.get_worksheet(sheet_id)

    def fetch_rating_table(self) -> "RatingTable":
        return RatingTable(self._public_scores_sheet, self._cache)

    def get_spreadsheet_url(self) -> str:
        return f"{self._url}/spreadsheets/d/{self._public_worksheet_id}#gid={self._public_scoreboard_sheet}"


class RatingTable:
    class SubmissionStatus:
        def __init__(self, score: int, review: str, reviewer: str | None):
            self.score = score
            self.review = review
            self.reviewer = reviewer
    
    def __init__(
        self,
        worksheet: gspread.Worksheet,
        cache: BaseCache,
    ):
        self._cache = cache
        self.ws = worksheet

    def get_scores(
        self,
        username: str,
    ) -> dict[str, int]:
        scores = self._cache.get(f"{self.ws.id}:scores:{username}")
        if scores is None:
            scores = {}
        # logger.info(f"scores for {username}: {scores}")
        return scores

    def update_reviewers_list(
        self,
        reviewers: list[str],
        check_order: bool = True,
    ) -> None:
        updated_reviewers = []
        if check_order:
            current_reviewers = self._get_reviewers_queue()
            preserved_reviewers = [name for name in current_reviewers if name in reviewers]
            new_reviewers = [name for name in reviewers if not (name in current_reviewers)]
            updated_reviewers = new_reviewers + preserved_reviewers
        else:
            updated_reviewers = reviewers
        self._cache.set(f"{self.ws.id}:reviewers", updated_reviewers)

    def pop_reviewer(
        self,
    ) -> str | None:
        current_reviewers = self._get_reviewers_queue()
        if not current_reviewers:
            return None
        logger.info(f"Reviewers order = {current_reviewers}")
        reviewer = current_reviewers[0]
        self.update_reviewers_list(current_reviewers[1:] + [reviewer], check_order=False)
        return reviewer
    
    def _get_reviewers_queue(
        self,
    ) -> list[str]:
        reviewers = self._cache.get(f"{self.ws.id}:reviewers")
        if reviewers is None:
            reviewers = []
        return reviewers

    def update_scores(
        self,
        username: str,
        scores_data: dict[str, int],
    ) -> None:
        self._cache.set(f"{self.ws.id}:scores:{username}", scores_data)
    
    def get_reviews(
        self,
        username: str,
    ) -> dict[str, TaskReviewInfo]:
        reviews = self._cache.get(f"{self.ws.id}:reviews:{username}")
        if reviews is None:
            reviews = {}
        return reviews

    def update_reviews(
        self,
        username: str,
        reviews_data: dict[str, TaskReviewInfo],
    ) -> None:
        self._cache.set(f"{self.ws.id}:reviews:{username}", reviews_data)

    def get_bonus_score(
        self,
        username: str,
    ) -> int:
        bonus_scores = self._cache.get(f"{self.ws.id}:bonus")
        if bonus_scores is None:
            return 0
        return bonus_scores.get(username, 0)

    def get_all_scores_reviews(self) -> dict[str, dict[str, tuple[int, TaskReviewInfo, str | None]]]:
        all_scores = self._cache.get(f"{self.ws.id}:scores_reviews")
        if all_scores is None:
            all_scores = {}
        return all_scores

    def get_stats(self) -> dict[str, float]:
        stats = self._cache.get(f"{self.ws.id}:stats")
        if stats is None:
            stats = {}
        return stats

    def get_scores_update_timestamp(self) -> str:
        timestamp = self._cache.get(f"{self.ws.id}:update-timestamp")
        if timestamp is None:
            timestamp = "None"
        return timestamp

    def _gather_worksheet_data(self) -> list:
        raw_values = self.ws.get_values()
        # logger.info(f"raw_values: {raw_values}")
        # logger.info(f"raw_values len: {len(raw_values)}")
        if len(raw_values) < PublicAccountsSheetOptions.STUDENTS_START_ROW:
            return list()

        result = list()
        header = raw_values[PublicAccountsSheetOptions.HEADER_ROW - 1]
        subheader = raw_values[PublicAccountsSheetOptions.SUBHEADER_ROW - 1]
        tasks_columns = self._parse_tasks_columns(header, subheader)
        # logger.info(f"header: {header}")

        for row in raw_values[PublicAccountsSheetOptions.STUDENTS_START_ROW:]:
            user_data = {"params": dict(), "tasks": dict()}
            # logger.info(f"user: {row[PublicAccountsSheetOptions.LOGIN_COLUMN - 1]}")
            for index, value in enumerate(row[:PublicAccountsSheetOptions.TASK_SCORES_START_COLUMN - 1]):
                user_data["params"][header[index]] = value
            for task_name, columns in tasks_columns.items():
                user_data["tasks"][task_name] = self._get_task_values(row, columns)
            result.append(user_data)
        return result

    @staticmethod
    def _parse_tasks_columns(header: list[Any], subheader: list[Any]) -> dict[str, TaskColumns]:
        """Find tasks columns by header (task names) and subheader (score/status/reviewer) rows"""
        tasks_columns = {}
        for index in range(PublicAccountsSheetOptions.TASK_SCORES_START_COLUMN - 1, len(header)):
            task_name = str(header[index]) if header[index] is not None else ""
            if not task_name:
                continue
            reviewer_index = index + 2
            has_reviewer = (
                reviewer_index < len(subheader)
                and str(subheader[reviewer_index]) == PublicAccountsSheetOptions.REVIEWER_SUBHEADER
            )
            tasks_columns[task_name] = TaskColumns(score=index + 1, has_reviewer=has_reviewer)
        return tasks_columns

    @staticmethod
    def _get_task_values(row: list[Any], columns: TaskColumns) -> tuple[Any, Any, Any]:
        """Get (score, status, reviewer) of the task from student row, missing values are empty strings"""
        def get(col: int | None) -> Any:
            if col is None or col - 1 >= len(row):
                return ""
            return row[col - 1]
        return get(columns.score), get(columns.status), get(columns.reviewer)

    def _get_tasks_columns(self) -> dict[str, TaskColumns]:
        header = self._get_row_values(PublicAccountsSheetOptions.HEADER_ROW)
        subheader = self._get_row_values(PublicAccountsSheetOptions.SUBHEADER_ROW)
        return self._parse_tasks_columns(list(header), list(subheader))

    def update_cached_scores(self) -> None:
        _current_timestamp = get_current_time()

        processed_data = self._gather_worksheet_data()
        # logger.info(f"processed_data: {processed_data}")

        all_scores_and_reviews = {
            user_data["params"]["login"]: {
                k: (int(v[0]),  TaskReviewInfo.from_string(v[1] if len(v) > 1 else ""), v[2] if len(v) > 2 and v[2] else None) 
                for k, v in user_data["tasks"].items()
                if len(v[0]) > 0
            }
            for user_data in processed_data
        }
        # logger.info(f"all_scores_and_reviews: {all_scores_and_reviews}")
        
        users_score_cache = {}
        for username, user_data in all_scores_and_reviews.items():
            scores = {}
            reviews = {}
            for task, data in user_data.items():
                scores[task] = data[0]
                reviews[task] = data[1]
            users_score_cache[f"{self.ws.id}:scores:{username}"] = scores
            users_score_cache[f"{self.ws.id}:reviews:{username}"] = reviews

        # logger.info(f"{users_score_cache}: users_score_cache")
        all_users_bonus_scores = {
            user_data["params"]["login"]: int(user_data["params"].get("bonus", "")) if user_data["params"].get("bonus", "") else 0
            for user_data in processed_data
        }

        # clear cache saving config
        _config = self._cache.get("__config__")
        config = ManytaskConfig(**_config)

        # get all tasks stats
        _tasks_stats: defaultdict[str, int] = defaultdict(int)
        for tasks in all_scores_and_reviews.values():
            for task_name in tasks.keys():
                _tasks_stats[task_name] += 1
        tasks_stats: dict[str, float] = {
            task.name: (_tasks_stats[task.name] / len(all_scores_and_reviews) if len(all_scores_and_reviews) != 0 else 0)
            for task in config.get_tasks(enabled=True, started=True)
        }

        reviewers_order = self._cache.get(f"{self.ws.id}:reviewers")

        self._cache.clear()
        self._cache.set("__config__", _config)
        self._cache.set(f"{self.ws.id}:scores_reviews", all_scores_and_reviews)
        self._cache.set(f"{self.ws.id}:bonus", all_users_bonus_scores)
        self._cache.set(f"{self.ws.id}:stats", tasks_stats)
        self._cache.set(f"{self.ws.id}:update-timestamp", _current_timestamp)
        self._cache.set(f"{self.ws.id}:reviewers", reviewers_order)
        self._cache.set_many(users_score_cache)

    def store_score(
        self,
        student: Student,
        task: ManytaskTaskConfig,
        update_fn: Callable[..., Any],
        review: TaskReviewStatus,
    ) -> SubmissionStatus:
        try:
            student_row = self._find_login_row(student.username)
        except LoginNotFound:
            student_row = self._add_student_row(student)

        tasks_columns = self._get_tasks_columns()
        task_columns = self._find_task_columns(task.name, tasks_columns)

        score_cell = self.ws.cell(student_row, task_columns.score)
        old_score: int | None = int(score_cell.value) if score_cell.value else None

        review_cell = self.ws.cell(student_row, task_columns.status)
        old_review = TaskReviewInfo.from_string(review_cell.value if review_cell.value else "")

        reviewer_cell: GCell | None = None
        old_reviewer: str | None = None
        if task_columns.reviewer is not None:
            reviewer_cell = self.ws.cell(student_row, task_columns.reviewer)
            old_reviewer = reviewer_cell.value if reviewer_cell.value else None

        if not TaskReviewStatus.is_review_status(review) and old_score is None:
            new_score = update_fn("")
            score_cell.value = new_score
            logger.info(f"Setting score = {new_score}")
        else:
            new_score = old_score if old_score else 0

        # task without review is accepted as soon as tests are passed
        if task.reviewer_mode == ManytaskReviewerMode.NONE and not TaskReviewStatus.is_review_status(review):
            new_review = self._format_review(old_review, TaskReviewStatus.ACCEPTED)
        else:
            new_review = self._format_review(old_review, review)
        review_cell.value = new_review
        logger.info(f"Setting review = {new_review}")

        if task.has_reviewer and reviewer_cell is None:
            logger.warning(f"Task {task.name} has no reviewer column in spreadsheet, reviewer is not set")

        new_reviewer = old_reviewer
        if (
            task.has_reviewer
            and reviewer_cell is not None
            and new_reviewer is None
            and review == TaskReviewStatus.SOLVED_WITH_MR
        ):
            if task.reviewer_mode == ManytaskReviewerMode.CONCRETE:
                new_reviewer = task.reviewer_login
            else:
                new_reviewer = self.pop_reviewer()
            if not (new_reviewer is None):
                reviewer_cell.value = new_reviewer
                logger.info(f"Setting reviewer = {new_reviewer}")
            else:
                logger.warning("No reviewers found")

        repo_link_cell = GCell(
            student_row,
            PublicAccountsSheetOptions.GITLAB_COLUMN,
            self.create_student_repo_link(student),
        )
        cells_to_update = [repo_link_cell, score_cell, review_cell]
        if reviewer_cell is not None:
            cells_to_update.append(reviewer_cell)
        self.ws.update_cells(
            cells_to_update,
            value_input_option=ValueInputOption.user_entered,
        )

        row_values = list(self._get_row_values(student_row))
        student_scores = {}
        student_reviews = {}
        for task_name, columns in tasks_columns.items():
            score, review_value, _ = self._get_task_values(row_values, columns)
            if score or str(score) == "0":
                student_scores[task_name] = score
            if review_value:
                student_reviews[task_name] = TaskReviewInfo.from_string(review_value)

        logger.info(f"Actual scores: {student_scores}")
        logger.info(f"Actual reviews: {student_reviews}")

        self.update_scores(student.username, student_scores)
        self.update_reviews(student.username, student_reviews)
        return self.SubmissionStatus(new_score, new_review, new_reviewer)

    def sync_columns(
        self,
        deadlines_config: ManytaskDeadlinesConfig,
    ) -> None:
        max_score = deadlines_config.max_score_started
        groups = deadlines_config.get_groups(enabled=True, started=True)
        tasks = deadlines_config.get_tasks(enabled=True, started=True)
        task_name_to_group_name = {task.name: group.name for group in groups for task in group.tasks if task in tasks}

        # TODO: maintain group orger when adding new task in added group
        logger.info("Syncing rating columns...")
        existing_tasks_columns = self._get_tasks_columns()
        tasks_to_create = [task for task in tasks if task.name not in existing_tasks_columns]

        current_worksheet_size = max(
            [columns.last for columns in existing_tasks_columns.values()],
            default=PublicAccountsSheetOptions.TASK_SCORES_START_COLUMN - 1,
        )
        required_worksheet_size = current_worksheet_size
        if tasks_to_create:
            required_worksheet_size = current_worksheet_size + sum(
                PublicAccountsSheetOptions.columns_per_task(task.has_reviewer) for task in tasks_to_create
            )

            self.ws.resize(cols=required_worksheet_size)

            cells_to_update = []
            current_group = None
            col = current_worksheet_size + 1
            for task in tasks_to_create:
                columns = TaskColumns(score=col, has_reviewer=task.has_reviewer)
                cells_to_update.append(GCell(PublicAccountsSheetOptions.HEADER_ROW, columns.score, task.name))
                cells_to_update.append(
                    GCell(
                        PublicAccountsSheetOptions.SUBHEADER_ROW,
                        columns.score,
                        PublicAccountsSheetOptions.SCORE_SUBHEADER,
                    )
                )
                cells_to_update.append(
                    GCell(
                        PublicAccountsSheetOptions.SUBHEADER_ROW,
                        columns.status,
                        PublicAccountsSheetOptions.STATUS_SUBHEADER,
                    )
                )
                if columns.reviewer is not None:
                    cells_to_update.append(
                        GCell(
                            PublicAccountsSheetOptions.SUBHEADER_ROW,
                            columns.reviewer,
                            PublicAccountsSheetOptions.REVIEWER_SUBHEADER,
                        )
                    )
                cells_to_update.append(GCell(PublicAccountsSheetOptions.MAX_SCORES_ROW, columns.score, str(task.score)))
                col = columns.last + 1

                task_group_name = task_name_to_group_name[task.name]

                if task_group_name != current_group:
                    cells_to_update.append(GCell(PublicAccountsSheetOptions.GROUPS_ROW, col, task_group_name))
                    current_group = task_group_name
        else:
            cells_to_update = []

        if cells_to_update:
            self.ws.update_cells(cells_to_update, value_input_option=ValueInputOption.user_entered)

            self.ws.format(
                f"{rowcol_to_a1(PublicAccountsSheetOptions.GROUPS_ROW, PublicAccountsSheetOptions.TASK_SCORES_START_COLUMN)}:"  # noqa: E501
                f"{rowcol_to_a1(PublicAccountsSheetOptions.GROUPS_ROW, required_worksheet_size)}",
                GROUP_ROW_FORMATTING,
            )
            self.ws.format(
                f"{rowcol_to_a1(PublicAccountsSheetOptions.MAX_SCORES_ROW, PublicAccountsSheetOptions.TASK_SCORES_START_COLUMN)}:"  # noqa: E501
                f"{rowcol_to_a1(PublicAccountsSheetOptions.MAX_SCORES_ROW, required_worksheet_size)}",
                HEADER_ROW_FORMATTING,
            )
            self.ws.format(
                f"{rowcol_to_a1(PublicAccountsSheetOptions.HEADER_ROW, PublicAccountsSheetOptions.TASK_SCORES_START_COLUMN)}:"  # noqa: E501
                f"{rowcol_to_a1(PublicAccountsSheetOptions.HEADER_ROW, required_worksheet_size)}",
                HEADER_ROW_FORMATTING,
            )
            self.ws.format(
                f"{rowcol_to_a1(PublicAccountsSheetOptions.SUBHEADER_ROW, PublicAccountsSheetOptions.TASK_SCORES_START_COLUMN)}:"  # noqa: E501
                f"{rowcol_to_a1(PublicAccountsSheetOptions.SUBHEADER_ROW, required_worksheet_size)}",
                HEADER_ROW_FORMATTING,
            )

    def _get_row_values(
        self,
        row: int,
        start: int | None = None,
        step: int | None = None,
        with_index: bool = False,
    ) -> Iterable[Any]:
        values: Iterable[Any] = self.ws.row_values(row, value_render_option=ValueRenderOption.unformatted)
        if with_index:
            values = enumerate(values, start=1)
        if start:
            step = step if step else 1
            values = islice(values, start, None, step)
        return values

    @staticmethod
    def _find_task_columns(
        task: str,
        tasks_columns: dict[str, TaskColumns],
    ) -> TaskColumns:
        logger.info(f'Looking for task "{task}"...')
        logger.info(list(tasks_columns.keys()))
        if task in tasks_columns:
            return tasks_columns[task]
        raise TaskNotFound(f'Task "{task}" not found in spreadsheet')

    def _find_login_row(
        self,
        login: str,
    ) -> int:
        logger.info(f'Looking for student "{login}"...')
        all_logins = self.ws.col_values(
            PublicAccountsSheetOptions.LOGIN_COLUMN,
            value_render_option=ValueRenderOption.unformatted,
        )

        for row, found_login in islice(enumerate(all_logins, start=1), PublicAccountsSheetOptions.HEADER_ROW, None):
            if str(found_login) == login:
                return row

        raise LoginNotFound(f"Login {login} not found in spreadsheet")

    def _add_student_row(
        self,
        student: Student,
    ) -> int:
        logger.info(f'Adding student "{student.username}" with name "{student.name}"...')
        if len(student.name) == 0 or re.match(r"\W", student.name, flags=re.UNICODE):
            raise ValueError(f'Name "{student.name}" looks fishy')

        TASKS_RANGE: str = f'INDIRECT(ADDRESS(ROW(), {PublicAccountsSheetOptions.TASK_SCORES_START_COLUMN}) & ":" & ROW())'

        column_to_values_dict = {
            PublicAccountsSheetOptions.GITLAB_COLUMN: self.create_student_repo_link(student),
            PublicAccountsSheetOptions.LOGIN_COLUMN: student.username,
            PublicAccountsSheetOptions.NAME_COLUMN: student.name,
        }

        # fill empty columns with empty string
        row_values = [column_to_values_dict.get(i + 1, "") for i in range(max(column_to_values_dict.keys()))]

        result = self.ws.append_row(
            values=row_values,
            value_input_option=ValueInputOption.user_entered,  # don't escape link
            # note logical table to upend to (gdoc implicit split it to logical tables)
            table_range=f"A{PublicAccountsSheetOptions.STUDENTS_START_ROW}",
        )

        updated_range = result["updates"]["updatedRange"]
        updated_range_upper_bound = updated_range.split(":")[1]
        row_count, _ = a1_to_rowcol(updated_range_upper_bound)
        return row_count
    
    @staticmethod
    def _format_review(old_value: TaskReviewInfo, review_status: TaskReviewStatus) -> str:
        # logger.info(f"initial {old_value}, review {review_status}")
        gen_trunkated_string = lambda att: (str(att) if att > 0 else "")
        bad_attempts = old_value.bad_attempts

        if not TaskReviewStatus.is_review_status(review_status):
            if old_value.status == TaskReviewStatus.ACCEPTED:
                return f"'{TaskReviewStatus.ACCEPTED.value}{gen_trunkated_string(bad_attempts)}"
            return f"'{review_status.value}{gen_trunkated_string(bad_attempts)}"
        elif review_status == TaskReviewStatus.ACCEPTED:
            return f"'{TaskReviewStatus.ACCEPTED.value}{gen_trunkated_string(bad_attempts)}"
        else:
            if not old_value.status == TaskReviewStatus.REJECTED:
                bad_attempts += 1
            return f"'{TaskReviewStatus.REJECTED.value}{bad_attempts}"
        
    @staticmethod
    def create_student_repo_link(
        student: Student,
    ) -> str:
        return f'=HYPERLINK("{student.repo}";"git")'
