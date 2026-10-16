#!/usr/bin/env bash
# Publish the static site directory to the gh-pages branch (force-updated, single commit).
set -euo pipefail
src="site"
test -f "$src/index.html"
tmp="$(mktemp -d)"
cp -r "$src"/. "$tmp"/
touch "$tmp/.nojekyll"
cd "$tmp"
git init -q -b gh-pages
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit -q -m "Publish site $(date -u +%Y-%m-%dT%H:%MZ)"
git push -f "https://x-access-token:${GITHUB_TOKEN:-$(git -C "$OLDPWD" config --get http.https://github.com/.extraheader | sed 's/.*Basic //' | base64 -d | cut -d: -f2)}@github.com/${GITHUB_REPOSITORY}.git" gh-pages:gh-pages
echo "published to gh-pages"
