import json
from pathlib import Path

from fastapi import APIRouter

from app.errors import ApiError

router = APIRouter()
REPORT_PATH = Path(__file__).resolve().parents[2] / "eval" / "report.json"


@router.get("/api/eval")
def evaluation_report():
    try:
        return json.loads(REPORT_PATH.read_text())
    except FileNotFoundError as error:
        raise ApiError(
            404, "NOT_FOUND", "Not Found", "Calibration report is not available."
        ) from error
