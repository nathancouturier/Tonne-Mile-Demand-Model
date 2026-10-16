#!/usr/bin/env bash
# Best effort: enable GitHub Pages from the gh-pages branch through the REST API and report its state.
# The workflow token may lack the permission to create the Pages site; in that case the script prints
# the exact setting to change and exits successfully so that the build is not marked as failed.
set -uo pipefail
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
api="https://api.github.com/repos/${GITHUB_REPOSITORY}/pages"
auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28")
status=$(curl -sS -o /tmp/pages.json -w "%{http_code}" "${auth[@]}" "$api")
echo "GET pages: HTTP $status"
if [ "$status" = "404" ]; then
  create=$(curl -sS -o /tmp/pages_create.json -w "%{http_code}" -X POST "${auth[@]}" "$api" \
    -d '{"source":{"branch":"gh-pages","path":"/"}}')
  echo "POST pages: HTTP $create"
  cat /tmp/pages_create.json; echo
  status=$(curl -sS -o /tmp/pages.json -w "%{http_code}" "${auth[@]}" "$api")
  echo "GET pages after create: HTTP $status"
fi
if [ "$status" = "200" ]; then
  python3 - <<'PY'
import json
d = json.load(open("/tmp/pages.json"))
print("Pages url:", d.get("html_url"), "| status:", d.get("status"), "| source:", d.get("source"))
PY
else
  echo "Pages is not enabled and the workflow token could not enable it."
  echo "One manual step: repository Settings > Pages > Build and deployment > Source: Deploy from a branch, Branch: gh-pages, folder: / (root)."
fi
exit 0
