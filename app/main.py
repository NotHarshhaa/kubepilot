"""KubePilot entrypoint — run with `python -m app.main`."""

from fastapi import FastAPI

from app.api.routes import router


def create_app() -> FastAPI:
    application = FastAPI(
        title="KubePilot",
        description=(
            "AI-powered Kubernetes troubleshooting and operations agent. "
            "Read tools run automatically; operational actions require approval via "
            "the /approvals endpoints."
        ),
        version="0.1.0",
    )
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
