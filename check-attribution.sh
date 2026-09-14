#!/bin/bash
# Rejects AI attribution trailers and "Generated with" lines in commit
# messages and PR descriptions. This project does not carry them. Without a
# gate the only thing that catches one is a person happening to read the
# diff, so this is the backstop for when nobody does.
set -euo pipefail

pattern='(Co-Authored-By:[^\n]*[Cc]laude|Generated with \[?Claude Code)'

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
      echo "Remove the Co-Authored-By/\"Generated with\" lines and amend."
      exit 1
    fi
    ;;
  body)
    file="${1:-}"
    [ -n "$file" ] || usage
    if [ -f "$file" ] && grep -Eioq "$pattern" "$file"; then
      echo "AI attribution found in the PR description."
      echo "Remove the Co-Authored-By/\"Generated with\" lines and amend."
      exit 1
    fi
    ;;
  *)
    usage
    ;;
esac

echo "No AI attribution found."
