from typing import Any


def update_cells_request(worksheet_id: int, row: int, column: int, values: list[Any]) -> dict[str, Any]:
    return {"updateCells": {
        "start": {"sheetId": worksheet_id, "rowIndex": row - 1, "columnIndex": column - 1},
        "rows": [{"values": [{"userEnteredValue": (
            {"numberValue": value} if isinstance(value, (int, float)) else {"stringValue": str(value)}
        )} for value in values]}],
        "fields": "userEnteredValue",
    }}
