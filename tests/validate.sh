#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
validator="${WEBSERVICES_MODULE_CONTRACT_VALIDATOR:-}"
if [ -z "$validator" ]; then
  for candidate in     "$repo_root/../../sso-stack-generator/scripts/modules/module-contract.sh"     "$repo_root/../sso-stack-generator/scripts/modules/module-contract.sh"; do
    if [ -x "$candidate" ]; then
      validator="$candidate"
      break
    fi
  done
fi
[ -n "$validator" ] || { printf '[module-contract] set WEBSERVICES_MODULE_CONTRACT_VALIDATOR or keep sso-stack-generator next to modules workspace\n' >&2; exit 1; }
"$validator" validate "$repo_root"
grep -Fq 'mastodon-rss-state-init:' "$repo_root/stack.runtime.yaml"
grep -Fq 'chgrp 10001 /state && chmod 2770 /state' "$repo_root/stack.runtime.yaml"
grep -Fq 'mastodon-rss-state-init: "completed"' "$repo_root/stack.runtime.yaml"
grep -Fq -- '- "10001"' "$repo_root/stack.runtime.yaml"
python3 -m unittest discover -s "$repo_root/tests" -p 'test_*.py'
