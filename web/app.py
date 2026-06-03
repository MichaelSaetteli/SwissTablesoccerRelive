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

@api_bp.route("/state/<discipline>")
@login_required
def api_state(discipline: str):
    """Combined snapshot for the front-end's single poll loop.

    Returns ``{pipeline, upload, files, active_tournament}`` so the JS only
    needs one request per tab per cycle.
    """
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    return jsonify({
        "pipeline": services.get_status(config).to_dict(),
        "upload": services.get_upload_status(config).to_dict(),
        "files": services.list_output_files(config),
        "active_tournament": services.get_active_tournament_for(config),
        "processing_estimate": services.get_processing_estimate_for(config),
        "upload_throughput": services.get_upload_throughput_for(config),
        "tiering": services.get_tiering_status(config),
    })


# ---- Tournaments (Dashboard Modul 1+4) ----

@api_bp.route("/tournaments/<discipline>")
@login_required
def api_tournaments(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    return jsonify({
        "tournaments": services.list_tournaments_for(config),
        "active_tournament": services.get_active_tournament_for(config),
    })


@api_bp.route("/tournaments/<discipline>", methods=["POST"])
@login_required
def api_tournament_create(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    payload = request.get_json(silent=True) or {}
    created = services.create_tournament_for(config, payload)
    if created is None:
        return jsonify({"error": "invalid payload"}), 400
    return jsonify(created), 201


@api_bp.route("/tournaments/<discipline>/<int:tid>", methods=["PATCH"])
@login_required
def api_tournament_update(discipline: str, tid: int):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    payload = request.get_json(silent=True) or {}
    updated = services.update_tournament_for(config, tid, payload)
    if updated is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(updated)


@api_bp.route("/tournaments/<discipline>/<int:tid>/activate", methods=["POST"])
@login_required
def api_tournament_activate(discipline: str, tid: int):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    if not services.set_active_tournament_for(config, discipline, tid):
        return jsonify({"error": "tournament not found"}), 404
    return jsonify({"status": "activated", "tournament_id": tid})


# ---- Run history (Dashboard Modul 3) ----

@api_bp.route("/history/<discipline>")
@login_required
def api_history(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    return jsonify(services.get_run_history_for(config))


# ---- Manual pipeline control (M2: Pause/Resume/Restart/Bulk) ----

@api_bp.route("/pipeline/<discipline>/pause", methods=["POST"])
@login_required
def api_pipeline_pause(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    if not services.set_pipeline_paused(config, True):
        return jsonify({"error": "no DB available - cannot pause"}), 500
    return jsonify({"discipline": discipline, "paused": True})


@api_bp.route("/pipeline/<discipline>/resume", methods=["POST"])
@login_required
def api_pipeline_resume(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    if not services.set_pipeline_paused(config, False):
        return jsonify({"error": "no DB available - cannot resume"}), 500
    return jsonify({"discipline": discipline, "paused": False})


@api_bp.route("/pipeline/<discipline>/control")
@login_required
def api_pipeline_control(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    return jsonify({
        "discipline": discipline,
        "paused": services.is_pipeline_paused(config),
    })


@api_bp.route("/tiering/<discipline>", methods=["POST"])
@login_required
def api_tiering_start(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    if config.tiering_staging_root is None:
        return jsonify({"error": "tiering.staging_root nicht konfiguriert"}), 400
    if services.is_discipline_busy(config):
        return jsonify({"error": "Disziplin ist beschaeftigt"}), 409
    services.tier_discipline_async(config)
    return jsonify({"discipline": discipline, "started": True}), 202


@api_bp.route("/tiering/<discipline>/sweep", methods=["POST"])
@login_required
def api_tiering_sweep(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    # Idle-gate the sweep across ALL disciplines (decision #4).
    if not services.is_system_idle(_configs()):
        return jsonify({"error": "System beschaeftigt - Sweep abgelehnt"}), 409
    result = services.run_retention_sweep_for(config)
    return jsonify(result), 200 if result.get("ok") else 400


@api_bp.route("/jobs/<discipline>")
@login_required
def api_jobs_list(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    state_filter = request.args.get("states")
    states = state_filter.split(",") if state_filter else None
    return jsonify({
        "jobs": services.list_jobs_for(config, states=states),
        "paused": services.is_pipeline_paused(config),
    })


@api_bp.route("/jobs/<discipline>/<int:run_id>", methods=["PATCH"])
@login_required
def api_job_update(discipline: str, run_id: int):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    payload = request.get_json(silent=True) or {}
    if not services.update_job_for(config, run_id=run_id, payload=payload):
        return jsonify({"error": "job not found or invalid payload"}), 404
    return jsonify({"status": "updated", "run_id": run_id})


@api_bp.route("/jobs/<discipline>/<int:run_id>/restart", methods=["POST"])
@login_required
def api_job_restart(discipline: str, run_id: int):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    payload = request.get_json(silent=True) or {}
    from_phase = str(payload.get("from_phase", "merge"))
    if from_phase not in ("rename", "merge", "output"):
        return jsonify({
            "error": f"invalid from_phase {from_phase!r} - "
                     f"allowed: rename | merge | output"
        }), 400
    services.restart_job_async(config, run_id=run_id, from_phase=from_phase)
    return jsonify({
        "status": "scheduled", "run_id": run_id, "from_phase": from_phase,
    }), 202


@api_bp.route("/jobs/<discipline>/bulk-restart", methods=["POST"])
@login_required
def api_jobs_bulk_restart(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    payload = request.get_json(silent=True) or {}
    run_ids = payload.get("run_ids") or []
    if not isinstance(run_ids, list) or not all(isinstance(x, int) for x in run_ids):
        return jsonify({"error": "run_ids must be a list of ints"}), 400
    from_phase = str(payload.get("from_phase", "merge"))
    if from_phase not in ("rename", "merge", "output"):
        return jsonify({"error": f"invalid from_phase {from_phase!r}"}), 400
    services.bulk_restart_async(config, run_ids=run_ids, from_phase=from_phase)
    return jsonify({
        "status": "scheduled",
        "count": len(run_ids),
        "from_phase": from_phase,
    }), 202


# ---- Storage watcher (Dashboard Modul 6.2) ----

@api_bp.route("/storage")
@login_required
def api_storage():
    """Snapshot of all volumes the pipeline cares about. No discipline."""
    configs = _configs()
    volume_roots = set()
    for cfg in configs.values():
        for p in cfg.paths.all():
            # Step up to the /volume<N>/<share>/ level if possible.
            try:
                parts = p.resolve().parts
            except OSError:
                continue
            if len(parts) >= 3 and parts[1].startswith("volume"):
                volume_roots.add(Path("/" + parts[1] + "/" + parts[2]))
            elif p.is_dir():
                volume_roots.add(p)
    if not volume_roots:
        snapshot = {"volumes": [], "overall_status": "ok"}
    else:
        snapshot = dict(services.get_storage_snapshot(sorted(volume_roots)))
    snapshot["speedtest"] = services.get_last_speedtest(configs)
    return jsonify(snapshot)


# ---- Archive flow (Auftrag 5 + Dashboard Modul 5/8) ----

@api_bp.route("/archive/<discipline>/plan", methods=["POST"])
@login_required
def api_archive_plan(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    payload = request.get_json(silent=True) or {}
    tournament_id = payload.get("tournament_id")
    archive_root = payload.get("archive_root")
    if not tournament_id or not archive_root:
        return jsonify({"error": "tournament_id + archive_root required"}), 400
    plan = services.build_archive_plan_for(
        config, tournament_id=int(tournament_id),
        archive_root=Path(archive_root),
    )
    if plan is None:
        return jsonify({"error": "could not build plan"}), 400
    return jsonify(plan)


@api_bp.route("/archive/<discipline>/execute", methods=["POST"])
@login_required
def api_archive_execute(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    payload = request.get_json(silent=True) or {}
    tournament_id = payload.get("tournament_id")
    archive_root = payload.get("archive_root")
    delete_source = bool(payload.get("delete_source", True))
    confirm = payload.get("confirm")
    if not tournament_id or not archive_root:
        return jsonify({"error": "tournament_id + archive_root required"}), 400
    if confirm != "yes":
        return jsonify({"error": "confirmation required (confirm='yes')"}), 400
    services.start_archive_async(
        config, tournament_id=int(tournament_id),
        archive_root=Path(archive_root), delete_source=delete_source,
    )
    return jsonify({"status": "scheduled", "tournament_id": tournament_id}), 202


@api_bp.route("/archives/<discipline>")
@login_required
def api_archives(discipline: str):
    config = _get_config_or_404(discipline)
    if config is None:
        return jsonify({"error": "unknown discipline"}), 404
    return jsonify({"archives": services.list_archives_for(config)})


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
# Upload-staging routes (Issue #15)
#
# Client-tool surface area. Authenticated like every other /api/* endpoint;
# the desktop tool logs in once and re-uses the session cookie.
# ---------------------------------------------------------------------------

def _eingang_for(discipline: str) -> Optional[Path]:
    config = _get_config_or_404(discipline)
    return None if config is None else config.paths.eingang


def _eingang_roots() -> Dict[str, Path]:
    return {
        d: cfg.paths.eingang for d, cfg in _configs().items()
    }


def _conn_for_uploads():
    """Use whichever discipline config we have to find the runs.db.

    Both configs share the same DB; we pick the first one available so
    upload endpoints stay agnostic of which discipline the client is
    targeting (the discipline is in the request payload, not the URL).
    """
    from db import open_db
    for cfg in _configs().values():
        if cfg.source_path is not None:
            return open_db(cfg.source_path.parent / "runs.db")
    return None


@api_bp.route("/upload/active-tournament", methods=["GET"])
@login_required
def api_upload_active_tournament():
    """Client tool fetches this once at startup to know what's running.

    Returns the active tournament per discipline plus the expected card
    counts. With these numbers the operator UI can show the
    completeness check (\"23/30 verified\") before any upload starts.
    """
    conn = _conn_for_uploads()
    if conn is None:
        return jsonify({"error": "no discipline configured"}), 503
    from db.tournaments import get_active_tournament
    out: Dict[str, object] = {"disciplines": {}}
    for discipline in _configs().keys():
        t = get_active_tournament(conn, discipline)
        if t is None:
            out["disciplines"][discipline] = None
            continue
        d = t.to_dict()
        # Expected card counts live as new tournament columns; the row
        # returned from to_dict() does not surface them yet, so we read
        # the raw row here for backwards compatibility.
        row = conn.execute(
            "SELECT expected_cards_doppel, expected_cards_einzel "
            "FROM tournaments WHERE id = ?", (t.id,),
        ).fetchone()
        d["expected_cards_doppel"] = int(row["expected_cards_doppel"] or 0)
        d["expected_cards_einzel"] = int(row["expected_cards_einzel"] or 0)
        out["disciplines"][discipline] = d
    return jsonify(out)


@api_bp.route("/upload/start", methods=["POST"])
@login_required
def api_upload_start():
    """Reserve a staging dir + DB row for a new SD card.

    Payload (JSON):
        {
          "tournament_id": int,
          "discipline": "Doppel"|"Einzel",
          "table_name": "ET01",
          "expected_files": int,
          "expected_bytes": int,
          "auto_release": bool (optional, default false),
          "card_uuid": str (optional, server mints one if absent)
        }
    """
    from upload_staging import service as upload_service
    payload = request.get_json(silent=True) or {}
    discipline = payload.get("discipline", "")
    eingang = _eingang_for(discipline)
    if eingang is None:
        return jsonify({"error": "unknown discipline"}), 404
    conn = _conn_for_uploads()
    if conn is None:
        return jsonify({"error": "no DB"}), 503

    try:
        card = upload_service.start_upload(
            conn,
            eingang_root=eingang,
            tournament_id=int(payload.get("tournament_id", 0)),
            table_name=str(payload.get("table_name", "")),
            discipline=discipline,
            expected_files=int(payload.get("expected_files", 0)),
            expected_bytes=int(payload.get("expected_bytes", 0)),
            auto_release=bool(payload.get("auto_release", False)),
            card_uuid=payload.get("card_uuid"),
        )
    except upload_service.UploadServiceError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(card.to_dict()), 201


@api_bp.route("/upload/<int:card_id>/chunk", methods=["POST"])
@login_required
def api_upload_chunk(card_id: int):
    """Multipart-form upload of one file under the staging dir.

    Form fields:
        relative_name: str  (e.g. "video_001.mp4")
        file:          file (binary)
    """
    from upload_staging import service as upload_service
    relative_name = request.form.get("relative_name", "")
    if "file" not in request.files:
        return jsonify({"error": "no 'file' part in multipart"}), 400
    stream = request.files["file"].stream

    conn = _conn_for_uploads()
    if conn is None:
        return jsonify({"error": "no DB"}), 503

    try:
        card = upload_service.store_chunk(
            conn, card_id, relative_name=relative_name, stream=stream,
        )
    except upload_service.UploadServiceError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(card.to_dict())


@api_bp.route("/upload/<int:card_id>/finish", methods=["POST"])
@login_required
def api_upload_finish(card_id: int):
    """Verify manifest; transition to verified (or released if auto)."""
    from upload_staging import service as upload_service
    conn = _conn_for_uploads()
    if conn is None:
        return jsonify({"error": "no DB"}), 503

    card_pre = None
    from db import uploads as db_uploads
    card_pre = db_uploads.get_upload_card(conn, card_id)
    if card_pre is None:
        return jsonify({"error": "card not found"}), 404

    eingang = _eingang_for(card_pre.discipline)
    if eingang is None:
        return jsonify({"error": "discipline no longer configured"}), 503

    try:
        card = upload_service.finish_upload(
            conn, card_id, eingang_root=eingang,
        )
    except upload_service.UploadServiceError as exc:
        return jsonify({"error": str(exc)}), 400

    code = 200
    if card.state == db_uploads.STATE_FAILED:
        code = 422
    return jsonify(card.to_dict()), code


@api_bp.route("/upload/release", methods=["POST"])
@login_required
def api_upload_release():
    """Release one or many verified cards via atomic rename.

    Payload: {"card_ids": [int, ...]}
    """
    from upload_staging import service as upload_service
    payload = request.get_json(silent=True) or {}
    ids = payload.get("card_ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({"error": "card_ids must be a list of ints"}), 400
    conn = _conn_for_uploads()
    if conn is None:
        return jsonify({"error": "no DB"}), 503

    try:
        results = upload_service.release_many(
            conn, ids, eingang_roots=_eingang_roots(),
        )
    except upload_service.UploadServiceError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"released": [c.to_dict() for _id, c in results]})


@api_bp.route("/upload/<int:card_id>/reopen", methods=["POST"])
@login_required
def api_upload_reopen(card_id: int):
    """Reopen a failed card (failed -> uploading) for an in-place retry."""
    from upload_staging import service as upload_service
    conn = _conn_for_uploads()
    if conn is None:
        return jsonify({"error": "no DB"}), 503
    try:
        card = upload_service.reopen_upload(conn, card_id)
    except upload_service.UploadServiceError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(card.to_dict())


@api_bp.route("/upload/<int:card_id>/cancel", methods=["POST"])
@login_required
def api_upload_cancel(card_id: int):
    from upload_staging import service as upload_service
    conn = _conn_for_uploads()
    if conn is None:
        return jsonify({"error": "no DB"}), 503
    try:
        card = upload_service.cancel_upload(conn, card_id)
    except upload_service.UploadServiceError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(card.to_dict())


@api_bp.route("/upload/status", methods=["GET"])
@login_required
def api_upload_status():
    """Snapshot for the client tool's polling loop."""
    from upload_staging import service as upload_service
    discipline = request.args.get("discipline")
    tournament_id_raw = request.args.get("tournament_id")
    tournament_id = int(tournament_id_raw) if tournament_id_raw else None
    conn = _conn_for_uploads()
    if conn is None:
        return jsonify({"error": "no DB"}), 503
    cards = upload_service.status_snapshot(
        conn, tournament_id=tournament_id, discipline=discipline,
    )
    return jsonify({"cards": cards})


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
    # Large MP4 uploads (one ~3 GB file per multipart request). Werkzeug 3.1
    # caps a multipart body at max_form_memory_size (500 kB) when
    # MAX_CONTENT_LENGTH is unset, which rejects the file with HTTP 413; raise
    # both so the upload chunk endpoint accepts large bodies. (Waitress caps
    # the socket read separately - see _serve.)
    _MAX_UPLOAD = 32 * 1024 ** 3  # 32 GB headroom per request
    app.config["MAX_CONTENT_LENGTH"] = _MAX_UPLOAD
    app.config["MAX_FORM_MEMORY_SIZE"] = _MAX_UPLOAD
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


def _start_tiering_scheduler(configs: Dict[str, PipelineConfig]):
    """Spawn the idle-gated tiering scheduler (daily sweep + auto-stage)."""
    from watcher.tiering_scheduler import TieringScheduler

    def _upload_info(cfg):
        st = services.get_upload_status(cfg)
        return st.state, st.finished_at

    scheduler = TieringScheduler(
        configs,
        idle_fn=services.is_system_idle,
        sweep_fn=services.run_retention_sweep_for,
        stage_fn=services.tier_discipline,
        upload_info_fn=_upload_info,
        speedtest_fn=lambda: services.run_and_store_speedtest(configs),
    )
    scheduler.start()
    print("[tiering] scheduler started (idle-gated daily sweep + auto-stage)",
          file=sys.stderr)
    return scheduler


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
    # One uploaded MP4 == one request body; tournament files run to ~3 GB,
    # past waitress's 1 GB default max_request_body_size (which rejects the
    # chunk with HTTP 413). Allow large bodies and give a slow large-file
    # upload room before the channel idle-timeout fires.
    waitress_serve(
        app, host=host, port=port,
        max_request_body_size=32 * 1024 ** 3,  # 32 GB headroom per file
        channel_timeout=600,                    # 10 min for a big upload
    )


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

    scheduler = None
    if os.environ.get("ENABLE_TIERING_SCHEDULER", "1") != "0":
        scheduler = _start_tiering_scheduler(configs)

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
        if scheduler is not None:
            try:
                scheduler.stop()
            except Exception:  # pragma: no cover - best-effort shutdown
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
