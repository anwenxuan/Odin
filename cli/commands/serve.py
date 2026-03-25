"""
FastAPI Server — Odin REST API + WebSocket

提供 HTTP API 接口，支持：
- POST /analyze — 启动分析任务
- GET /analyze/{job_id} — 查询任务状态
- GET /analyze/{job_id}/report — 获取分析报告
- WebSocket /ws/{job_id} — 实时任务进度推送

适合团队协作、多人同时使用。

启动方式：
    uvicorn cli.commands.serve:app --reload --port 8080
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
import logging

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
import uvicorn

logger = logging.getLogger("odin.serve")


# ─────────────────────────────────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    """POST /analyze 请求体。"""
    repo_url: str | None = Field(None, description="GitHub URL")
    repo_path: str | None = Field(None, description="本地路径（与 repo_url 二选一）")
    workflow: str = Field("codebase_research", description="工作流 ID")
    provider: str = Field("openai", description="LLM 提供商")
    model: str = Field("gpt-4o-mini", description="模型名称")
    focus_paths: list[str] | None = Field(None, description="重点分析路径")
    description: str | None = Field(None, description="任务描述")


class AnalyzeResponse(BaseModel):
    """POST /analyze 响应。"""
    job_id: str
    status: str
    message: str


class JobStatus(BaseModel):
    """GET /analyze/{job_id} 响应。"""
    job_id: str
    status: str
    created_at: str
    finished_at: str | None = None
    progress: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


class ConfigRequest(BaseModel):
    """配置 API Key 等。"""
    api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Job 管理
# ─────────────────────────────────────────────────────────────────────────────

class JobStatusEnum(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class AnalysisJob:
    """分析任务记录。"""
    job_id: str
    status: JobStatusEnum = JobStatusEnum.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    request: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    progress: str = "等待执行"


class JobManager:
    """全局 Job 管理器。"""
    def __init__(self):
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = asyncio.Lock()

    async def create_job(self, request: dict[str, Any]) -> AnalysisJob:
        async with self._lock:
            job_id = f"job_{uuid.uuid4().hex[:12]}"
            job = AnalysisJob(job_id=job_id, request=request)
            self._jobs[job_id] = job
            return job

    async def get_job(self, job_id: str) -> AnalysisJob | None:
        return self._jobs.get(job_id)

    async def update_job(self, job_id: str, **kwargs: Any) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                for k, v in kwargs.items():
                    if hasattr(job, k):
                        setattr(job, k, v)

    def list_jobs(self) -> list[AnalysisJob]:
        return list(self._jobs.values())


job_manager = JobManager()


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Odin AI Code Research API",
    description="AI Code Research System — Evidence-backed code analysis framework",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 简单的 API Key 认证
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

DEMO_API_KEY = "odin-demo-key"


async def verify_api_key(api_key: str | None = Depends(API_KEY_HEADER)) -> str:
    """验证 API Key。"""
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    if api_key != DEMO_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API Key",
        )
    return api_key


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    request: AnalyzeRequest,
    _api_key: str = Depends(verify_api_key),
) -> AnalyzeResponse:
    """
    启动一个新的代码分析任务。

    返回 job_id，可通过 GET /analyze/{job_id} 查询进度。
    """
    # 验证输入
    if not request.repo_url and not request.repo_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 repo_url 或 repo_path",
        )

    # 创建 Job
    job = await job_manager.create_job(request.model_dump())

    # 在后台执行分析
    asyncio.create_task(_run_analysis(job.job_id, request))

    return AnalyzeResponse(
        job_id=job.job_id,
        status="pending",
        message=f"分析任务已创建：{job.job_id}，使用 workflow='{request.workflow}'",
    )


@app.get("/analyze/{job_id}", response_model=JobStatus)
async def get_status(job_id: str, _api_key: str = Depends(verify_api_key)) -> JobStatus:
    """查询任务状态和进度。"""
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    return JobStatus(
        job_id=job.job_id,
        status=job.status.value,
        created_at=job.created_at,
        finished_at=job.finished_at,
        progress=job.progress,
        error=job.error,
        result=job.result,
    )


@app.get("/analyze/{job_id}/report")
async def get_report(job_id: str, _api_key: str = Depends(verify_api_key)) -> dict[str, Any]:
    """获取分析报告内容。"""
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    if job.status != JobStatusEnum.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"任务尚未完成（当前状态：{job.status.value}）",
        )

    return job.result or {}


@app.get("/jobs")
async def list_jobs(_api_key: str = Depends(verify_api_key)) -> list[dict[str, Any]]:
    """列出所有分析任务。"""
    return [
        {
            "job_id": j.job_id,
            "status": j.status.value,
            "created_at": j.created_at,
            "finished_at": j.finished_at,
            "description": j.request.get("description", ""),
        }
        for j in job_manager.list_jobs()
    ]


@app.get("/health")
async def health_check() -> dict[str, str]:
    """健康检查端点。"""
    return {"status": "ok", "version": "0.2.0"}


@app.post("/config")
async def configure(req: ConfigRequest, _api_key: str = Depends(verify_api_key)) -> dict[str, str]:
    """配置 API Keys（仅本次服务进程有效）。"""
    if req.openai_api_key:
        import os
        os.environ["OPENAI_API_KEY"] = req.openai_api_key
    if req.anthropic_api_key:
        import os
        os.environ["ANTHROPIC_API_KEY"] = req.anthropic_api_key
    return {"message": "Configuration updated"}


# ─────────────────────────────────────────────────────────────────────────────
# 后台任务执行
# ─────────────────────────────────────────────────────────────────────────────

async def _run_analysis(job_id: str, request: AnalyzeRequest) -> None:
    """在 asyncio 背景中执行分析。"""
    from agent.llm_adapter import create_adapter
    from agent.loop import LoopConfig
    from agent.skill_agent import SkillAgent
    from core.skill_loader import SkillRegistry
    from core.pipeline_executor import PipelineExecutor
    from memory.evidence_store import EvidenceStore
    from memory.memory_store import MemoryStore
    from tools.executor import ToolExecutor
    from pathlib import Path
    import tempfile, shutil

    await job_manager.update_job(job_id, status=JobStatusEnum.RUNNING, progress="初始化中...")

    try:
        # 1. 解析 repo
        repo_path: Path | None = None

        if request.repo_path:
            repo_path = Path(request.repo_path)
            if not repo_path.exists():
                raise ValueError(f"本地路径不存在: {repo_path}")

        elif request.repo_url:
            await job_manager.update_job(job_id, progress="克隆仓库...")
            repo_path = await _clone_repo_async(request.repo_url)

        if repo_path is None:
            raise ValueError("无法解析仓库路径")

        # 2. 初始化组件
        evidence_store = EvidenceStore()
        memory_store = MemoryStore()
        tool_executor = ToolExecutor(repo_path=repo_path, session_id=job_id)
        tool_executor.auto_load_builtin()
        llm = create_adapter(provider=request.provider, default_model=request.model)

        # 3. 加载 Skills 和 Workflows
        skills_dir = Path(__file__).parent.parent.parent / "skills"
        workflows_dir = Path(__file__).parent.parent.parent / "workflows"
        registry = SkillRegistry()
        registry.load_from_directory(skills_dir)

        await job_manager.update_job(job_id, progress="准备执行工作流...")

        # 4. 执行 Pipeline
        inputs = {
            "repo_url": request.repo_url or "",
            "repo_path": str(repo_path),
        }
        if request.focus_paths:
            inputs["focus_paths"] = request.focus_paths

        loop_config = LoopConfig(
            max_iterations=20,
            evidence_required=True,
            require_final_json=True,
            allow_fallback_on_error=True,
        )

        executor = PipelineExecutor(
            skill_registry=registry,
            llm_adapter=llm,
            tool_executor=tool_executor,
            evidence_store=evidence_store,
            memory_store=memory_store,
            loop_config=loop_config,
            max_workers_per_layer=3,
        )

        await job_manager.update_job(job_id, progress=f"执行 workflow '{request.workflow}'...")
        run_result = executor.run_parallel(request.workflow, inputs)

        await job_manager.update_job(
            job_id,
            status=JobStatusEnum.SUCCEEDED if run_result.status.value == "succeeded" else JobStatusEnum.FAILED,
            finished_at=datetime.now(timezone.utc).isoformat(),
            progress="完成",
            result={
                "workflow_id": request.workflow,
                "run_id": run_result.run_id,
                "status": run_result.status.value,
                "evidence_stats": evidence_store.stats(),
                "steps_summary": run_result.to_summary(),
            },
        )

    except Exception as exc:
        logger.exception("[API] Job '%s' 失败", job_id)
        await job_manager.update_job(
            job_id,
            status=JobStatusEnum.FAILED,
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )


async def _clone_repo_async(url: str) -> Path:
    """异步克隆仓库。"""
    import subprocess
    clone_dir = Path(tempfile.mkdtemp(prefix="odin_serve_repo_"))
    cmd = ["git", "clone", "--depth", "1", url, str(clone_dir)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"克隆失败: {stderr.decode()[:200]}")
    return clone_dir


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry
# ─────────────────────────────────────────────────────────────────────────────

def run_server(host: str = "0.0.0.0", port: int = 8080, reload: bool = False) -> None:
    """启动 FastAPI 服务器。"""
    uvicorn.run(
        "cli.commands.serve:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    run_server()
