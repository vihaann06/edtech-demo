from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from app.jobs import GENERIC_ERROR, JOB_ID_RE, Job, JobManager, new_job_id
from app.pipeline.types import JobStatus
from tests.conftest import wait_for


def _job(tmp_path: Path) -> Job:
    return Job(id=new_job_id(), prompt="p", mode="live", job_dir=tmp_path / "x")


def test_job_ids_are_short_and_url_safe():
    ids = {new_job_id() for _ in range(500)}
    assert len(ids) == 500
    assert all(JOB_ID_RE.match(i) for i in ids)


def test_stage_index_is_monotonic_and_failed_keeps_it(tmp_path):
    job = _job(tmp_path)
    assert (job.status, job.stage_index) == (JobStatus.QUEUED, 0)
    job.update(JobStatus.PLANNING)
    job.update(JobStatus.DESIGNING, "Writing")
    job.update(JobStatus.RENDERING, "Rendering animation 3")
    assert job.stage_index == 3
    job.update(JobStatus.GENERATING_CODE, "Rewriting code")  # e.g. a repair path reporting an earlier status
    assert job.status == JobStatus.GENERATING_CODE
    assert job.stage_index == 3
    job.update(JobStatus.REPAIRING, "Fixing")
    assert job.stage_index == 3
    job.fail("Rendering failed.")
    assert job.status == JobStatus.FAILED
    assert job.stage_index == 3
    assert job.error == "Rendering failed."


def test_terminal_jobs_ignore_further_updates(tmp_path):
    job = _job(tmp_path)
    job.update(JobStatus.RENDERING)
    job.fail("nope")
    job.update(JobStatus.VALIDATING, "late callback")
    job.complete(video_path=tmp_path / "v.mp4", code_path=None, duration=30.0, attempts=1)
    assert job.status == JobStatus.FAILED
    assert job.error == "nope"


def test_progress_callback_cannot_mark_completed(tmp_path):
    job = _job(tmp_path)
    job.update(JobStatus.COMPLETED, None)
    job.update(JobStatus.FAILED, None)
    assert job.status == JobStatus.QUEUED


def test_complete_sets_stage_4_and_timings(tmp_path):
    job = _job(tmp_path)
    for status in (JobStatus.PLANNING, JobStatus.DESIGNING, JobStatus.GENERATING_CODE, JobStatus.RENDERING):
        job.update(status)
    job.complete(video_path=tmp_path / "v.mp4", code_path=None, duration=30.1, attempts=2)
    assert job.stage_index == 4 and job.status == JobStatus.COMPLETED
    timings = job.timings()
    assert {"queued", "planning", "designing", "generating_code", "rendering", "total"} <= set(timings)
    assert [e["status"] for e in job.timeline] == [
        "queued", "planning", "designing", "generating_code", "rendering", "completed"
    ]


def test_snapshot_is_a_copy(tmp_path):
    job = _job(tmp_path)
    snap = job.snapshot()
    job.update(JobStatus.PLANNING)
    assert snap.status == JobStatus.QUEUED
    assert len(snap.timeline) == 1


def test_manager_update_by_id_and_submit_runs_runner(isolated_settings):
    seen = []

    def runner(job: Job) -> None:
        manager.update(job.id, JobStatus.PLANNING, "thinking")
        seen.append(job.id)
        job.fail("stopped")

    manager = JobManager(runner=runner)
    try:
        job = manager.submit("What is entropy?", "live")
        wait_for(lambda: manager.get(job.id).is_terminal)
        assert seen == [job.id]
        assert manager.get(job.id).stage_index == 0
        assert job.job_dir == isolated_settings / job.id
    finally:
        manager.shutdown()


def test_crashing_runner_fails_job_with_generic_message():
    def runner(job: Job) -> None:
        raise RuntimeError("kaboom")

    manager = JobManager(runner=runner)
    try:
        job = manager.submit("x")
        wait_for(lambda: manager.get(job.id).is_terminal)
        snap = manager.snapshot(job.id)
        assert snap.error == GENERIC_ERROR
        assert "kaboom" in snap.error_detail
    finally:
        manager.shutdown()


def test_runner_that_forgets_to_finish_still_terminates():
    manager = JobManager(runner=lambda job: None)
    try:
        job = manager.submit("x")
        wait_for(lambda: manager.get(job.id).is_terminal)
        assert manager.get(job.id).status == JobStatus.FAILED
    finally:
        manager.shutdown()


def test_jobs_beyond_worker_limit_wait_in_queue():
    gate = threading.Event()

    def runner(job: Job) -> None:
        job.update(JobStatus.PLANNING)
        gate.wait(10)
        job.fail("done")

    manager = JobManager(runner=runner, max_workers=1)
    try:
        first = manager.submit("one")
        wait_for(lambda: manager.get(first.id).status == JobStatus.PLANNING)
        second = manager.submit("two")
        time.sleep(0.1)
        snap = manager.snapshot(second.id)
        assert snap.status == JobStatus.QUEUED
        assert snap.detail and "Waiting" in snap.detail
        gate.set()
        wait_for(lambda: manager.get(second.id).is_terminal)
    finally:
        gate.set()
        manager.shutdown()


def _make_job_dir(root: Path, name: str, age_hours: float, *, with_prompt: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if with_prompt:
        (d / "prompt.txt").write_text("p\n")
    (d / "final.mp4").write_bytes(b"x")
    old = time.time() - age_hours * 3600
    os.utime(d, (old, old))
    return d


def test_cleanup_removes_expired_jobs_and_dirs(isolated_settings):
    jobs_dir = isolated_settings
    manager = JobManager(runner=lambda job: None, ttl_hours=6)
    try:
        # In-memory, finished, old -> removed from memory and disk.
        old_job = manager.create("old")
        old_job.job_dir.mkdir(parents=True)
        (old_job.job_dir / "prompt.txt").write_text("old\n")
        old_job.fail("x")
        old_job.created_at = time.time() - 7 * 3600
        # In-memory, finished, recent -> kept.
        new_job = manager.create("new")
        new_job.job_dir.mkdir(parents=True)
        (new_job.job_dir / "prompt.txt").write_text("new\n")
        new_job.fail("x")
        # In-memory, still running, old -> kept (never pull the rug from under a worker).
        running = manager.create("running")
        running.update(JobStatus.RENDERING)
        running.created_at = time.time() - 7 * 3600
        # On disk only (previous server run).
        orphan_old = _make_job_dir(jobs_dir, new_job_id(), age_hours=8)
        orphan_recent = _make_job_dir(jobs_dir, new_job_id(), age_hours=1)
        not_a_job = _make_job_dir(jobs_dir, "Important-Stuff", age_hours=100)
        no_marker = _make_job_dir(jobs_dir, new_job_id(), age_hours=100, with_prompt=False)

        removed = manager.cleanup_expired()

        assert removed == 2
        assert manager.get(old_job.id) is None and not old_job.job_dir.exists()
        assert manager.get(new_job.id) is not None and new_job.job_dir.exists()
        assert manager.get(running.id) is not None
        assert not orphan_old.exists()
        assert orphan_recent.exists()
        assert not_a_job.exists() and no_marker.exists()
    finally:
        manager.shutdown()


def test_start_runs_cleanup_immediately(isolated_settings):
    orphan = _make_job_dir(isolated_settings, new_job_id(), age_hours=10)
    manager = JobManager(runner=lambda job: None, ttl_hours=6)
    try:
        manager.start()
        assert not orphan.exists()
    finally:
        manager.shutdown()
