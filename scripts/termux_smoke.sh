#!/usr/bin/env bash
# Usage: termux_smoke.sh provision | wheels <dist-dir> | uvx <version>
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)

provision() {
  apt-get update -q
  apt-get install -y -q -o Dpkg::Options::=--force-confnew \
    python uv git golang openjdk-17 nodejs-lts termux-exec
}

materialize() {
  # uvx's relocatable launchers need Termux's normal /bin/sh rewriting preload.
  export LD_PRELOAD=$PREFIX/lib/libtermux-exec-ld-preload.so
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  fixture=$work/fixture-repo
  python3 "$repo/scripts/materialize_fixture.py" "$fixture"
  touch "$fixture/dev.py"
}

hook_protocol() {
  local response
  response=$(cd "$fixture" && printf '%s\n' \
    "{\"protocol\":1,\"phase\":\"post\",\"cwd\":\"$fixture\",\"tool\":\"bash\",\"targets\":[],\"unknown\":null,\"targetless\":\"opaque\",\"succeeded\":true}" \
    | "${tackbox[@]}" hook-protocol)
  printf '%s\n' "$response"
  printf '%s\n' "$response" | python3 -c 'import json,sys; e={"protocol":1,"decision":"allow","reason":""}; r=json.load(sys.stdin); sys.exit(0 if r==e else f"unexpected hook-protocol response: {r!r}")'
  response=$(cd "$fixture" && python3 -c 'import json,pathlib; p=pathlib.Path("pkga/violate.go").resolve(); print(json.dumps({"protocol":1,"phase":"post","cwd":str(p.parent.parent),"tool":"write","targets":[{"path":str(p),"op":"write","expectedPresent":True,"content":p.read_text()}],"unknown":None,"targetless":None,"succeeded":True}))' \
    | "${tackbox[@]}" hook-protocol)
  printf '%s\n' "$response"
  printf '%s\n' "$response" | python3 -c 'import json,sys; r=json.load(sys.stdin); sys.exit(0 if r["decision"]=="block" and "ERC001" in r["reason"] else f"unexpected hook-protocol response: {r!r}")'
}

doctor() {
  (cd "$fixture" && "${tackbox[@]}" doctor)
}

lint() {
  local rc=0 out
  out=$(cd "$fixture" && "${tackbox[@]}" lint . --no-cache --codequality "$work/quality.json" 2>&1) || rc=$?
  printf '%s\n' "$out"
  echo "lint exit=$rc"
  if [ "$rc" != 1 ]; then
    echo "expected exit 1 on the planted violations, got $rc" >&2
    exit 1
  fi
  for rule in ERC001 go-exit-in-recover JV001 no-swallow-catch MD-CHARS; do
    if ! printf '%s\n' "$out" | grep -q -- "$rule"; then
      echo "lint output carries no $rule finding" >&2
      exit 1
    fi
  done
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); e=[i for i in r if i["check_name"]=="tackbox-engine-error"]; sys.exit(f"engine failures: {e!r}" if e else 0)' "$work/quality.json"
}

wheels() {
  local dist=$1 payload
  materialize
  uv venv -q --python python3 "$work/venv"
  # Thin installs alone: the payloads are store fetches, not pip dependencies.
  uv pip install -q --python "$work/venv/bin/python" "$dist"/tackbox-*.whl
  # Unpublished payloads must be supplied directly instead of fetched from PyPI.
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
  # Retry index propagation only; runtime failures must fail the canary at once.
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
