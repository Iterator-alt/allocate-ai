"""Unit tests for run artifacts helpers and endpoint."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

from src.api.v1.runs import (
    _build_artifact_status,
    _compute_run_duration_seconds,
    _list_artifact_files,
    _stage1_ran,
    DEBUG_BUNDLE_MAP,
)


@pytest.fixture
def artifact_paths(tmp_path, monkeypatch):
    """Redirect artifact storage to a temporary directory."""

    def run_dir(run_id: int) -> str:
        path = tmp_path / f"run_{run_id}"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def zip_path(run_id: int) -> str:
        return str(tmp_path / f"run_{run_id}.zip")

    monkeypatch.setattr("src.api.v1.runs._artifact_run_dir", run_dir)
    monkeypatch.setattr("src.api.v1.runs._artifact_zip_path", zip_path)
    return tmp_path


class TestArtifactHelpers:
    def test_stage1_ran_detects_indicator_file(self):
        assert _stage1_ran({"01_industry_resolution.json"}) is True
        assert _stage1_ran(set()) is False

    def test_list_artifact_files_from_directory(self, artifact_paths):
        run_id = 7
        run_dir = artifact_paths / f"run_{run_id}"
        (run_dir / "01_industry_resolution.json").write_text("{}", encoding="utf-8")
        (run_dir / "08_final_result.json").write_text("{}", encoding="utf-8")

        files = _list_artifact_files(run_id)
        assert "01_industry_resolution.json" in files
        assert "08_final_result.json" in files

    def test_build_artifact_status_all_available(self):
        available = set(DEBUG_BUNDLE_MAP[1])
        status = _build_artifact_status(42, 1, "completed", available, stage1_ran=True)

        assert status.status == "available"
        assert status.download_available is True
        assert status.files_found == len(DEBUG_BUNDLE_MAP[1])
        assert status.missing_files == []
        assert status.download_url == "/api/v1/runs/42/debug-zip?n=1"

    def test_build_artifact_status_stage1_skipped(self):
        status = _build_artifact_status(42, 1, "completed", set(), stage1_ran=False)

        assert status.status == "unavailable"
        assert status.download_available is False
        assert "Stage 1 was skipped" in (status.message or "")

    def test_build_artifact_status_pending_run(self):
        status = _build_artifact_status(42, 3, "generating", set(), stage1_ran=True)

        assert status.status == "pending"
        assert status.download_available is False

    def test_build_artifact_status_partial_failed_run(self):
        available = {"01_industry_resolution.json", "02_brand_competitors.json"}
        status = _build_artifact_status(42, 1, "failed", available, stage1_ran=True)

        assert status.status == "partial"
        assert status.download_available is True
        assert status.missing_files
        assert "failed" in (status.message or "").lower()

    def test_compute_run_duration_completed(self):
        started = datetime.utcnow() - timedelta(seconds=120)
        completed = datetime.utcnow()
        ai_run = SimpleNamespace(
            startedAt=started,
            completedAt=completed,
            updatedAt=completed,
            status="completed",
        )

        duration = _compute_run_duration_seconds(ai_run)
        assert duration is not None
        assert 119 <= duration <= 121

    def test_compute_run_duration_failed_uses_updated_at(self):
        started = datetime.utcnow() - timedelta(seconds=30)
        updated = datetime.utcnow()
        ai_run = SimpleNamespace(
            startedAt=started,
            completedAt=None,
            updatedAt=updated,
            status="failed",
        )

        duration = _compute_run_duration_seconds(ai_run)
        assert duration is not None
        assert 29 <= duration <= 31


class TestGetRunArtifactsEndpoint:
    @pytest_asyncio.fixture
    async def mock_ai_run(self):
        return SimpleNamespace(
            status="completed",
            startedAt=datetime.utcnow() - timedelta(minutes=2),
            completedAt=datetime.utcnow(),
            updatedAt=datetime.utcnow(),
            errorMessage=None,
            traceSnapshot={
                "llm_calls_count": 5,
                "stage1_ai_calls": 4,
                "stage2_ai_calls": 1,
                "stage2_retry": False,
            },
        )

    async def test_get_run_artifacts_success(
        self,
        client: AsyncClient,
        artifact_paths,
        mock_ai_run,
    ):
        run_id = 99
        run_dir = artifact_paths / f"run_{run_id}"
        for filename in DEBUG_BUNDLE_MAP[1]:
            (run_dir / filename).write_text("x", encoding="utf-8")
        for filename in DEBUG_BUNDLE_MAP[2]:
            (run_dir / filename).write_text("x", encoding="utf-8")
        for filename in DEBUG_BUNDLE_MAP[3]:
            (run_dir / filename).write_text("x", encoding="utf-8")

        with patch(
            "src.api.v1.runs.get_ai_run_by_external_id",
            new=AsyncMock(return_value=mock_ai_run),
        ):
            response = await client.get(f"/api/v1/runs/{run_id}/artifacts")

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == run_id
        assert data["run_status"] == "completed"
        assert data["llm_calls_count"] == 5
        assert len(data["artifacts"]) == 3
        assert all(a["download_available"] for a in data["artifacts"])

    async def test_get_run_artifacts_not_found(self, client: AsyncClient):
        with patch(
            "src.api.v1.runs.get_ai_run_by_external_id",
            new=AsyncMock(return_value=None),
        ):
            response = await client.get("/api/v1/runs/404/artifacts")

        assert response.status_code == 404
