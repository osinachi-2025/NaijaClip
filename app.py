from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import database
from database import initialize_database
from core.config import AUTH_RESET_COOKIE_NAME, CORS_ORIGINS, FRONTEND_URL, GOOGLE_CLIENT_ID, GOOGLE_REDIRECT_URI, MEDIA_ROOT, PAYSTACK_PUBLIC_KEY
from routers.auth import get_optional_current_user, router as auth_router
from routers.clip_editor import router as clip_editor_router
from routers.homepage import router as homepage_router
from services.auth_service import is_reset_authorization_valid

app = FastAPI(title="NaijaClip", description="A video clipping and sharing platform", version="1.0.0")
templates = Jinja2Templates(directory="templates")
BASE_DIR = Path(__file__).resolve().parent

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(clip_editor_router)
app.include_router(homepage_router)

NAV_ITEMS = [
    {"label": "Overview", "href": "/dashboard", "icon": "&#9632;"},
    {"label": "Videos", "href": "/dashboard/videos", "icon": "&#9654;"},
    {"label": "Clips", "href": "/dashboard/clips", "icon": "&#10022;"},
    {"label": "Upload", "href": "/dashboard/upload", "icon": "&#8593;"},
    {"label": "Usage", "href": "/dashboard/usage", "icon": "&#9673;"},
    {"label": "Billing", "href": "/dashboard/billing", "icon": "&#8358;"},
    {"label": "Settings", "href": "/dashboard/settings", "icon": "&#9881;"},
]

PLANS = [
    {"name": "Starter", "price": 0, "cta": "Get started", "highlight": False},
    {"name": "Creator", "price": 5000, "cta": "Choose Creator", "highlight": True},
    {"name": "Pro", "price": 15000, "cta": "Go Pro", "highlight": False},
]


def dashboard_context(request: Request) -> dict:
    return {
        "request": request,
        "nav_items": NAV_ITEMS,
        "user": {},
        "FRONTEND_URL": FRONTEND_URL,
        "PAYSTACK_PUBLIC_KEY": PAYSTACK_PUBLIC_KEY or "",
        "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID or "",
        "GOOGLE_REDIRECT_URI": GOOGLE_REDIRECT_URI or "",
    }


@app.on_event("startup")
def create_schema() -> None:
    initialize_database()


@app.get("/", name="home")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "plans": PLANS,
            "FRONTEND_URL": FRONTEND_URL,
            "PAYSTACK_PUBLIC_KEY": PAYSTACK_PUBLIC_KEY or "",
            "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID or "",
            "GOOGLE_REDIRECT_URI": GOOGLE_REDIRECT_URI or "",
        },
    )


@app.get("/dashboard", name="dashboard_overview")
@app.get("/dashboard/", name="dashboard_overview_root")
@app.get("/dashboard/index.html", name="dashboard_index_legacy")
def dashboard_overview(
    request: Request,
    page: str | None = None,
    current_user=Depends(get_optional_current_user),
):
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)
    context = dashboard_context(request)
    context["user"] = {"id": current_user.id, "email": current_user.email}
    context["page"] = page or "dashboard-overview"
    return templates.TemplateResponse(request=request, name="dashboard/index.html", context=context)


@app.get("/dashboard/{section}", name="dashboard_section")
def dashboard_section(
    request: Request,
    section: str,
    current_user=Depends(get_optional_current_user),
):
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)
    allowed_sections = {"videos", "clips", "upload", "usage", "billing", "settings"}
    if section not in allowed_sections:
        if section == "index.html":
            return templates.TemplateResponse(request=request, name="dashboard/index.html", context={**dashboard_context(request), "user": {"id": current_user.id, "email": current_user.email}, "page": "dashboard-overview"})
        raise HTTPException(status_code=404, detail="Dashboard page not found")
    context = dashboard_context(request)
    context["user"] = {"id": current_user.id, "email": current_user.email}
    context["page"] = f"dashboard-{section}"
    return templates.TemplateResponse(request=request, name="dashboard/index.html", context=context)


@app.get("/login", name="login_page")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={
            "request": request,
            "FRONTEND_URL": FRONTEND_URL,
            "PAYSTACK_PUBLIC_KEY": PAYSTACK_PUBLIC_KEY or "",
            "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID or "",
            "GOOGLE_REDIRECT_URI": GOOGLE_REDIRECT_URI or "",
        },
    )


@app.get("/register", name="register_page")
def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/register.html",
        context={
            "request": request,
            "FRONTEND_URL": FRONTEND_URL,
            "PAYSTACK_PUBLIC_KEY": PAYSTACK_PUBLIC_KEY or "",
            "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID or "",
            "GOOGLE_REDIRECT_URI": GOOGLE_REDIRECT_URI or "",
        },
    )


@app.get("/forgot-password", name="forgot_password_page")
def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/forgot_password.html",
        context={"request": request, "reset_page": "forgot"},
    )


@app.get("/verify-reset-code", name="verify_reset_code_page")
def verify_reset_code_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/verify_reset_code.html",
        context={"request": request, "reset_page": "verify"},
    )


@app.get("/reset-password", name="reset_password_page")
def reset_password_page(request: Request):
    authorization = request.cookies.get(AUTH_RESET_COOKIE_NAME)
    if not is_reset_authorization_valid(authorization):
        return RedirectResponse(url="/verify-reset-code?expired=1", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="auth/reset_password.html",
        context={"request": request, "reset_page": "reset"},
    )


from app.main import cancel_video, create_video_job, delete_video, get_job, health, list_clips, list_videos

app.add_api_route("/health", health, methods=["GET"])
app.add_api_route("/api/videos", create_video_job, methods=["POST"], status_code=202)
app.add_api_route("/api/videos/{video_id}/cancel", cancel_video, methods=["POST"])
app.add_api_route("/api/videos/{video_id}", delete_video, methods=["DELETE"], status_code=204)
app.add_api_route("/api/jobs/{job_id}", get_job, methods=["GET"])
app.add_api_route("/api/videos", list_videos, methods=["GET"])
app.add_api_route("/api/clips", list_clips, methods=["GET"])


