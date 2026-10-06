from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.analysis import analyze, rolling_year_start
from app.database import read_connection
from app.models import RouteAnalysis

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Contract Vehicle Route Analysis", version="1.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/v1/network/health")
def health() -> dict[str, str]:
    try:
        with read_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "ok", "database": "connected", "access": "read-only"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database connection unavailable. Check the local environment settings.") from exc


@app.get("/api/v1/network/contract-route-analysis", response_model=RouteAnalysis)
def contract_route_analysis(
    start_date: date | None = Query(default=None, alias="startDate"),
    end_date: date | None = Query(default=None, alias="endDate"),
) -> RouteAnalysis:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    end = end_date or today
    start = start_date or rolling_year_start(end)
    if start > end:
        raise HTTPException(status_code=422, detail="startDate must be on or before endDate")
    try:
        return analyze(start, end)
    except Exception as exc:
        # Keep credentials, SQL, and internal connection details out of the HTTP response.
        raise HTTPException(status_code=503, detail="Analysis could not be loaded from the configured PostgreSQL database.") from exc
