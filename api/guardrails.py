"""
Cost-safety guardrails for the Silver Bullet API.

Two independent limits gate POST /api/runs:

  1. Per-IP sliding window — at most IP_HOURLY_LIMIT new runs per IP per
     rolling 60-minute window.  State lives in process memory (thread-safe).
     A server restart resets the window, which is acceptable for a
     single-instance deployment.  Override the limit via:
         RATE_LIMIT_PER_HOUR=N  (default 3)

  2. Global daily cap — at most DAILY_RUN_CAP new runs per UTC calendar day
     across all IPs.  State is queried from PostgreSQL (the same jobs table)
     so it survives restarts.  Once the cap is hit every POST /api/runs
     returns 503 and a one-per-day alert is fired.  Override via:
         DAILY_RUN_CAP=N  (default 50)

Alert delivery
--------------
When the daily cap is reached, _fire_alert() is called:
  - Always: prints a prominent message to stderr and appends to
    /tmp/agentbio_alerts.log (visible in Replit's console).
  - Optional SMTP email: set all four env vars to enable:
        ALERT_EMAIL_TO      recipient address
        ALERT_SMTP_HOST     SMTP server hostname
        ALERT_SMTP_PORT     SMTP port (default 587)
        ALERT_SMTP_USER     SMTP login
        ALERT_SMTP_PASS     SMTP password

IMPORTANT — application limits are not a substitute for provider-level caps.
Configure hard spend limits independently at:
  • Anthropic Console → Settings → Billing → Spend limits
  • OpenAI Platform  → Settings → Limits → Monthly budget
  • Boltz            → account dashboard → API usage / billing
These provider caps are the backstop if the application logic ever has a bug.
"""
from __future__ import annotations

import os
import smtplib
import sys
import threading
import time
from collections import defaultdict
from email.message import EmailMessage
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Tuneable limits (overridable via environment variables at startup)
# ---------------------------------------------------------------------------
IP_HOURLY_LIMIT: int = int(os.environ.get("RATE_LIMIT_PER_HOUR", "3"))
DAILY_RUN_CAP: int = int(os.environ.get("DAILY_RUN_CAP", "50"))

_HOUR_SECONDS: float = 3600.0

# ---------------------------------------------------------------------------
# Per-IP sliding-window state
# ---------------------------------------------------------------------------
# Maps ip → list of POSIX timestamps (one per accepted request in the last hour)
_ip_windows: dict[str, list[float]] = defaultdict(list)
_window_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    """
    Extract the real client IP, preferring the X-Forwarded-For / X-Real-IP
    headers set by Replit's reverse proxy.  Falls back to the direct peer
    address if neither header is present.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # X-Forwarded-For may be a comma-separated list; the leftmost is the
        # original client.
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    if request.client:
        return request.client.host
    return "unknown"


def check_ip_rate_limit(request: Request) -> None:
    """
    Raise HTTP 429 if this IP has already made IP_HOURLY_LIMIT POST /api/runs
    requests in the last rolling hour.

    The response includes a Retry-After header (seconds until the oldest
    in-window request ages out) so clients know exactly when to retry.
    """
    ip = _client_ip(request)
    now = time.monotonic()
    cutoff = now - _HOUR_SECONDS

    with _window_lock:
        # Evict timestamps older than one hour.
        window = [t for t in _ip_windows[ip] if t > cutoff]
        _ip_windows[ip] = window

        if len(window) >= IP_HOURLY_LIMIT:
            # Time until the oldest request in the window ages out.
            retry_after = int(_HOUR_SECONDS - (now - window[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded: at most {IP_HOURLY_LIMIT} new cases "
                    f"per hour per IP.  Try again in {retry_after} seconds "
                    f"({retry_after // 60}m {retry_after % 60}s)."
                ),
                headers={"Retry-After": str(retry_after)},
            )

        # Accept and record this request.
        _ip_windows[ip].append(now)


# ---------------------------------------------------------------------------
# Global daily-cap state
# ---------------------------------------------------------------------------
# We fire at most one alert per calendar day to avoid flooding the log/inbox.
_alert_fired_date: str = ""
_alert_lock = threading.Lock()


def check_daily_cap(count_today_fn) -> None:  # noqa: ANN001
    """
    Raise HTTP 503 if today's job count has reached DAILY_RUN_CAP.

    count_today_fn is a zero-argument callable that returns the count of jobs
    created today (injected to avoid a circular import with jobs_db).
    The cap is checked BEFORE the new job is created, so the ceiling is
    inclusive (jobs 1 … DAILY_RUN_CAP succeed; job DAILY_RUN_CAP+1 is blocked).
    """
    today_count = count_today_fn()
    if today_count >= DAILY_RUN_CAP:
        _maybe_fire_alert(today_count)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Daily capacity reached: the system allows at most "
                f"{DAILY_RUN_CAP} new cases per UTC day to control API costs. "
                f"Today's count: {today_count}.  Try again tomorrow."
            ),
            headers={"Retry-After": _seconds_until_midnight()},
        )


def _seconds_until_midnight() -> str:
    """Seconds remaining until the next UTC midnight (string for the header)."""
    import datetime
    now_utc = time.gmtime()
    tomorrow = (
        time.mktime(time.strptime(
            time.strftime("%Y-%m-%d", now_utc), "%Y-%m-%d"))
        + 86400
        - time.timezone
    )
    return str(max(0, int(tomorrow - time.time())))


def _maybe_fire_alert(count: int) -> None:
    """Fire an alert at most once per UTC calendar day."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    with _alert_lock:
        global _alert_fired_date
        if _alert_fired_date == today:
            return
        _alert_fired_date = today

    msg = (
        f"[AgentBio ALERT] {today} UTC — daily run cap reached. "
        f"Jobs today: {count} / {DAILY_RUN_CAP}. "
        f"New case creation is now blocked until midnight UTC."
    )
    _fire_alert(msg)


def _fire_alert(msg: str) -> None:
    """
    Deliver an alert via:
      1. Stderr + /tmp/agentbio_alerts.log  (always)
      2. SMTP email (only if ALERT_EMAIL_TO + ALERT_SMTP_* env vars are set)
    """
    # --- 1. Stderr + log file ------------------------------------------------
    banner = "=" * 72
    print(f"\n{banner}\n  {msg}\n{banner}\n", file=sys.stderr, flush=True)
    try:
        with open("/tmp/agentbio_alerts.log", "a") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass

    # --- 2. Optional SMTP email ----------------------------------------------
    to_addr = os.environ.get("ALERT_EMAIL_TO", "").strip()
    smtp_host = os.environ.get("ALERT_SMTP_HOST", "").strip()
    smtp_user = os.environ.get("ALERT_SMTP_USER", "").strip()
    smtp_pass = os.environ.get("ALERT_SMTP_PASS", "").strip()
    smtp_port = int(os.environ.get("ALERT_SMTP_PORT", "587"))

    if not (to_addr and smtp_host and smtp_user and smtp_pass):
        return  # email not configured — skip silently

    try:
        em = EmailMessage()
        em["Subject"] = "[AgentBio] Daily run cap reached"
        em["From"] = smtp_user
        em["To"] = to_addr
        em.set_content(msg)
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(em)
        print("[guardrails] alert email sent.", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[guardrails] alert email failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public summary (used by GET /api/limits)
# ---------------------------------------------------------------------------
def limits_summary(count_today_fn) -> dict:  # noqa: ANN001
    """Return a plain-English dict describing both limits and current usage."""
    today_count = count_today_fn()
    return {
        "ip_rate_limit": {
            "description": "Maximum new cases per IP address per rolling hour.",
            "limit": IP_HOURLY_LIMIT,
            "window": "60 minutes",
            "env_var_to_override": "RATE_LIMIT_PER_HOUR",
            "response_when_exceeded": "HTTP 429 with Retry-After header",
        },
        "daily_run_cap": {
            "description": "Maximum new cases globally per UTC calendar day.",
            "limit": DAILY_RUN_CAP,
            "used_today": today_count,
            "remaining_today": max(0, DAILY_RUN_CAP - today_count),
            "resets": "00:00 UTC",
            "env_var_to_override": "DAILY_RUN_CAP",
            "response_when_exceeded": "HTTP 503 with Retry-After header",
            "alert_on_cap": (
                "stderr + /tmp/agentbio_alerts.log; "
                "optional SMTP email if ALERT_EMAIL_TO + ALERT_SMTP_* are set"
            ),
        },
        "provider_spend_caps": {
            "description": (
                "Hard budget limits configured at each API provider, independent "
                "of app-level guardrails.  These are the backstop if the "
                "application logic ever has a bug."
            ),
            "anthropic": "Anthropic Console → Settings → Billing → Spend limits",
            "openai": "OpenAI Platform → Settings → Limits → Monthly budget",
            "boltz": "Boltz account dashboard → API usage / billing",
            "status": (
                "Must be configured manually in each provider's dashboard. "
                "This API cannot read or set provider-level caps."
            ),
        },
    }
