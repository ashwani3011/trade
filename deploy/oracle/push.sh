#!/usr/bin/env bash
# Commit state/, reports/ and dashboard data and push, rebasing over other commits.
cd "$(dirname "$0")/../.." || exit 1
.venv/bin/python -m algo.cli dashboard >/dev/null 2>&1 || true
git add state reports dashboard/data.json >/dev/null 2>&1
git diff --cached --quiet && exit 0
git commit -q -m "paper-trade (oracle): $1 $(date '+%F %H:%M')"
for i in 1 2 3 4; do
  timeout 60 git pull -q --rebase origin claude/adoring-planck-88ci7e && timeout 60 git push -q origin HEAD && exit 0
  sleep $((i * 10))
done
echo "push failed" >&2
exit 1
