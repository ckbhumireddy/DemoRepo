"""Send a short failure-alert email from a GitHub Actions job.

Used by the workflow's ``if: failure()`` steps. Reads the same SMTP_* and
EMAIL_* environment variables as the notifier. Exits 0 even when the send
fails — an alert must never mask the original failure.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from earnings_notifier.config import Config  # noqa: E402
from earnings_notifier.notifier import EmailNotifier  # noqa: E402


def main() -> int:
    job = os.environ.get("GITHUB_JOB", "unknown job")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if repo and run_id else ""

    subject = f"[ALERT] earnings workflow failed: {job}"
    body = (
        f"The scheduled earnings job '{job}' failed.\n\n"
        f"Run log: {run_url}\n\n"
        "No earnings email was sent for this run. Examine the log, then "
        "re-run the workflow from the Actions tab if necessary."
    )
    html = (
        "<div style='font-family:Arial,Helvetica,sans-serif'>"
        f"<p>The scheduled earnings job <b>{job}</b> failed.</p>"
        + (f"<p><a href='{run_url}'>Open the run log</a></p>" if run_url else "")
        + "<p>No earnings email was sent for this run.</p></div>"
    )

    try:
        config = Config.from_env()
        config.validate()
        EmailNotifier(config).send(subject, body, html)
        print("Alert email sent.")
    except Exception as exc:  # noqa: BLE001 - never mask the original failure
        print(f"Could not send the alert email: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
