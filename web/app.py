"""Flask application factory.

Two tabs (Doppel / Einzel) per the briefing s.5. The factory takes a
mapping ``{"Doppel": PipelineConfig, "Einzel": PipelineConfig}`` so the
caller decides which disciplines exist - either or both can be omitted
(a missing discipline is rendered as a disabled tab).
"""

from __future__ import annotations

import io
import os
import secrets
import sys
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

from flask import (
    Blueprint,
    Flask,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from pipeline.config_loader import PipelineConfig, load_config

from . import services
from .auth import (
    SESSION_KEY,
    check_credentials,
    is_logged_in,
    login_required,
)

sys.stdout.reconfigure(encoding="utf-8")


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "changeme"  # used only when WEB_PASSWORD env var is unset


# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------

auth_bp = Blueprint("auth", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")
ui_bp = Blueprint("ui", __name__)
download_bp = Blueprint("download", __name__, url_prefix="/download")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _configs() -> Dict[str, PipelineConfig]:
    return current_app.config["PIPELINE_CONFIGS"]


def _runner() -> services.Runner:
    return current_app.config["PIPELINE_RUNNER"]


def _get_config_or_404(discipline: str) -> Optional[PipelineConfig]:
    configs = _configs()
    return configs.get(discipline)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if check_credentials(username, password):
            session[SESSION_KEY] = username
            next_url = request.args.get("next") or url_for("ui.index")
            return redirect(next_url)
        flash("Login fehlgeschlagen.", "error")
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.pop(SESSION_KEY, None)
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# UI route
# ---------------------------------------------------------------------------

@ui_bp.route("/favicon.ico")
def favicon():
    """Silence the browser's auto-request for /favicon.ico (no asset shipped)."""
    return ("", 204)


@ui_bp.route("/")
@login_required
def index():
    configs = _configs()
    disciplines = []
    for name in ("Doppel", "Einzel"):
        cfg = configs.get(name)
        disciplines.append({
            "name": name,
            "available": cfg is not None,
            "enabled": cfg.enabled if cfg else False,
        })
    return render_template("index.html", disciplines=disciplines)


# ---------------------------------------------------------------------------
# API routes (all login-protected)
# ---------------------------------------------------------------------------

@api_bp.route("/status/<discipline>")
@login_required
def api_status(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    status = services.get_status(config)
    return jsonify(status.to_dict())


@api_bp.route("/files/<discipline>")
@login_required
def api_files(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    return jsonify({"files": services.list_output_files(config)})


@api_bp.route("/run/<discipline>", methods=["POST"])
@login_required
def api_run(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    if not config.enabled:
        return jsonify({"error": "discipline disabled in config"}), 409
    services.start_run_async(config, runner=_runner())
    return jsonify({"status": "scheduled", "discipline": discipline}), 202


@api_bp.route("/filename-config/<discipline>", methods=["GET", "POST"])
@login_required
def api_filename_config(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        updated = services.update_filename_config(config, payload)
        return jsonify(updated)
    return jsonify(services.get_filename_config(config))


@api_bp.route("/upload-status/<discipline>")
@login_required
def api_upload_status(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    status = services.get_upload_status(config)
    return jsonify(status.to_dict())


@api_bp.route("/upload-preview/<discipline>")
@login_required
def api_upload_preview(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    return jsonify(services.get_upload_preview(config))


@api_bp.route("/upload/<discipline>", methods=["POST"])
@login_required
def api_upload(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    if not config.enabled:
        return jsonify({"error": "discipline disabled in config"}), 409

    factory = current_app.config.get("YOUTUBE_SERVICE_FACTORY")
    runner = current_app.config.get("YOUTUBE_UPLOAD_RUNNER")
    kwargs = {}
    if factory is not None:
        kwargs["service_factory"] = factory
    if runner is not None:
        kwargs["upload_runner"] = runner

    services.start_upload_async(config, **kwargs)
    return jsonify({"status": "scheduled", "discipline": discipline}), 202


@api_bp.route("/youtube-config/<discipline>", methods=["GET", "POST"])
@login_required
def api_youtube_config(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        updated = services.update_youtube_config(config, payload)
        return jsonify(updated)
    return jsonify(services.get_youtube_config(config))


# ---------------------------------------------------------------------------
# Download routes
# ---------------------------------------------------------------------------

@download_bp.route("/<discipline>/<path:filename>")
@login_required
def download_one(discipline: str, filename: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return ("unknown discipline", 404)
    resolved = services.resolve_output_file(config, filename)
    if resolved is None:
        return ("not found", 404)
    return send_file(resolved, as_attachment=True, download_name=resolved.name)


@download_bp.route("/<discipline>/all.zip")
@login_required
def download_all_zip(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return ("unknown discipline", 404)
    files = services.list_output_files(config)
    if not files:
        return ("no files", 404)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as zf:
        for entry in files:
            full = config.paths.output / str(entry["name"])
            zf.write(full, arcname=entry["name"])
    buffer.seek(0)
    archive_name = f"{discipline.lower()}_videos.zip"
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=archive_name,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_app(
    configs: Mapping[str, PipelineConfig],
    *,
    secret_key: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    runner: services.Runner = None,  # type: ignore[assignment]
    youtube_service_factory: Optional[services.ServiceFactory] = None,
    youtube_upload_runner: Optional[services.UploadRunner] = None,
) -> Flask:
    """Build a configured Flask app.

    Parameters
    ----------
    configs:
        Mapping ``{discipline: PipelineConfig}``. Missing keys render as
        disabled tabs (briefing s.4: "wenn nur eine Disziplin vorhanden
        ist: die andere bleibt deaktiviert").
    secret_key:
        Flask session signing key. Defaults to env ``WEB_SECRET_KEY`` or
        a fresh random value (sessions then invalidate on restart).
    username/password:
        Defaults to env ``WEB_USERNAME`` / ``WEB_PASSWORD``.
    runner:
        Pipeline runner injected for tests. Defaults to the real
        ``watcher.pipeline_runner.run_pipeline``.
    """
    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"
    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
    )

    app.config["PIPELINE_CONFIGS"] = dict(configs)
    app.config["WEB_USERNAME"] = (
        username or os.environ.get("WEB_USERNAME", DEFAULT_USERNAME)
    )
    app.config["WEB_PASSWORD"] = (
        password or os.environ.get("WEB_PASSWORD", DEFAULT_PASSWORD)
    )
    app.secret_key = (
        secret_key
        or os.environ.get("WEB_SECRET_KEY")
        or secrets.token_hex(32)
    )

    if runner is None:
        from watcher.pipeline_runner import run_pipeline as _real_runner
        runner = _real_runner
    app.config["PIPELINE_RUNNER"] = runner
    app.config["YOUTUBE_SERVICE_FACTORY"] = youtube_service_factory
    app.config["YOUTUBE_UPLOAD_RUNNER"] = youtube_upload_runner

    app.register_blueprint(auth_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(download_bp)

    @app.context_processor
    def inject_globals():
        return {
            "logged_in": is_logged_in(),
            "username": session.get(SESSION_KEY),
        }

    return app


# ---------------------------------------------------------------------------
# CLI launcher
# ---------------------------------------------------------------------------

def _resolve_data_dir() -> Path:
    """Resolve the directory that holds ``config_<discipline>.json``.

    Order of precedence:
      1. ``$VIDEO_PIPELINE_DATA_DIR`` (set in Docker via the compose file)
      2. ``<repo>/config`` for local development checkouts
    """
    env_value = os.environ.get("VIDEO_PIPELINE_DATA_DIR")
    if env_value:
        return Path(env_value)
    return Path(__file__).resolve().parents[1] / "config"


def _load_configs_from(data_dir: Path) -> Dict[str, PipelineConfig]:
    """Load Doppel + Einzel configs from *data_dir* if present."""
    out: Dict[str, PipelineConfig] = {}
    candidates = (
        ("Doppel", data_dir / "config_doppel.json"),
        ("Einzel", data_dir / "config_einzel.json"),
    )
    for name, path in candidates:
        if path.is_file():
            out[name] = load_config(path)
    return out


def _start_watchers(configs: Dict[str, PipelineConfig]) -> List[object]:
    """Spawn one FolderWatcher per enabled discipline.

    Returns the list of started watchers so the caller can ``stop()`` them
    on shutdown.
    """
    from watcher.folder_watcher import FolderWatcher

    watchers: List[FolderWatcher] = []
    for name, cfg in configs.items():
        if not cfg.enabled:
            print(f"[watcher] {name}: disabled in config, skipping",
                  file=sys.stderr)
            continue
        watcher = FolderWatcher(cfg)
        watcher.start()
        watchers.append(watcher)
        print(f"[watcher] {name}: started on {cfg.paths.eingang}",
              file=sys.stderr)
    return watchers


def _serve(app: Flask, host: str, port: int) -> None:
    """Production-grade WSGI server. Falls back to Flask's dev server if
    waitress is not importable (only happens in bare local dev)."""
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        print("[web] waitress not installed - using Flask dev server",
              file=sys.stderr)
        app.run(host=host, port=port)
        return
    print(f"[web] waitress serving on http://{host}:{port}",
          file=sys.stderr)
    waitress_serve(app, host=host, port=port)


def _main(argv: List[str]) -> int:
    data_dir = _resolve_data_dir()
    configs = _load_configs_from(data_dir)
    if not configs:
        print(f"No config files found in {data_dir}", file=sys.stderr)
        print("  expected: config_doppel.json and/or config_einzel.json",
              file=sys.stderr)
        return 1

    watchers: List[object] = []
    if os.environ.get("ENABLE_WATCHER", "1") != "0":
        watchers = _start_watchers(configs)

    app = create_app(configs)
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "5000"))

    try:
        _serve(app, host, port)
    finally:
        for watcher in watchers:
            try:
                watcher.stop()
            except Exception:  # pragma: no cover - best-effort shutdown
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
