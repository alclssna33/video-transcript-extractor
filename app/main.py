"""FastAPI 앱. 라우트는 얇게 유지하고 처리 로직은 worker에 둔다."""
import asyncio
import html
import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.asr_client import RtzrClient
from app.config import load_config
from app.db import connect, create_job, get_job, init_db, list_jobs
from app.renderer import format_timestamp
from app.worker import Worker

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(*, asr=None, poll_interval: float = 5.0) -> FastAPI:
    config = load_config()  # 자격 증명이 없으면 여기서 즉시 실패한다
    app = FastAPI(title="영상 대본 추출기")

    conn = connect(config.db_path)
    init_db(conn)

    worker = Worker(
        conn=conn,
        asr=asr or RtzrClient(config.client_id, config.client_secret),
        transcripts_dir=config.transcripts_dir,
        raw_dir=config.raw_dir,
        media_dir=config.media_dir,
        poll_interval=poll_interval,
    )
    app.state.conn = conn
    app.state.worker = worker
    # 1인용 로컬 앱이므로 동시 처리는 1건으로 제한한다.
    app.state.semaphore = asyncio.Semaphore(1)
    app.state.background_tasks: set[asyncio.Task] = set()

    VIDEO_SUFFIXES = {".mp4", ".m4a", ".mp3", ".mkv", ".mov", ".avi", ".wav", ".flac"}

    @app.on_event("startup")
    async def recover_and_scan() -> None:
        worker.recover()

        # inbox 폴더에 있는 파일 중 아직 등록되지 않은 것을 job으로 만든다.
        known_sources = {job["source"] for job in list_jobs(conn)}
        for path in sorted(config.inbox_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
                if str(path) not in known_sources:
                    create_job(
                        conn,
                        title=path.stem,
                        source=str(path),
                        source_type="file",
                    )

        for job_id in [*worker.resumable_job_ids(), *worker.fetched_job_ids(), *worker.pending_job_ids()]:
            _schedule(app, job_id)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "index.html", {"jobs": list_jobs(conn)}
        )

    @app.post("/jobs")
    async def submit(
        request: Request,
        source: str = Form(...),
        title: str = Form(...),
        keywords: str = Form(""),
        spk_count: str = Form(""),
    ):
        source = source.strip()
        if not source:
            raise HTTPException(status_code=400, detail="영상 경로 또는 URL을 입력하세요.")

        is_url = source.startswith(("http://", "https://"))
        if not is_url and not Path(source).exists():
            return HTMLResponse(
                f"<p>파일을 찾을 수 없습니다: {html.escape(source)}</p><p><a href='/'>돌아가기</a></p>",
                status_code=400,
            )

        job_id = create_job(
            conn,
            title=title.strip() or "제목없음",
            source=source,
            source_type="url" if is_url else "file",
            keywords=[k.strip() for k in keywords.split(",") if k.strip()],
            spk_count=int(spk_count) if spk_count.strip().isdigit() else None,
        )
        _schedule(app, job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def detail(request: Request, job_id: str):
        job = get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")

        raw_path = config.raw_dir / f"{job_id}.json"
        utterances = []
        speaker_ids: list[int] = []
        if raw_path.exists():
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            for item in payload.get("results", {}).get("utterances", []):
                utterances.append({**item, "timestamp": format_timestamp(item["start_at"])})
            speaker_ids = sorted({item["spk"] for item in utterances})

        return TEMPLATES.TemplateResponse(
            request,
            "detail.html",
            {
                "job": job,
                "utterances": utterances,
                "speaker_ids": speaker_ids,
                "speaker_map": json.loads(job["speaker_map"] or "{}"),
            },
        )

    @app.get("/jobs/{job_id}/status")
    async def status(job_id: str):
        job = get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return JSONResponse({"stage": job["stage"], "error": job["last_error"]})

    @app.post("/jobs/{job_id}/speakers")
    async def rename_speakers(request: Request, job_id: str):
        job = get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        if job["stage"] not in ("fetched", "done"):
            raise HTTPException(status_code=400, detail="아직 전사 결과가 없어 화자 이름을 저장할 수 없습니다.")

        form = await request.form()
        speaker_map = {
            key.removeprefix("speaker_"): str(value).strip()
            for key, value in form.items()
            if key.startswith("speaker_") and str(value).strip()
        }
        await asyncio.to_thread(worker.regenerate, job_id, speaker_map=speaker_map)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/retry")
    async def retry(job_id: str):
        job = get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        _schedule(app, job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    return app


def _schedule(app: FastAPI, job_id: str) -> None:
    """백그라운드 태스크를 만들고 GC되지 않도록 강한 참조를 보관한다."""
    task = asyncio.create_task(_run(app, job_id))
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)


async def _run(app: FastAPI, job_id: str) -> None:
    """블로킹 파이프라인을 스레드로 넘겨 웹 UI가 멈추지 않게 한다."""
    async with app.state.semaphore:
        await asyncio.to_thread(app.state.worker.process, job_id)
