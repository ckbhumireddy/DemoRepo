#!/usr/bin/env bash
#
# Interactive setup for the GitHub Actions secrets/variables the earnings
# notifier needs. Prompts for each value (password input is hidden) and sets
# them with the GitHub CLI. Nothing is written to disk or the shell history.
#
# Requirements: gh CLI installed and authenticated (`gh auth login`) with admin
# on the repo.
#
# Usage:
#   ./scripts/setup-secrets.sh                      # uses the current repo
#   ./scripts/setup-secrets.sh owner/repo           # target a specific repo
#
set -euo pipefail

REPO_ARG=()
if [[ "${1:-}" != "" ]]; then
  REPO_ARG=(--repo "$1")
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh CLI not found. Install it from https://cli.github.com/" >&2
  exit 1
fi

echo "Setting GitHub Actions secrets for the S&P 500 earnings notifier."
echo "Press Ctrl-C to abort. Leave a value blank to skip that item."
echo

set_secret() {
  local name="$1" prompt="$2" hidden="${3:-false}" value=""
  if [[ "$hidden" == "true" ]]; then
    read -r -s -p "$prompt: " value; echo
  else
    read -r -p "$prompt: " value
  fi
  if [[ -z "$value" ]]; then
    echo "  (skipped $name)"
    return
  fi
  gh secret set "$name" "${REPO_ARG[@]}" --body "$value"
  echo "  set secret $name"
}

set_variable() {
  local name="$1" prompt="$2" value=""
  read -r -p "$prompt: " value
  if [[ -z "$value" ]]; then
    echo "  (skipped $name)"
    return
  fi
  gh variable set "$name" "${REPO_ARG[@]}" --body "$value"
  echo "  set variable $name"
}

# --- Required secrets ---
set_secret SMTP_HOST     "SMTP host (e.g. smtp.gmail.com)"
set_secret SMTP_PORT     "SMTP port (587 for STARTTLS, 465 for SSL)"
set_secret SMTP_USER     "SMTP username / login"
set_secret SMTP_PASSWORD "SMTP password / app password" true
set_secret EMAIL_FROM    "From address (e.g. you@gmail.com)"
set_secret EMAIL_TO      "To address(es), comma-separated"
set_secret SMTP_USE_TLS  "Use STARTTLS? (true/false, default true)"
set_secret SMTP_USE_SSL  "Use implicit SSL? (true/false, default false)"

echo
echo "Optional tuning variables (press Enter to skip and use defaults):"
set_variable LEAD_DAYS   "Days ahead to notify (default 7)"
set_variable WINDOW_DAYS "Tolerance +/- days (default 0)"

echo
echo "Done. Verify with:  gh secret list ${REPO_ARG[*]}"
echo "Test it:            Actions tab -> 'S&P 500 earnings notifications' -> Run workflow (dry run)"
