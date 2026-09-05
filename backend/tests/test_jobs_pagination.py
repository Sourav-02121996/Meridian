"""Coverage for GET /api/workspaces/{workspace_id}/jobs pagination.

page/page_size range enforcement (Query(ge=..., le=...)) relies on FastAPI's own
request validation and isn't exercised here, since these tests call the route
function directly rather than through a TestClient — consistent with the
existing untested min_score bound in this same route.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.export import build_jobs_workbook
from app.models import Job, JobStatus, Workspace
from app.routes.jobs import export_jobs, list_jobs


def _memory_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _workspace(session_factory) -> int:
    with session_factory() as db:
        workspace = Workspace(
            name="Software Engineer",
            resume_file=b"%PDF fixture",
            resume_filename="resume.pdf",
            profile_name="Jane Doe",
            profile_email="jane@example.com",
        )
        db.add(workspace)
        db.commit()
        return workspace.id


# min_score's route signature default is `Query(None, ...)` — FastAPI resolves that
# to None for real requests, but calling the route function directly (this repo's
# test convention, see test_phase2_retry.py) leaves the raw Query sentinel in place
# unless a caller passes an explicit value, which breaks the query. These wrappers
# supply it so call sites below don't have to repeat `min_score=None` everywhere.
def _list_jobs(workspace_id, **kwargs):
    kwargs.setdefault("min_score", None)
    return list_jobs(workspace_id, **kwargs)


def _export_jobs(workspace_id, **kwargs):
    kwargs.setdefault("min_score", None)
    return export_jobs(workspace_id, **kwargs)


def _seed_jobs(session_factory, workspace_id: int, jobs: list[dict]) -> list[int]:
    with session_factory() as db:
        rows = []
        for i, overrides in enumerate(jobs):
            row = Job(
                workspace_id=workspace_id,
                external_id=f"job-{i}",
                title=overrides.pop("title", f"Engineer {i}"),
                company=overrides.pop("company", "Acme"),
                **overrides,
            )
            db.add(row)
            rows.append(row)
        db.commit()
        return [row.id for row in rows]


def test_list_jobs_slices_items_and_reports_total():
    session_factory = _memory_sessionmaker()
    workspace_id = _workspace(session_factory)
    _seed_jobs(session_factory, workspace_id, [{"score": s} for s in range(10)])

    with session_factory() as db:
        page1 = _list_jobs(
            workspace_id, sort="score", order="desc", page=1, page_size=4, db=db
        )
        page2 = _list_jobs(
            workspace_id, sort="score", order="desc", page=2, page_size=4, db=db
        )

    assert page1["total"] == 10
    assert page1["page"] == 1
    assert page1["page_size"] == 4
    assert [job.score for job in page1["items"]] == [9, 8, 7, 6]
    assert [job.score for job in page2["items"]] == [5, 4, 3, 2]


def test_list_jobs_beyond_last_page_returns_empty_items_but_correct_total():
    session_factory = _memory_sessionmaker()
    workspace_id = _workspace(session_factory)
    _seed_jobs(session_factory, workspace_id, [{"score": s} for s in range(3)])

    with session_factory() as db:
        result = _list_jobs(workspace_id, page=5, page_size=10, db=db)

    assert result["items"] == []
    assert result["total"] == 3


def test_list_jobs_combined_filters_hold_across_pages():
    session_factory = _memory_sessionmaker()
    workspace_id = _workspace(session_factory)
    _seed_jobs(
        session_factory,
        workspace_id,
        [
            {"title": "Backend Engineer", "score": 90, "status": JobStatus.discovered},
            {"title": "Backend Engineer", "score": 80, "status": JobStatus.discovered},
            {"title": "Backend Engineer", "score": 40, "status": JobStatus.discovered},
            {"title": "Frontend Engineer", "score": 95, "status": JobStatus.discovered},
            {"title": "Backend Engineer", "score": 85, "status": JobStatus.applied},
        ],
    )

    with session_factory() as db:
        page1 = list_jobs(
            workspace_id,
            status=JobStatus.discovered,
            min_score=50,
            q="Backend",
            sort="score",
            order="desc",
            page=1,
            page_size=1,
            db=db,
        )
        page2 = list_jobs(
            workspace_id,
            status=JobStatus.discovered,
            min_score=50,
            q="Backend",
            sort="score",
            order="desc",
            page=2,
            page_size=1,
            db=db,
        )

    # Only the two discovered "Backend Engineer" jobs scoring >= 50 match.
    assert page1["total"] == 2
    assert page2["total"] == 2
    assert [job.score for job in page1["items"]] == [90]
    assert [job.score for job in page2["items"]] == [80]


def test_list_jobs_tiebreaker_keeps_pages_disjoint_and_exhaustive():
    session_factory = _memory_sessionmaker()
    workspace_id = _workspace(session_factory)
    seeded_ids = set(
        _seed_jobs(session_factory, workspace_id, [{"score": 50} for _ in range(5)])
    )

    with session_factory() as db:
        pages = [
            _list_jobs(workspace_id, sort="score", order="desc", page=p, page_size=2, db=db)
            for p in (1, 2, 3)
        ]

    seen_ids: list[int] = []
    for page in pages:
        seen_ids.extend(job.id for job in page["items"])

    assert len(seen_ids) == len(set(seen_ids)), "same job returned on more than one page"
    assert set(seen_ids) == seeded_ids


def test_export_jobs_ignores_pagination_and_returns_all_matching_rows(monkeypatch):
    session_factory = _memory_sessionmaker()
    workspace_id = _workspace(session_factory)
    _seed_jobs(session_factory, workspace_id, [{"score": s} for s in range(25)])

    captured: dict = {}

    def fake_build_jobs_workbook(jobs):
        captured["jobs"] = list(jobs)
        return build_jobs_workbook(jobs)

    monkeypatch.setattr("app.routes.jobs.build_jobs_workbook", fake_build_jobs_workbook)

    with session_factory() as db:
        _export_jobs(workspace_id, db=db)

    assert len(captured["jobs"]) == 25
