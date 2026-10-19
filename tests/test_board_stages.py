"""Task 235: board stages. preferred_model restricted to four models,
planned_date, block_task / unblock_task, repo_path inherited from the project,
and the two data migrations (0006 planned-run line, 0007 preferred_model).
"""
import asyncio
from datetime import date, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from agent_kanban.mcp_server import mcp
from agent_kanban.models import (
    PREFERRED_MODELS,
    Project,
    Task,
    TaskStatus,
    canonical_model,
)
from agent_kanban.schemas import TaskCreate, TaskUpdate
from agent_kanban.services import (
    create_task,
    get_next_task,
    list_tasks,
    today_stockholm,
)

from tests.conftest import _create_test_db, _drop_test_db
from tests.test_mcp_server import _to_dict


@pytest.fixture(autouse=True)
def _bind_mcp_to_test_db(db_url):
    from agent_kanban.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _add(session, title="t", **kw) -> Task:
    kw.setdefault("status", TaskStatus.READY)
    task = Task(title=title, **kw)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


# ---- preferred_model: four values only ----


def test_schema_accepts_only_the_four_models_or_empty():
    for name in PREFERRED_MODELS:
        assert TaskCreate(title="x", preferred_model=name).preferred_model == name
    assert TaskCreate(title="x", preferred_model="").preferred_model is None
    assert TaskCreate(title="x").preferred_model is None
    assert TaskUpdate(preferred_model="").preferred_model is None
    for bad in ("Opus", "klådan", "Chrille", "codex"):
        with pytest.raises(ValidationError) as exc:
            TaskCreate(title="x", preferred_model=bad)
        for name in PREFERRED_MODELS:
            assert name in str(exc.value)
        with pytest.raises(ValidationError):
            TaskUpdate(preferred_model=bad)


async def test_rest_rejects_other_preferred_model(authed_client):
    r = await authed_client.post("/api/tasks", json={"title": "t", "preferred_model": "Opus"})
    assert r.status_code == 422
    r = await authed_client.post("/api/tasks", json={"title": "t", "preferred_model": "Kimi"})
    assert r.status_code == 201
    assert r.json()["preferred_model"] == "Kimi"
    tid = r.json()["id"]
    r = await authed_client.patch(f"/api/tasks/{tid}", json={"preferred_model": "Chrille"})
    assert r.status_code == 422
    r = await authed_client.patch(f"/api/tasks/{tid}", json={"preferred_model": None})
    assert r.status_code == 200
    assert r.json()["preferred_model"] is None


async def test_mcp_create_task_rejects_other_preferred_model(session):
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool(
            "create_task", {"agent": "codex", "title": "x", "preferred_model": "Opus"}
        )
    for name in PREFERRED_MODELS:
        assert name in str(exc.value)


async def test_mcp_update_task_validates_and_clears_preferred_model(session):
    created = _to_dict(
        await mcp.call_tool(
            "create_task", {"agent": "codex", "title": "x", "preferred_model": "Sonnet"}
        )
    )
    task = await session.get(Task, created["id"])
    task.status = TaskStatus.IN_PROGRESS
    task.claimed_by = "codex"
    await session.commit()

    with pytest.raises(ToolError) as exc:
        await mcp.call_tool(
            "update_task",
            {"task_id": created["id"], "agent": "codex", "preferred_model": "hermes"},
        )
    for name in PREFERRED_MODELS:
        assert name in str(exc.value)

    ok = _to_dict(
        await mcp.call_tool(
            "update_task",
            {"task_id": created["id"], "agent": "codex", "preferred_model": "GLM"},
        )
    )
    assert ok["preferred_model"] == "GLM"
    cleared = _to_dict(
        await mcp.call_tool(
            "update_task",
            {"task_id": created["id"], "agent": "codex", "preferred_model": ""},
        )
    )
    assert cleared["preferred_model"] is None


def test_canonical_model_maps_agent_spellings():
    assert canonical_model("Klådan") == "Klådan"
    assert canonical_model("klada") == "Klådan"
    assert canonical_model("Glammet") == "GLM"
    assert canonical_model("kinesen") == "Kimi"
    assert canonical_model("codex") == "codex"
    assert canonical_model(None) is None


async def test_get_next_task_prefers_canonical_agent_names(session):
    other = await _add(session, "wants GLM", preferred_model="GLM")
    neutral = await _add(session, "neutral")
    mine = await _add(session, "wants Klådan", preferred_model="Klådan")
    for agent in ("Klådan", "klada"):
        first = await get_next_task(session, None, None, None, agent=agent)
        assert first.id == mine.id, agent
    first = await get_next_task(session, None, None, None, agent="Glammet")
    assert first.id == other.id
    first = await get_next_task(session, None, None, None, agent="nobody")
    assert first.id == neutral.id


# ---- planned_date ----


async def test_rest_planned_date_create_update_read(authed_client):
    r = await authed_client.post("/api/tasks", json={"title": "t", "planned_date": "2026-11-02"})
    assert r.status_code == 201
    assert r.json()["planned_date"] == "2026-11-02"
    tid = r.json()["id"]
    r = await authed_client.patch(f"/api/tasks/{tid}", json={"planned_date": "2026-11-05"})
    assert r.json()["planned_date"] == "2026-11-05"
    r = await authed_client.patch(f"/api/tasks/{tid}", json={"planned_date": None})
    assert r.json()["planned_date"] is None
    r = await authed_client.post("/api/tasks", json={"title": "t", "planned_date": "nope"})
    assert r.status_code == 422


async def test_rest_list_filters_by_planned_date(authed_client):
    for title, d in (("a", "2026-11-01"), ("b", "2026-11-10"), ("c", None)):
        await authed_client.post("/api/tasks", json={"title": title, "planned_date": d})
    r = await authed_client.get("/api/tasks", params={"planned_before": "2026-11-05"})
    assert [t["title"] for t in r.json()] == ["a"]
    r = await authed_client.get("/api/tasks", params={"planned_after": "2026-11-05"})
    assert [t["title"] for t in r.json()] == ["b"]


async def test_mcp_planned_date_create_update_filter(session):
    created = _to_dict(
        await mcp.call_tool(
            "create_task", {"agent": "codex", "title": "x", "planned_date": "2026-12-24"}
        )
    )
    assert created["planned_date"] == "2026-12-24"
    row = await session.get(Task, created["id"])
    assert row.planned_date == date(2026, 12, 24)

    with pytest.raises(ToolError) as exc:
        await mcp.call_tool(
            "create_task", {"agent": "codex", "title": "y", "planned_date": "24/12/2026"}
        )
    assert "YYYY-MM-DD" in str(exc.value)

    row.status = TaskStatus.IN_PROGRESS
    row.claimed_by = "codex"
    await session.commit()
    updated = _to_dict(
        await mcp.call_tool(
            "update_task",
            {"task_id": created["id"], "agent": "codex", "planned_date": "2026-12-31"},
        )
    )
    assert updated["planned_date"] == "2026-12-31"

    await _add(session, "undated")
    after = _to_dict(await mcp.call_tool("list_tasks", {"planned_after": "2026-12-30"}))
    assert [t["id"] for t in after] == [created["id"]]
    before = _to_dict(await mcp.call_tool("list_tasks", {"planned_before": "2026-12-30"}))
    assert before == [] or before is None
    cleared = _to_dict(
        await mcp.call_tool(
            "update_task", {"task_id": created["id"], "agent": "codex", "planned_date": ""}
        )
    )
    assert cleared["planned_date"] is None


async def test_get_next_task_skips_future_planned_date(session):
    today = today_stockholm()
    future = await _add(session, "future", planned_date=today + timedelta(days=1))
    assert await get_next_task(session, None, None, None, agent="codex") is None
    assert await get_next_task(session, None, None, None) is None

    due_today = await _add(session, "today", planned_date=today)
    undated = await _add(session, "undated")
    past = await _add(session, "past", planned_date=today - timedelta(days=3))
    seen = set()
    for _ in range(3):
        nxt = await get_next_task(session, None, None, None, agent="codex")
        assert nxt is not None and nxt.id != future.id
        seen.add(nxt.id)
        nxt.status = TaskStatus.IN_PROGRESS
        await session.commit()
    assert seen == {due_today.id, undated.id, past.id}
    assert await get_next_task(session, None, None, None, agent="codex") is None


async def test_get_next_task_mcp_skips_future_planned_date(session):
    await _add(session, "future", planned_date=today_stockholm() + timedelta(days=30))
    assert _to_dict(await mcp.call_tool("get_next_task", {})) is None


# ---- block_task / unblock_task ----


async def _block(task_id, agent="codex", reason="saknar konto", unblock_by="Chrille: skapa konto"):
    return await mcp.call_tool(
        "block_task",
        {"task_id": task_id, "agent": agent, "reason": reason, "unblock_by": unblock_by},
    )


@pytest.mark.parametrize(
    "status", [TaskStatus.TODO, TaskStatus.READY, TaskStatus.IN_PROGRESS]
)
async def test_block_task_from_open_statuses_without_claim(session, status):
    task = await _add(session, status=status, claimed_by="hermes" if status == TaskStatus.IN_PROGRESS else None)
    result = _to_dict(await _block(task.id))
    assert result["status"] == "blocked"
    assert result["blocked_by"] == "codex"
    assert result["blocked_reason"] == "saknar konto"
    assert result["unblock_by"] == "Chrille: skapa konto"

    events = _to_dict(await mcp.call_tool("list_progress", {"task_id": task.id}))
    assert len(events) == 1
    ev = events[0]
    assert ev["agent"] == "codex"
    assert ev["kind"] == "status_change"
    assert ev["payload"]["status"]["from"] == status.value
    assert ev["payload"]["status"]["to"] == "blocked"
    assert ev["payload"]["reason"] == "saknar konto"
    assert ev["payload"]["unblock_by"] == "Chrille: skapa konto"


async def test_block_task_rejects_closed_statuses_and_empty_fields(session):
    for status in (TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.REVIEW, TaskStatus.BLOCKED):
        task = await _add(session, status=status)
        with pytest.raises(ToolError):
            await _block(task.id)
    task = await _add(session)
    with pytest.raises(ToolError):
        await _block(task.id, reason="  ")
    with pytest.raises(ToolError):
        await _block(task.id, unblock_by="")
    await session.refresh(task)
    assert task.status == TaskStatus.READY


async def test_block_task_requires_matching_agent(session):
    task = await _add(session)
    with pytest.raises(ToolError):
        await _block(task.id, agent="hermes")


async def test_block_publishes_board_event(session):
    from agent_kanban.events import event_bus

    stream = event_bus.subscribe("board")
    task = await _add(session)
    await _block(task.id)
    evt = await asyncio.wait_for(stream.__anext__(), 2)
    assert evt["type"] == "task_blocked"
    assert evt["task_id"] == task.id
    assert evt["status"] == "blocked"
    await stream.aclose()


async def test_unblock_returns_to_ready_when_unclaimed(session):
    task = await _add(session, status=TaskStatus.TODO)
    await _block(task.id)
    result = _to_dict(
        await mcp.call_tool("unblock_task", {"task_id": task.id, "agent": "codex", "note": "konto finns"})
    )
    assert result["status"] == "ready"
    assert result["blocked_by"] is None
    assert result["blocked_reason"] is None
    assert result["unblock_by"] is None
    events = _to_dict(await mcp.call_tool("list_progress", {"task_id": task.id}))
    assert events[-1]["payload"]["status"]["from"] == "blocked"
    assert events[-1]["payload"]["status"]["to"] == "ready"
    assert "konto finns" in events[-1]["payload"]["content"]


async def test_unblock_returns_to_in_progress_when_claimed(session):
    task = await _add(session, status=TaskStatus.IN_PROGRESS, claimed_by="hermes")
    await _block(task.id)
    result = _to_dict(await mcp.call_tool("unblock_task", {"task_id": task.id, "agent": "codex"}))
    assert result["status"] == "in_progress"
    assert result["claimed_by"] == "hermes"


async def test_unblock_rejects_task_that_is_not_blocked(session):
    task = await _add(session)
    with pytest.raises(ToolError):
        await mcp.call_tool("unblock_task", {"task_id": task.id, "agent": "codex"})


async def test_human_moving_task_off_blocked_clears_block_notes(authed_client, session):
    task = await _add(session)
    await _block(task.id)
    r = await authed_client.patch(f"/api/tasks/{task.id}", json={"status": "ready"})
    body = r.json()
    assert body["status"] == "ready"
    assert body["blocked_reason"] is None and body["unblock_by"] is None


async def test_rest_exposes_block_notes(authed_client, session):
    task = await _add(session)
    await _block(task.id)
    r = await authed_client.get(f"/api/tasks/{task.id}")
    body = r.json()
    assert body["blocked_by"] == "codex"
    assert body["blocked_reason"] == "saknar konto"
    assert body["unblock_by"] == "Chrille: skapa konto"


# ---- repo_path inherited from the project ----


async def test_create_task_inherits_project_repo_path(session):
    project = Project(name="p", repo_path="/home/chrille/dev/projects/p")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    inherited = await create_task(session, TaskCreate(title="a", project_id=project.id))
    assert inherited.repo_path == "/home/chrille/dev/projects/p"
    explicit = await create_task(
        session, TaskCreate(title="b", project_id=project.id, repo_path="/elsewhere")
    )
    assert explicit.repo_path == "/elsewhere"
    no_project = await create_task(session, TaskCreate(title="c"))
    assert no_project.repo_path is None

    bare = Project(name="bare")
    session.add(bare)
    await session.commit()
    await session.refresh(bare)
    assert (await create_task(session, TaskCreate(title="d", project_id=bare.id))).repo_path is None


async def test_mcp_and_rest_create_task_inherit_repo_path(session, authed_client):
    project = Project(name="p", repo_path="/srv/x")
    session.add(project)
    await session.commit()
    await session.refresh(project)
    via_mcp = _to_dict(
        await mcp.call_tool("create_task", {"agent": "codex", "title": "m", "project_id": project.id})
    )
    assert via_mcp["repo_path"] == "/srv/x"
    r = await authed_client.post("/api/tasks", json={"title": "r", "project_id": project.id})
    assert r.json()["repo_path"] == "/srv/x"


# ---- data migrations ----


@pytest_asyncio.fixture
async def migration_db(monkeypatch):
    """A throwaway DB migrated to just before 0006, so old data can be seeded."""
    name = await _create_test_db()
    sync_url = f"postgresql://kanban:kanban@localhost:5436/{name}"
    monkeypatch.setenv("DATABASE_URL", sync_url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "c3d4e5f6a7b8")
    engine = sa.create_engine(sync_url)
    try:
        yield cfg, engine
    finally:
        engine.dispose()
        await _drop_test_db(name)


def _insert(conn, title, description="", preferred_model=None):
    return conn.execute(
        sa.text(
            "INSERT INTO task (title, description, status, tags, sort_order, priority, "
            "preferred_model, created_at, updated_at) VALUES (:t, :d, 'todo', '[]', 0, "
            "'medium', :p, now(), now()) RETURNING id"
        ),
        {"t": title, "d": description, "p": preferred_model},
    ).scalar_one()


async def test_migration_planned_run_line_moves_into_planned_date(migration_db):
    cfg, engine = migration_db
    with engine.begin() as conn:
        a = _insert(conn, "a", "Planerad körning: 2026-10-27\n\nGör så här.\nSedan så.")
        b = _insert(conn, "b", "Planerad körning: 2026-11-01")
        c = _insert(conn, "c", "Planerad körning: 2026-11-02\nUtan tomrad.")
        d = _insert(conn, "d", "Text först.\n\nPlanerad körning: 2026-11-03")
        e = _insert(conn, "e", "Planerad körning: 2026-13-45\n\nOgiltigt.")
        f = _insert(conn, "f", "")
    command.upgrade(cfg, "d4e5f6a7b8c9")
    with engine.connect() as conn:
        rows = {
            r.id: (r.planned_date, r.description)
            for r in conn.execute(sa.text("SELECT id, planned_date, description FROM task"))
        }
    assert rows[a] == (date(2026, 10, 27), "Gör så här.\nSedan så.")
    assert rows[b] == (date(2026, 11, 1), "")
    assert rows[c] == (date(2026, 11, 2), "Utan tomrad.")
    assert rows[d] == (None, "Text först.\n\nPlanerad körning: 2026-11-03")
    assert rows[e] == (None, "Planerad körning: 2026-13-45\n\nOgiltigt.")
    assert rows[f] == (None, "")


async def test_migration_preferred_model_is_normalised(migration_db):
    cfg, engine = migration_db
    cases = {
        "Opus": "Klådan",
        "opus": "Klådan",
        "Klådan": "Klådan",
        "Klada": "Klådan",
        "klada": "Klådan",
        "GLM": "GLM",
        "Glammet": "GLM",
        "glm": "GLM",
        "Kimi": "Kimi",
        "Kinesen": "Kimi",
        "kimi": "Kimi",
        "Sonnet": "Sonnet",
        "sonnet": "Sonnet",
        "Chrille": None,
        "codex": None,
        "": None,
    }
    with engine.begin() as conn:
        ids = {old: _insert(conn, f"t-{old}", preferred_model=old) for old in cases}
        untouched = _insert(conn, "none", preferred_model=None)
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        got = {
            r.id: r.preferred_model
            for r in conn.execute(sa.text("SELECT id, preferred_model FROM task"))
        }
    for old, new in cases.items():
        assert got[ids[old]] == new, old
    assert got[untouched] is None
    assert set(v for v in got.values() if v) <= set(PREFERRED_MODELS)


async def test_migrations_chain_ends_at_one_head():
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["e5f6a7b8c9d0"]
