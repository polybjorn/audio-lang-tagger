#!/bin/bash
# Rejects AI attribution of every shape from commit messages and PR
# descriptions. CLAUDE.md says not to add these, but an agent has followed a
# conflicting session-level instruction and added them anyway more than once
# (dashboard #17, nixfleet #88, dashboard #20) - each time only caught because
# a person happened to read the diff. This is the backstop for when nobody
# does.
#
# Widened 2026-09-11: the pattern knew only Co-Authored-By and "Generated
# with", so the `Claude-Session:` trailer the harness injects per session
# walked straight through it. Session links and bare claude.ai URLs are
# attribution too.
set -euo pipefail

pattern='([[:alnum:]-]+-[Bb]y:[^\n]*([Cc]laude|[Aa]nthropic)|Generated with \[?Claude Code|[[:alnum:]-]*[Ss]ession:[^\n]*([Cc]laude|[Aa]nthropic)|https?://claude\.ai/)'

usage() {
  echo "usage: $(basename "$0") commits <git-log-range-args...> | body <file>" >&2
  exit 2
}

mode="${1:-}"
[ -n "$mode" ] || usage
shift

case "$mode" in
  commits)
    hits=$(git log "$@" --format='%B' | grep -Eio "$pattern" || true)
    if [ -n "$hits" ]; then
      echo "AI attribution found in commit message(s):"
      git log "$@" --format='  %h %s'
      echo "Remove it - trailers, session links and claude.ai URLs all count."
      echo "See CLAUDE.md: commits stay solely attributed to Bjorn."
      exit 1
    fi
    ;;
  body)
    file="${1:-}"
    [ -n "$file" ] || usage
    if [ -f "$file" ] && grep -Eioq "$pattern" "$file"; then
      echo "AI attribution found in the PR description."
      echo "Remove it - trailers, session links and claude.ai URLs all count."
      echo "See CLAUDE.md: commits stay solely attributed to Bjorn."
      exit 1
    fi
    ;;
  *)
    usage
    ;;
esac

echo "No AI attribution found."
