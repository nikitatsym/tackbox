#!/usr/bin/env bash
# Smoke tackbox's Android wheels in Termux, an Android userland: CI runs it in
# the pinned termux-docker image on an arm64 runner, and it runs unchanged in
# Termux on a device.
#
#   termux_smoke.sh provision          install the toolchains the fixture needs
#   termux_smoke.sh wheels <dist-dir>  the built wheels: thin alone into a fresh
#                                      venv, both payloads via the store overrides
#   termux_smoke.sh uvx <version>      tackbox==<version> from PyPI via uvx,
#                                      waiting out index convergence
#
# Both smokes run on the shared fixture: hook-protocol must allow, doctor must
# pass, and lint must exit 1 with a finding from every engine the fixture
# feeds - on Android each engine is a separately built binary or the system
# node, so one silent engine must not hide behind another's finding.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)

provision() {
  apt-get update -q
  apt-get install -y -q -o Dpkg::Options::=--force-confnew \
    python uv git golang openjdk-17 nodejs-lts
}

materialize() {
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  fixture=$work/fixture-repo
  python3 "$repo/scripts/materialize_fixture.py" "$fixture"
}

hook_protocol() {
  local response
  response=$(cd "$fixture" && printf '%s\n' \
    "{\"protocol\":1,\"phase\":\"post\",\"cwd\":\"$fixture\",\"tool\":\"bash\",\"targets\":[],\"unknown\":null,\"targetless\":\"opaque\",\"succeeded\":true}" \
    | "${tackbox[@]}" hook-protocol)
  printf '%s\n' "$response"
  printf '%s\n' "$response" | python3 -c 'import json,sys; e={"protocol":1,"decision":"allow","reason":""}; r=json.load(sys.stdin); sys.exit(0 if r==e else f"unexpected hook-protocol response: {r!r}")'
}

doctor() {
  (cd "$fixture" && "${tackbox[@]}" doctor)
}

lint() {
  local rc=0 out
  out=$(cd "$fixture" && "${tackbox[@]}" lint . --no-cache 2>&1) || rc=$?
  printf '%s\n' "$out"
  echo "lint exit=$rc"
  if [ "$rc" != 1 ]; then
    echo "expected exit 1 on the planted violations, got $rc" >&2
    exit 1
  fi
  # erclint, opengrep, javalint, ESLint, markdownlint
  for rule in ERC001 go-exit-in-recover JV001 no-swallow-catch MD-CHARS; do
    if ! printf '%s\n' "$out" | grep -q -- "$rule"; then
      echo "lint output carries no $rule finding" >&2
      exit 1
    fi
  done
}

wheels() {
  local dist=$1 payload
  materialize
  uv venv -q --python python3 "$work/venv"
  # Thin installs alone: the payloads are store fetches, not pip dependencies.
  uv pip install -q --python "$work/venv/bin/python" "$dist"/tackbox-*.whl
  # The store can't fetch an unpublished version; supply each payload by
  # unpacking its just-built wheel and overriding its store directory.
  for payload in engines opengrep; do
    python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' \
      "$dist"/tackbox_"$payload"-*.whl "$work/$payload-payload"
    chmod -R u+x "$work/$payload-payload/tackbox_$payload/bin"
  done
  export TACKBOX_ENGINES_DIR=$work/engines-payload/tackbox_engines
  export TACKBOX_OPENGREP_DIR=$work/opengrep-payload/tackbox_opengrep
  tackbox=("$work/venv/bin/tackbox")
  hook_protocol
  doctor
  lint
}

uvx_canary() {
  local version=$1 out rc i
  materialize
  # The real command is its own convergence gate (see verify-release.yml):
  # repeat only while the exact version, or its Android wheel, is not yet
  # visible on the index; anything else fails at once.
  for i in $(seq 1 360); do
    rc=0
    out=$(cd "$fixture" && uvx --refresh "tackbox@$version" doctor 2>&1) || rc=$?
    if [ "$rc" = 0 ]; then
      printf '%s\n' "$out"
      echo "doctor green after $(((i - 1) * 10))s"
      break
    fi
    if ! printf '%s' "$out" | grep -q "No solution found" \
       || { ! printf '%s' "$out" | grep -q "no version of tackbox==$version" \
            && ! printf '%s' "$out" | grep -q "tackbox==$version has no wheels with a matching platform tag"; }; then
      printf '%s\n' "$out"
      exit "$rc"
    fi
    if [ "$i" = 360 ]; then
      echo "tackbox@$version not resolvable after 3600s; last resolver error:" >&2
      printf '%s\n' "$out" >&2
      exit 1
    fi
    sleep 10
  done
  # No --refresh: reuse the tool env doctor resolved.
  tackbox=(uvx "tackbox@$version")
  hook_protocol
  lint
}

case "${1:-}" in
  provision) provision ;;
  wheels) wheels "$2" ;;
  uvx) uvx_canary "$2" ;;
  *) echo "usage: $0 provision | wheels <dist-dir> | uvx <version>" >&2; exit 2 ;;
esac
