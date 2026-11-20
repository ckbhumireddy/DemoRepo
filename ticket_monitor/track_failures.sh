#!/usr/bin/env bash
# Count consecutive request failures in a GitHub issue; email once when the
# count reaches FAIL_THRESHOLD; close the issue when a check succeeds again.
# Env: IS_ERROR (true/false), FAIL_THRESHOLD, RUN_URL, GH_TOKEN, plus the
# SMTP_*/EMAIL_* settings used by send_email.py.
set -euo pipefail

TITLE="Ticket monitor failing: recreation.gov requests are erroring"
THRESHOLD="${FAIL_THRESHOLD:-6}"

num=$(gh issue list --state open --search "\"$TITLE\" in:title" \
        --json number --jq '.[0].number // empty')

if [ "${IS_ERROR:-false}" != "true" ]; then
  if [ -n "$num" ]; then
    gh issue close "$num" --comment "Recovered: the latest check succeeded. ($RUN_URL)"
  fi
  exit 0
fi

if [ -z "$num" ]; then
  count=1
  url=$(gh issue create --title "$TITLE" \
        --body "consecutive_failures: $count"$'\n'"Latest failing run: $RUN_URL")
  num="${url##*/}"
else
  prev=$(gh issue view "$num" --json body --jq .body \
          | grep -oE 'consecutive_failures: [0-9]+' | grep -oE '[0-9]+' | head -1 || true)
  count=$(( ${prev:-0} + 1 ))
  gh issue edit "$num" \
    --body "consecutive_failures: $count"$'\n'"Latest failing run: $RUN_URL"
fi
echo "Consecutive request failures: $count (threshold $THRESHOLD)"

# Email exactly once, when the threshold is first reached.
if [ "$count" -eq "$THRESHOLD" ]; then
  SUMMARY="The ticket monitor could not read recreation.gov for $count checks in a row, so it is NOT watching for tickets right now. recreation.gov may be blocking requests or have changed its data format." \
  SUBJECT="[MONITOR DOWN] Ticket monitor failing ($count checks in a row)" \
  LINK="$RUN_URL" LINK_TEXT="Open the latest failing run" \
  python3 ticket_monitor/send_email.py
fi
