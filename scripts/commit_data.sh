#!/usr/bin/env bash
# Commit the given paths on the current branch and push with a rebase-retry loop.
# Usage: commit_data.sh "<message>" <path> [<path> ...]
set -euo pipefail
msg="$1"; shift
branch="$(git rev-parse --abbrev-ref HEAD)"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A -- "$@"
if git diff --cached --quiet; then
  echo "nothing to commit for: $*"
  exit 0
fi
git commit -q -m "$msg ($(date -u +%Y-%m-%dT%H:%MZ))"
for i in 1 2 3 4 5 6; do
  if git pull --rebase -X theirs origin "$branch" && git push origin "HEAD:$branch"; then
    echo "pushed"
    exit 0
  fi
  echo "push attempt $i failed, retrying"
  sleep $((i * 10))
done
echo "could not push after retries"
exit 1
