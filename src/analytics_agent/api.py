from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from analytics_agent.factory import retail_agent
from analytics_agent.models import Analysis, Question
from analytics_agent.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with retail_agent(settings or Settings()) as agent:
            app.state.agent = agent
            yield

    app = FastAPI(title="Enterprise Agentic Analytics", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    def health(request: Request):
        agent = request.app.state.agent
        return {"status": "ok", "mode": agent.settings.mode, "database": agent.settings.backend}

    @app.post("/v1/analyze", response_model=Analysis)
    async def analyze(body: Question, request: Request):
        agent = request.app.state.agent
        return await run_in_threadpool(agent.ask, body.question)

    return app


app = create_app()
