#!/usr/bin/env bash
set -euo pipefail

REQUESTED_MODE="${1:-}"
PLAN_FILE="${2:-}"
REPORT_DIR="${3:-auto-publisher-reports}"
DOCKERHUB_REPOSITORY="docker.io/woosungchoi/fpm-alpine"
GHCR_REPOSITORY="ghcr.io/woosungchoi/fpm-alpine"
EXPECTED_PUBLISHER_WORKFLOW="${EXPECTED_PUBLISHER_WORKFLOW:-dependency-auto-publish.yml}"

case "$REQUESTED_MODE" in
  automatic|backfill-ghcr|recover) ;;
  *) echo "usage: $0 <automatic|backfill-ghcr|recover> <promotion-plan.json> [report-dir]" >&2; exit 64 ;;
esac
[ -f "$PLAN_FILE" ] || { echo "promotion plan is required" >&2; exit 64; }
for command in docker cosign python3 sha256sum; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 69; }
done
if [ "$REQUESTED_MODE" = automatic ]; then
  command -v crane >/dev/null 2>&1 || { echo "crane is required" >&2; exit 69; }
fi

mkdir -p "$REPORT_DIR" "$REPORT_DIR/post-promotion" "$REPORT_DIR/rollback"
plan_copy="$REPORT_DIR/promotion-plan.json"
if [ "$PLAN_FILE" != "$plan_copy" ]; then
  cp "$PLAN_FILE" "$plan_copy"
fi
plan_sha256="$(sha256sum "$PLAN_FILE" | cut -d' ' -f1)"
[[ "$plan_sha256" =~ ^[0-9a-f]{64}$ ]]
operation="$(python3 - "$PLAN_FILE" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1])).get("operation")
if value not in {"automatic", "backfill-ghcr"}:
    raise SystemExit("invalid plan operation")
print(value)
PY
)"
if [ "$REQUESTED_MODE" != recover ] && [ "$REQUESTED_MODE" != "$operation" ]; then
  echo "requested mode does not match the frozen plan" >&2
  exit 64
fi
./scripts/validate-auto-promotion-plan.py "$PLAN_FILE" --operation "$operation" \
  --emit-tsv > "$REPORT_DIR/validated-plan.tsv"
./scripts/transaction-journal.py assert-owner "$PLAN_FILE"

resolve_digest() {
  ./scripts/resolve-image-digest.sh "$1"
}

journal_state() {
  local minor="$1" registry="$2" payload
  payload="$(./scripts/transaction-journal.py state "$PLAN_FILE" "$minor" "$registry")"
  python3 - "$payload" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
attempted = payload.get("attempted")
target = payload.get("target_digest")
if type(attempted) is not bool or (target is not None and not isinstance(target, str)):
    raise SystemExit("invalid transaction journal state")
print(f"{1 if attempted else 0}\t{target or '-'}")
PY
}

restore_ghcr_only() {
  local minor="$1" previous_ghcr="$2" rollback_ghcr_digest="$3" actual
  local source="${GHCR_REPOSITORY}@${rollback_ghcr_digest}"
  ./scripts/transaction-journal.py recovery-attempt "$PLAN_FILE" "$minor" ghcr
  ./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
  if ! docker buildx imagetools create --tag "${GHCR_REPOSITORY}:${minor}" "$source"; then
    echo "GHCR backfill rollback mutation failed: $minor" >&2
    return 1
  fi
  actual="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")" || return 1
  if [ "$actual" != "$previous_ghcr" ]; then
    echo "GHCR backfill rollback read-back mismatch: $minor" >&2
    return 1
  fi
  ./scripts/transaction-journal.py recovery-complete \
    "$PLAN_FILE" "$minor" ghcr "$actual"
  echo "GHCR backfill alias restored exactly: $minor@$previous_ghcr"
}

restore_dual() {
  local minor="$1" previous_dockerhub="$2" previous_ghcr="$3"
  local rollback_dockerhub_backup="$4" rollback_ghcr_digest="$5"
  local restore_dockerhub="${6:-1}" restore_ghcr="${7:-1}"
  local status=0 actual
  if [ "$restore_dockerhub" = 1 ]; then
    ./scripts/transaction-journal.py recovery-attempt "$PLAN_FILE" "$minor" dockerhub
  fi
  if [ "$restore_ghcr" = 1 ]; then
    ./scripts/transaction-journal.py recovery-attempt "$PLAN_FILE" "$minor" ghcr
  fi
  ./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
  if EXPECTED_PUBLISHER_WORKFLOW="$EXPECTED_PUBLISHER_WORKFLOW" \
    COSIGN_SIGN_DESTINATION=1 \
    TRANSACTION_PLAN_FILE="$PLAN_FILE" \
    RESTORE_DOCKERHUB="$restore_dockerhub" \
    RESTORE_GHCR="$restore_ghcr" \
    DOCKERHUB_ROLLBACK_SOURCE="${DOCKERHUB_REPOSITORY}@${previous_dockerhub}" \
    DOCKERHUB_ROLLBACK_FALLBACK_SOURCE="${GHCR_REPOSITORY}@${rollback_dockerhub_backup}" \
    GHCR_ROLLBACK_SOURCE="${GHCR_REPOSITORY}@${rollback_ghcr_digest}" \
      ./scripts/rollback-moving-aliases.sh \
        "$DOCKERHUB_REPOSITORY" "$previous_dockerhub" \
        "$GHCR_REPOSITORY" "$previous_ghcr" \
        "$minor" "$REPORT_DIR/rollback/$minor"; then
    status=0
  else
    status=$?
  fi
  if [ "$restore_dockerhub" = 1 ]; then
    if actual="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")" && \
       [ "$actual" = "$previous_dockerhub" ]; then
      ./scripts/transaction-journal.py recovery-complete \
        "$PLAN_FILE" "$minor" dockerhub "$actual"
    else
      status=1
    fi
  fi
  if [ "$restore_ghcr" = 1 ]; then
    if actual="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")" && \
       [ "$actual" = "$previous_ghcr" ]; then
      ./scripts/transaction-journal.py recovery-complete \
        "$PLAN_FILE" "$minor" ghcr "$actual"
    else
      status=1
    fi
  fi
  return "$status"
}

preflight_plan() {
  local minor patch source_sha canary_ref target_ghcr target_dockerhub dockerhub_source
  local previous_dockerhub previous_ghcr rollback_dockerhub_ref rollback_dockerhub_backup
  local rollback_ghcr_ref rollback_ghcr_digest current_dockerhub current_ghcr
  local release_date
  release_date="$(date -u +'%Y%m%d')"
  while IFS=$'\t' read -r minor patch source_sha canary_ref target_ghcr target_dockerhub \
      dockerhub_source previous_dockerhub previous_ghcr rollback_dockerhub_ref \
      rollback_dockerhub_backup rollback_ghcr_ref rollback_ghcr_digest; do
    [ "$(resolve_digest "${GHCR_REPOSITORY}@${target_ghcr}")" = "$target_ghcr" ] || {
      echo "canary exact subject changed before promotion: $minor" >&2
      return 1
    }
    [ "$(resolve_digest "$canary_ref")" = "$target_ghcr" ] || {
      echo "canary tag no longer binds its frozen digest: $minor" >&2
      return 1
    }
    [ "$(resolve_digest "$rollback_ghcr_ref")" = "$rollback_ghcr_digest" ] || {
      echo "GHCR rollback pin changed before promotion: $minor" >&2
      return 1
    }
    if [ "$operation" = automatic ]; then
      [ "$(resolve_digest "${DOCKERHUB_REPOSITORY}@${target_dockerhub}")" = "$target_dockerhub" ] || {
        echo "Docker Hub digest-preserved staged subject changed before promotion: $minor" >&2
        return 1
      }
      [ "$(resolve_digest "$rollback_dockerhub_ref")" = "$rollback_dockerhub_backup" ] || {
        echo "Docker Hub rollback backup pin changed before promotion: $minor" >&2
        return 1
      }
    fi
    current_dockerhub="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")"
    current_ghcr="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")"
    [ "$current_dockerhub" = "$previous_dockerhub" ] || {
      echo "Docker Hub baseline changed before promotion: $minor" >&2
      return 1
    }
    [ "$current_ghcr" = "$previous_ghcr" ] || {
      echo "GHCR baseline changed before promotion: $minor" >&2
      return 1
    }
    if [ "$operation" = automatic ]; then
      ./scripts/verify-rollback-image.sh \
        "${DOCKERHUB_REPOSITORY}@${previous_dockerhub}" \
        "${GHCR_REPOSITORY}@${previous_ghcr}" \
        "$minor" "$REPORT_DIR/preflight-baseline/$minor"
      ./scripts/verify-image-parity.py \
        "${DOCKERHUB_REPOSITORY}@${previous_dockerhub}" \
        "${GHCR_REPOSITORY}@${rollback_dockerhub_backup}" \
        --output "$REPORT_DIR/preflight-dockerhub-backup/$minor.json"
    else
      [ "$current_dockerhub" = "$dockerhub_source" ] || {
        echo "backfill source no longer matches the Docker Hub moving alias: $minor" >&2
        return 1
      }
    fi
    ./scripts/promote-image.sh --check-only --policy evidence \
      "$GHCR_REPOSITORY" "$GHCR_REPOSITORY" "$target_ghcr" \
      "$minor" "$patch" "$source_sha" "$release_date"
    if [ "$operation" = automatic ]; then
      ./scripts/promote-image.sh --check-only --policy moving-only \
        "$DOCKERHUB_REPOSITORY" "$GHCR_REPOSITORY" "$target_ghcr" \
        "$minor" "$patch" "$source_sha" "$release_date"
    fi
  done < "$REPORT_DIR/validated-plan.tsv"
  echo "all immutable subjects, semantic baselines, rollback pins, and collision preflights passed"
}

write_recovery_result() {
  local status="$1" reason="${2:-none}"
  python3 - "$REPORT_DIR/recovery-result.json" "$operation" "$plan_sha256" "$status" "$reason" <<'PY'
import json
import os
import sys
from pathlib import Path
path, operation, plan_sha256, status, reason = sys.argv[1:]
if status not in {"restored", "unknown", "failed"}:
    raise SystemExit("invalid recovery status")
payload = json.dumps({
    "schema_version": 3,
    "status": status,
    "reason": reason,
    "operation": operation,
    "plan_sha256": plan_sha256,
}, indent=2, sort_keys=True) + "\n"
destination = Path(path)
temporary = destination.with_name(f".{destination.name}.tmp")
with temporary.open("w") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, destination)
directory_fd = os.open(destination.parent, os.O_RDONLY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
}

recovery_terminal_written=0
emit_recovery_result() {
  local status="$1" reason="$2" journal_reason="$3"
  if ! write_recovery_result "$status" "$reason"; then
    echo "local recovery evidence write failed: $reason" >&2
  fi
  if [ "$status" != restored ]; then
    ./scripts/transaction-journal.py note-failure "$PLAN_FILE" "$journal_reason"
  fi
  recovery_terminal_written=1
}

recover_transaction() {
  local actions="$REPORT_DIR/recovery-actions.tsv"
  local unknown=0
  local minor patch source_sha canary_ref target_ghcr target_dockerhub dockerhub_source
  local previous_dockerhub previous_ghcr rollback_dockerhub_ref rollback_dockerhub_backup
  local rollback_ghcr_ref rollback_ghcr_digest current_dockerhub current_ghcr dockerhub_state ghcr_state
  local restore_dockerhub restore_ghcr state_line attempted journal_target
  if ! : > "$actions"; then
    emit_recovery_result failed classification-evidence-init classification-init
    return 1
  fi
  while IFS=$'\t' read -r minor patch source_sha canary_ref target_ghcr target_dockerhub \
      dockerhub_source previous_dockerhub previous_ghcr rollback_dockerhub_ref \
      rollback_dockerhub_backup rollback_ghcr_ref rollback_ghcr_digest; do
    ./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
    if ! current_dockerhub="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")"; then
      echo "unknown Docker Hub alias state during recovery: $minor (read failed)" >&2
      unknown=1
      continue
    fi
    if ! current_ghcr="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")"; then
      echo "unknown GHCR alias state during recovery: $minor (read failed)" >&2
      unknown=1
      continue
    fi

    if [ "$operation" = automatic ]; then
      if ! state_line="$(journal_state "$minor" dockerhub)"; then
        echo "Docker Hub ownership journal read failed: $minor" >&2
        unknown=1
        continue
      fi
      IFS=$'\t' read -r attempted journal_target <<< "$state_line"
      if [ "$attempted" = 0 ]; then
        if [ "$current_dockerhub" = "$previous_dockerhub" ]; then
          dockerhub_state=prior
        else
          echo "Docker Hub changed without a durable transaction attempt: $minor" >&2
          unknown=1
          continue
        fi
      elif [ "$journal_target" != "$target_dockerhub" ]; then
        echo "Docker Hub journal target mismatch: $minor" >&2
        unknown=1
        continue
      elif [ "$current_dockerhub" = "$previous_dockerhub" ]; then
        dockerhub_state=prior
      elif [ "$current_dockerhub" = "$journal_target" ]; then
        dockerhub_state=target
      else
        echo "unknown Docker Hub exact state during recovery: $minor@$current_dockerhub" >&2
        unknown=1
        continue
      fi
    elif [ "$current_dockerhub" = "$previous_dockerhub" ]; then
      dockerhub_state=prior
    else
      echo "Docker Hub changed during a GHCR-only backfill: $minor@$current_dockerhub" >&2
      unknown=1
      continue
    fi

    if ! state_line="$(journal_state "$minor" ghcr)"; then
      echo "GHCR ownership journal read failed: $minor" >&2
      unknown=1
      continue
    fi
    IFS=$'\t' read -r attempted journal_target <<< "$state_line"
    if [ "$attempted" = 0 ]; then
      if [ "$current_ghcr" = "$previous_ghcr" ]; then
        ghcr_state=prior
      else
        echo "GHCR changed without a durable transaction attempt: $minor" >&2
        unknown=1
        continue
      fi
    elif [ "$journal_target" != "$target_ghcr" ]; then
      echo "GHCR journal target mismatch: $minor" >&2
      unknown=1
      continue
    elif [ "$current_ghcr" = "$previous_ghcr" ]; then
      ghcr_state=prior
    elif [ "$current_ghcr" = "$journal_target" ]; then
      ghcr_state=target
    else
      echo "unknown GHCR exact state during recovery: $minor@$current_ghcr" >&2
      unknown=1
      continue
    fi

    if ! printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$minor" "$previous_dockerhub" "$previous_ghcr" \
      "$rollback_dockerhub_backup" "$rollback_ghcr_digest" \
      "$dockerhub_state" "$ghcr_state" "$current_dockerhub" "$current_ghcr" >> "$actions"; then
      emit_recovery_result failed classification-evidence-append classification-append
      return 1
    fi
  done < "$REPORT_DIR/validated-plan.tsv"

  if [ "$unknown" -ne 0 ]; then
    emit_recovery_result unknown unknown-alias-or-ownership-state classification-unknown
    echo "recovery classified an unknown state; no moving aliases were modified" >&2
    return 1
  fi

  while IFS=$'\t' read -r minor _ _ _ _ _ _ classified_dockerhub classified_ghcr; do
    ./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
    if ! current_dockerhub="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")" || \
       [ "$current_dockerhub" != "$classified_dockerhub" ]; then
      emit_recovery_result unknown dockerhub-post-classification-drift classification-unknown
      echo "Docker Hub alias changed after recovery classification: $minor" >&2
      return 1
    fi
    if ! current_ghcr="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")" || \
       [ "$current_ghcr" != "$classified_ghcr" ]; then
      emit_recovery_result unknown ghcr-post-classification-drift classification-unknown
      echo "GHCR alias changed after recovery classification: $minor" >&2
      return 1
    fi
  done < "$actions"

  local recovery_status=0
  while IFS=$'\t' read -r minor previous_dockerhub previous_ghcr \
      rollback_dockerhub_backup rollback_ghcr_digest dockerhub_state ghcr_state \
      classified_dockerhub classified_ghcr; do
    ./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
    if ! current_dockerhub="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")" || \
       ! current_ghcr="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")" || \
       [ "$current_dockerhub" != "$classified_dockerhub" ] || \
       [ "$current_ghcr" != "$classified_ghcr" ]; then
      echo "registry alias changed at the recovery mutation boundary: $minor" >&2
      recovery_status=1
      break
    fi
    if [ "$operation" = automatic ]; then
      if [ "$dockerhub_state" = target ] || [ "$ghcr_state" = target ]; then
        restore_dockerhub=0
        restore_ghcr=0
        [ "$dockerhub_state" = target ] && restore_dockerhub=1
        [ "$ghcr_state" = target ] && restore_ghcr=1
        restore_dual "$minor" "$previous_dockerhub" "$previous_ghcr" \
          "$rollback_dockerhub_backup" "$rollback_ghcr_digest" \
          "$restore_dockerhub" "$restore_ghcr" || recovery_status=1
      fi
    elif [ "$ghcr_state" = target ]; then
      restore_ghcr_only "$minor" "$previous_ghcr" "$rollback_ghcr_digest" || recovery_status=1
    fi
    [ "$recovery_status" -eq 0 ] || break
  done < "$actions"

  if [ "$recovery_status" -ne 0 ]; then
    emit_recovery_result failed restore-failed restore-failed
    return 1
  fi

  while IFS=$'\t' read -r minor previous_dockerhub previous_ghcr \
      _rollback_dockerhub_backup _rollback_ghcr_digest _dockerhub_state _ghcr_state \
      _classified_dockerhub _classified_ghcr; do
    if ! current_dockerhub="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")"; then
      echo "final recovery Docker Hub read-back failed for $minor" >&2
      recovery_status=1
      continue
    fi
    if ! current_ghcr="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")"; then
      echo "final recovery GHCR read-back failed for $minor" >&2
      recovery_status=1
      continue
    fi
    if [ "$current_dockerhub" != "$previous_dockerhub" ] || \
       [ "$current_ghcr" != "$previous_ghcr" ]; then
      echo "final recovery baseline read-back mismatch for $minor" >&2
      recovery_status=1
    fi
  done < "$actions"

  if [ "$recovery_status" -ne 0 ]; then
    emit_recovery_result failed final-readback-failed final-readback
    return 1
  fi

  emit_recovery_result restored exact-baseline-readback restore-failed
  echo "recovery restored every durably attempted alias to its exact registry baseline"
}

if [ "$REQUESTED_MODE" = recover ]; then
  # Invoked indirectly by the EXIT trap below.
  # shellcheck disable=SC2329
  on_recovery_exit() {
    local status=$? reason=unexpected-exit journal_reason=unexpected-exit
    trap - EXIT
    trap '' INT TERM
    if [ "$status" -eq 130 ]; then
      reason=signal-int
      journal_reason=signal-int
    elif [ "$status" -eq 143 ]; then
      reason=signal-term
      journal_reason=signal-term
    fi
    if [ "$recovery_terminal_written" -eq 0 ]; then
      emit_recovery_result failed "$reason" "$journal_reason" || true
    fi
    exit "$status"
  }
  trap on_recovery_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  recover_transaction
  trap - EXIT INT TERM
  exit 0
fi

preflight_plan
attempted="$REPORT_DIR/attempted-promotions.tsv"
completed="$REPORT_DIR/completed-promotions.tsv"
: > "$attempted"
: > "$completed"
mutation_started=0
anonymous_docker_config=""

rollback_all() {
  local classification="$REPORT_DIR/rollback-classification.tsv"
  local unknown=0 rollback_status=0
  local minor patch source_sha _canary_ref target_ghcr _target_dockerhub _dockerhub_source
  local previous_dockerhub previous_ghcr _rollback_dockerhub_ref rollback_dockerhub_backup
  local _rollback_ghcr_ref rollback_ghcr_digest
  local current_dockerhub current_ghcr dockerhub_state ghcr_state
  local restore_dockerhub restore_ghcr state_line attempted_registry journal_target
  if ! : > "$classification"; then
    echo "rollback classification evidence could not be initialized" >&2
    return 1
  fi

  while IFS=$'\t' read -r minor patch source_sha _canary_ref target_ghcr target_dockerhub \
      _dockerhub_source previous_dockerhub previous_ghcr _rollback_dockerhub_ref \
      rollback_dockerhub_backup _rollback_ghcr_ref rollback_ghcr_digest; do
    ./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
    if ! current_dockerhub="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")"; then
      echo "unknown Docker Hub alias state before rollback: $minor (read failed)" >&2
      unknown=1
      continue
    fi
    if ! current_ghcr="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")"; then
      echo "unknown GHCR alias state before rollback: $minor (read failed)" >&2
      unknown=1
      continue
    fi

    if [ "$operation" = automatic ]; then
      if ! state_line="$(journal_state "$minor" dockerhub)"; then
        unknown=1
        continue
      fi
      IFS=$'\t' read -r attempted_registry journal_target <<< "$state_line"
      if [ "$attempted_registry" = 0 ]; then
        if [ "$current_dockerhub" = "$previous_dockerhub" ]; then
          dockerhub_state=prior
        else
          unknown=1
          continue
        fi
      elif [ "$journal_target" = "$target_dockerhub" ] && \
           [ "$current_dockerhub" = "$target_dockerhub" ]; then
        dockerhub_state=target
      elif [ "$current_dockerhub" = "$previous_dockerhub" ]; then
        dockerhub_state=prior
      else
        unknown=1
        continue
      fi
    elif [ "$current_dockerhub" = "$previous_dockerhub" ]; then
      dockerhub_state=prior
    else
      unknown=1
      continue
    fi

    if ! state_line="$(journal_state "$minor" ghcr)"; then
      unknown=1
      continue
    fi
    IFS=$'\t' read -r attempted_registry journal_target <<< "$state_line"
    if [ "$attempted_registry" = 0 ]; then
      if [ "$current_ghcr" = "$previous_ghcr" ]; then
        ghcr_state=prior
      else
        unknown=1
        continue
      fi
    elif [ "$journal_target" = "$target_ghcr" ] && [ "$current_ghcr" = "$target_ghcr" ]; then
      ghcr_state=target
    elif [ "$current_ghcr" = "$previous_ghcr" ]; then
      ghcr_state=prior
    else
      unknown=1
      continue
    fi

    if ! printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$minor" "$previous_dockerhub" "$previous_ghcr" \
        "$rollback_dockerhub_backup" "$rollback_ghcr_digest" \
        "$dockerhub_state" "$ghcr_state" "$current_dockerhub" "$current_ghcr" \
        >> "$classification"; then
      echo "rollback classification evidence write failed: $minor" >&2
      unknown=1
    fi
  done < "$attempted"

  if [ "$unknown" -ne 0 ]; then
    echo "rollback classified an unknown state; no moving aliases were modified" >&2
    return 1
  fi

  while IFS=$'\t' read -r minor _ _ _ _ _ _ classified_dockerhub classified_ghcr; do
    if ! current_dockerhub="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")" || \
       ! current_ghcr="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")" || \
       [ "$current_dockerhub" != "$classified_dockerhub" ] || \
       [ "$current_ghcr" != "$classified_ghcr" ]; then
      echo "registry alias changed after rollback classification: $minor; no moving aliases were modified" >&2
      return 1
    fi
  done < "$classification"

  while IFS=$'\t' read -r minor previous_dockerhub previous_ghcr \
      rollback_dockerhub_backup rollback_ghcr_digest dockerhub_state ghcr_state _ _; do
    if [ "$operation" = automatic ]; then
      if [ "$dockerhub_state" = target ] || [ "$ghcr_state" = target ]; then
        restore_dockerhub=0
        restore_ghcr=0
        [ "$dockerhub_state" = target ] && restore_dockerhub=1
        [ "$ghcr_state" = target ] && restore_ghcr=1
        restore_dual "$minor" "$previous_dockerhub" "$previous_ghcr" \
          "$rollback_dockerhub_backup" "$rollback_ghcr_digest" \
          "$restore_dockerhub" "$restore_ghcr" || rollback_status=1
      fi
    elif [ "$ghcr_state" = target ]; then
      restore_ghcr_only "$minor" "$previous_ghcr" "$rollback_ghcr_digest" || rollback_status=1
    fi
  done < "$classification"
  while IFS=$'\t' read -r minor previous_dockerhub previous_ghcr _ _ _ _ _ _; do
    if ! current_dockerhub="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")" || \
       ! current_ghcr="$(resolve_digest "${GHCR_REPOSITORY}:${minor}")"; then
      echo "final rollback read-back failed for $minor" >&2
      rollback_status=1
      continue
    fi
    if [ "$current_dockerhub" != "$previous_dockerhub" ] || \
       [ "$current_ghcr" != "$previous_ghcr" ]; then
      echo "final rollback baseline read-back mismatch for $minor" >&2
      rollback_status=1
    fi
  done < "$classification"
  if [ "$rollback_status" -ne 0 ]; then
    echo "one or more attempted release units could not be rolled back" >&2
    return 1
  fi
  echo "all known attempted release units rolled back to exact registry baselines"
}

on_exit() {
  local status=$?
  local rollback_status=0
  trap - EXIT
  trap '' INT TERM
  if [ -n "$anonymous_docker_config" ] && \
     ! rm -rf -- "$anonymous_docker_config"; then
    echo "anonymous Docker config cleanup failed" >&2
    status=1
  fi
  if [ "$status" -ne 0 ] && [ "$mutation_started" -eq 1 ]; then
    if [ -f "$REPORT_DIR/transaction-result.json" ]; then
      rm -f "$REPORT_DIR/transaction-result.json"
    fi
    set +e
    rollback_all
    rollback_status=$?
    set -e
    if [ "$rollback_status" -ne 0 ]; then
      status=1
    fi
  fi
  exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

anonymous_docker_config="$(mktemp -d)"
chmod 700 "$anonymous_docker_config"
release_date="$(date -u +'%Y%m%d')"
while IFS=$'\t' read -r minor patch source_sha canary_ref target_ghcr target_dockerhub \
    dockerhub_source previous_dockerhub previous_ghcr rollback_dockerhub_ref \
    rollback_dockerhub_backup rollback_ghcr_ref rollback_ghcr_digest; do
  [ "$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")" = "$previous_dockerhub" ] || {
    echo "Docker Hub baseline changed at mutation boundary: $minor" >&2
    exit 1
  }
  [ "$(resolve_digest "${GHCR_REPOSITORY}:${minor}")" = "$previous_ghcr" ] || {
    echo "GHCR baseline changed at mutation boundary: $minor" >&2
    exit 1
  }
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$minor" "$patch" "$source_sha" "$canary_ref" "$target_ghcr" "$target_dockerhub" \
    "$dockerhub_source" "$previous_dockerhub" "$previous_ghcr" "$rollback_dockerhub_ref" \
    "$rollback_dockerhub_backup" "$rollback_ghcr_ref" "$rollback_ghcr_digest" >> "$attempted"

  ghcr_minor_ref="${GHCR_REPOSITORY}:${minor}"
  ghcr_patch_ref="${GHCR_REPOSITORY}:${patch}-${source_sha:0:12}"
  ghcr_date_ref="${GHCR_REPOSITORY}:${release_date}-${minor}"
  ./scripts/transaction-journal.py attempt "$PLAN_FILE" "$minor" ghcr \
    --subject "$ghcr_minor_ref" \
    --subject "$ghcr_patch_ref" \
    --subject "$ghcr_date_ref"
  mutation_started=1
  ./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
  ./scripts/promote-image.sh --policy evidence \
    "$GHCR_REPOSITORY" "$GHCR_REPOSITORY" "$target_ghcr" \
    "$minor" "$patch" "$source_sha" "$release_date"
  ghcr_actual="$(resolve_digest "$ghcr_minor_ref")"
  [ "$ghcr_actual" = "$target_ghcr" ] || {
    echo "GHCR moving alias mismatch after promotion: $minor" >&2
    exit 1
  }
  ghcr_patch_actual="$(resolve_digest "$ghcr_patch_ref")"
  ghcr_date_actual="$(resolve_digest "$ghcr_date_ref")"
  [ "$ghcr_patch_actual" = "$target_ghcr" ] && \
    [ "$ghcr_date_actual" = "$target_ghcr" ] || {
    echo "GHCR immutable evidence tag batch mismatch after promotion: $minor" >&2
    exit 1
  }
  ./scripts/transaction-journal.py complete \
    "$PLAN_FILE" "$minor" ghcr "$ghcr_actual" \
    --observed-subject "$ghcr_minor_ref=$ghcr_actual" \
    --observed-subject "$ghcr_patch_ref=$ghcr_patch_actual" \
    --observed-subject "$ghcr_date_ref=$ghcr_date_actual"

  if [ "$operation" = automatic ]; then
    ./scripts/transaction-journal.py attempt "$PLAN_FILE" "$minor" dockerhub
    ./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
    ./scripts/promote-dockerhub-exact.sh "$PLAN_FILE" "$minor"
    dockerhub_actual="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")"
    [ "$dockerhub_actual" = "$target_dockerhub" ] || {
      echo "Docker Hub moving alias mismatch after promotion: $minor" >&2
      exit 1
    }
    ./scripts/transaction-journal.py complete \
      "$PLAN_FILE" "$minor" dockerhub "$dockerhub_actual"
  else
    dockerhub_actual="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")"
    [ "$dockerhub_actual" = "$previous_dockerhub" ] || {
      echo "Docker Hub changed during GHCR-only backfill: $minor" >&2
      exit 1
    }
  fi

  verify_dockerhub_signature=0
  if [ "$operation" = automatic ]; then
    ./scripts/transaction-journal.py referrer-attempt \
      "$PLAN_FILE" "$minor"
    cosign sign --yes -a "fpm.operation=$operation" \
      "${DOCKERHUB_REPOSITORY}@${dockerhub_actual}"
    verify_dockerhub_signature=1
  fi
  DOCKER_CONFIG="$anonymous_docker_config" \
  EXPECTED_PUBLISHER_WORKFLOW="$EXPECTED_PUBLISHER_WORKFLOW" \
  EXPECTED_OPERATION="$operation" \
  VERIFY_DOCKERHUB_SIGNATURE="$verify_dockerhub_signature" \
    ./scripts/verify-published-image.sh \
      "${DOCKERHUB_REPOSITORY}@${dockerhub_actual}" \
      "${GHCR_REPOSITORY}@${ghcr_actual}" \
      "$source_sha" "$patch" \
      "$REPORT_DIR/post-promotion/$minor" main
  if [ "$operation" = automatic ]; then
    ./scripts/transaction-journal.py referrer-complete \
      "$PLAN_FILE" "$minor" "$dockerhub_actual"
  fi
  [ "$(DOCKER_CONFIG="$anonymous_docker_config" \
      resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")" = "$dockerhub_actual" ]
  [ "$(DOCKER_CONFIG="$anonymous_docker_config" \
      resolve_digest "${GHCR_REPOSITORY}:${minor}")" = "$ghcr_actual" ]
  printf '%s\t%s\t%s\n' "$minor" "$dockerhub_actual" "$ghcr_actual" >> "$completed"
done < "$REPORT_DIR/validated-plan.tsv"

while IFS=$'\t' read -r minor expected_dockerhub expected_ghcr; do
  observed_dockerhub="$(DOCKER_CONFIG="$anonymous_docker_config" \
    resolve_digest "${DOCKERHUB_REPOSITORY}:${minor}")"
  observed_ghcr="$(DOCKER_CONFIG="$anonymous_docker_config" \
    resolve_digest "${GHCR_REPOSITORY}:${minor}")"
  if [ "$observed_dockerhub" != "$expected_dockerhub" ] || \
     [ "$observed_ghcr" != "$expected_ghcr" ]; then
    echo "transaction-wide final alias read-back mismatch: $minor" >&2
    exit 1
  fi
done < "$completed"

python3 - "$REPORT_DIR/transaction-result.json" "$PLAN_FILE" "$completed" "$plan_sha256" <<'PY'
import json
import os
import re
import sys
from pathlib import Path
path, plan_path, completed_path, plan_sha256 = sys.argv[1:]
plan = json.loads(Path(plan_path).read_text())
actual = {}
for line in Path(completed_path).read_text().splitlines():
    minor, dockerhub_digest, ghcr_digest = line.split("\t")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", dockerhub_digest):
        raise SystemExit("invalid completed Docker Hub digest")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", ghcr_digest):
        raise SystemExit("invalid completed GHCR digest")
    actual[minor] = (dockerhub_digest, ghcr_digest)
units = []
for unit in plan["release_units"]:
    minor = unit["php_minor"]
    dockerhub_digest, ghcr_digest = actual[minor]
    units.append({
        "php_minor": minor,
        "php_patch": unit["php_patch"],
        "source_sha": plan["source_sha"],
        "dockerhub_digest": dockerhub_digest,
        "ghcr_digest": ghcr_digest,
    })
payload = json.dumps({
    "schema_version": 2,
    "status": "verified",
    "operation": plan["operation"],
    "repository": plan["repository"],
    "workflow_path": plan["workflow_path"],
    "workflow_sha": plan["workflow_sha"],
    "run_id": plan["run_id"],
    "run_attempt": plan["run_attempt"],
    "source_sha": plan["source_sha"],
    "plan_sha256": plan_sha256,
    "release_units": units,
}, indent=2, sort_keys=True) + "\n"
destination = Path(path)
temporary = destination.with_name(f".{destination.name}.tmp")
with temporary.open("w") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, destination)
directory_fd = os.open(destination.parent, os.O_RDONLY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY

[ -s "$REPORT_DIR/transaction-result.json" ]
echo "verified auto-publisher transaction completed: operation=$operation"
