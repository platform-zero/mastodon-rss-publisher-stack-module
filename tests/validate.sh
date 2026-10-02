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
grep -Fq 'chmod 0640' "$repo_root/stack.runtime.yaml"
grep -Fq 'File.chown(nil, 10_001, temporary)' "$repo_root/stack.config/mastodon-rss/bootstrap-rss-accounts.sh"
grep -Fq 'File.chmod(0o640, temporary)' "$repo_root/stack.config/mastodon-rss/bootstrap-rss-accounts.sh"
grep -Fq 'mastodon-rss-state-init: "completed"' "$repo_root/stack.runtime.yaml"
grep -Fq -- '- "10001"' "$repo_root/stack.runtime.yaml"
# This assertion intentionally matches a literal template expression.
# shellcheck disable=SC2016
grep -Fq 'DB_PASS: "${POSTGRES_MASTODON_PASSWORD}"' "$repo_root/stack.runtime.yaml"
grep -Fq 'while true; do bash /opt/mastodon/bin/bootstrap-rss-accounts.sh; sleep' "$repo_root/stack.runtime.yaml"
if grep -Fq 'bootstrap-rss-accounts.sh || true' "$repo_root/stack.runtime.yaml"; then
  printf '[mastodon-rss-validate] bootstrap failures must not be suppressed\n' >&2
  exit 1
fi
python3 "$repo_root/tests/check_avatars.py"
python3 -m unittest discover -s "$repo_root/tests" -p 'test_*.py'
