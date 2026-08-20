#!/bin/sh
set -eu

canonical_repository="harrisonoconnorhover/dander"
upstream_repository="WagnerJ-Dev/dander"
upstream_url="https://github.com/$upstream_repository.git"
disabled_push_url="disabled://WagnerJ-Dev/dander"
repository_root=$(git rev-parse --show-toplevel)
verifier="$repository_root/scripts/verify_repository_target.py"

python3 "$verifier" --repository-root "$repository_root" --remote origin
python3 "$verifier" --repository-root "$repository_root" --remote origin --push

git -C "$repository_root" config remote.pushDefault origin
gh repo set-default "$canonical_repository"

if git -C "$repository_root" remote get-url upstream >/dev/null 2>&1; then
  :
else
  git -C "$repository_root" remote add upstream "$upstream_url"
fi
git -C "$repository_root" config --unset-all remote.upstream.url >/dev/null 2>&1 || true
git -C "$repository_root" config --add remote.upstream.url "$upstream_url"
git -C "$repository_root" config --unset-all remote.upstream.pushurl >/dev/null 2>&1 || true
git -C "$repository_root" config --add remote.upstream.pushurl "$disabled_push_url"
git -C "$repository_root" config core.hooksPath .githooks

python3 "$verifier" --repository-root "$repository_root" --remote origin
python3 "$verifier" --repository-root "$repository_root" --remote origin --push

actual_push_default=$(git -C "$repository_root" config --get remote.pushDefault)
actual_gh_default=$(gh repo set-default --view)
actual_upstream_fetch=$(git -C "$repository_root" remote get-url upstream)
actual_upstream_push=$(git -C "$repository_root" remote get-url --push upstream)
actual_hooks_path=$(git -C "$repository_root" config --get core.hooksPath)

[ "$actual_push_default" = "origin" ]
[ "$actual_gh_default" = "$canonical_repository" ]
[ "$actual_upstream_fetch" = "$upstream_url" ]
[ "$actual_upstream_push" = "$disabled_push_url" ]
[ "$actual_hooks_path" = ".githooks" ]
[ -x "$repository_root/.githooks/pre-push" ]

printf '%s\n' \
  "writable_repository=$canonical_repository" \
  "origin_fetch=$(git -C "$repository_root" remote get-url origin)" \
  "origin_push=$(git -C "$repository_root" remote get-url --push origin)" \
  "push_default=$actual_push_default" \
  "gh_default=$actual_gh_default" \
  "upstream_fetch=$actual_upstream_fetch" \
  "upstream_push=$actual_upstream_push" \
  "hooks_path=$actual_hooks_path"
