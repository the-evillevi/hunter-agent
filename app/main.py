from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import applications, health, jobs

app = FastAPI(title="hunter-agent")

# Static files are plain browser assets: CSS, images, and later small scripts.
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers keep `main.py` focused on wiring. Feature code lives in `app/routes/`.
app.include_router(health.router)
app.include_router(applications.router)
app.include_router(jobs.router)
