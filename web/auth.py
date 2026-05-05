"""Login + password verification for the web interface.

Briefing s.5: "Einfacher Login mit Benutzername + Passwort". The
credentials live in environment variables (never hardcoded, briefing s.12).
The session cookie is signed by Flask's secret key.
"""

from __future__ import annotations

import hmac
from functools import wraps
from typing import Callable

from flask import current_app, redirect, request, session, url_for


SESSION_KEY = "logged_in_user"


def check_credentials(username: str, password: str) -> bool:
    """Constant-time comparison against the configured user/password."""
    expected_user = current_app.config["WEB_USERNAME"]
    expected_pass = current_app.config["WEB_PASSWORD"]
    user_ok = hmac.compare_digest(
        username.encode("utf-8"), expected_user.encode("utf-8")
    )
    pass_ok = hmac.compare_digest(
        password.encode("utf-8"), expected_pass.encode("utf-8")
    )
    return user_ok and pass_ok


def is_logged_in() -> bool:
    return SESSION_KEY in session


def login_required(view: Callable) -> Callable:
    """Redirect to /login if the session has no user."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            if request.path.startswith("/api/") or request.path.startswith("/download/"):
                # API callers prefer 401 over a redirect.
                return ("Login required", 401)
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapper
