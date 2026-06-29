#!/usr/bin/env bash
# deploy.sh — branch management + deployment helper
#
# COMMANDS:
#
#   Create a new feature branch from a service's dev branch:
#     ./deploy.sh new <branch-name> [chat|voice|whatsapp]
#     ./deploy.sh new feat/rich-results chat
#     ./deploy.sh new feat/voice-filler voice
#
#   Deploy a feature branch to one or more services:
#     ./deploy.sh deploy <branch-name> [chat|voice|whatsapp|both|all]
#     ./deploy.sh deploy feat/rich-results chat
#     ./deploy.sh deploy feat/voice-filler voice
#     ./deploy.sh deploy feat/shared-fix both        (chat + voice)
#     ./deploy.sh deploy feat/shared-fix all         (chat + voice + whatsapp)
#
#   Shorthand — create AND deploy in one go:
#     ./deploy.sh ship <branch-name> [chat|voice|whatsapp|both|all]




# deploy.sh is updated. Here's what's new:

# new command — creates a branch from a specific service's dev branch:


# ./deploy.sh new feat/rich-results chat      # from chat/development
# ./deploy.sh new feat/voice-fix voice        # from voice/development
# deploy command — merges + pushes to one or more services:


# ./deploy.sh deploy feat/rich-results chat
# ./deploy.sh deploy feat/shared-fix both     # chat + voice
# ./deploy.sh deploy feat/shared-fix all      # chat + voice + whatsapp
# Conflict handling — if a merge conflict is detected, it stops cleanly and prints exact commands to resolve:


# ⚠ Merge conflict detected in chat/development!
# ⚠ Fix the conflicts, then run:
# ⚠   git add .
# ⚠   git commit -m 'Merge feat/x into chat/development'
# ⚠   git push origin chat/development
# status command — quick ahead/behind check vs all three dev branches:


# ./deploy.sh status
# Note: the script uses set -e so it stops on the first error. When deploying to both/all, if the first service fails on a conflict, the second is skipped — you resolve the first conflict manually then re-run for the second service.








set -e

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

info()    { echo -e "${CYAN}→ $*${NC}"; }
success() { echo -e "${GREEN}✓ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠ $*${NC}"; }
error()   { echo -e "${RED}✗ $*${NC}"; exit 1; }

# ── helpers ───────────────────────────────────────────────────────────────────
dev_branch() {
  case "$1" in
    chat)      echo "chat/development" ;;
    voice)     echo "voice/development" ;;
    whatsapp)  echo "whatsapp/development" ;;
    *)         error "Unknown service '$1'. Use: chat | voice | whatsapp" ;;
  esac
}

services_for_target() {
  case "$1" in
    chat|voice|whatsapp) echo "$1" ;;
    both)  echo "chat voice" ;;
    all)   echo "chat voice whatsapp" ;;
    *)     error "Unknown target '$1'. Use: chat | voice | whatsapp | both | all" ;;
  esac
}

current_branch() {
  git branch --show-current
}

branch_exists_remote() {
  git ls-remote --exit-code --heads origin "$1" > /dev/null 2>&1
}

branch_exists_local() {
  git show-ref --verify --quiet "refs/heads/$1"
}

# ── cmd: new ─────────────────────────────────────────────────────────────────
cmd_new() {
  local FEAT=$1
  local SERVICE=${2:-chat}   # default: branch from chat/development

  [[ -z "$FEAT" ]] && error "Usage: ./deploy.sh new <branch-name> [chat|voice|whatsapp]"

  local DEV
  DEV=$(dev_branch "$SERVICE")

  # Make sure we have the latest remote state
  info "Fetching latest from origin..."
  git fetch origin "$DEV" --quiet

  if branch_exists_local "$FEAT"; then
    warn "Branch '$FEAT' already exists locally — switching to it."
    git checkout "$FEAT"
  else
    info "Creating '$FEAT' from '$DEV'..."
    git checkout -b "$FEAT" "origin/$DEV"
    success "Branch '$FEAT' created from '$DEV'"
  fi
}

# ── cmd: deploy ───────────────────────────────────────────────────────────────
cmd_deploy() {
  local FEAT=$1
  local TARGET=${2:-chat}

  [[ -z "$FEAT" ]] && error "Usage: ./deploy.sh deploy <branch-name> [chat|voice|whatsapp|both|all]"

  local ORIGIN
  ORIGIN=$(current_branch)

  # Make sure feature branch exists
  branch_exists_local "$FEAT" || error "Branch '$FEAT' not found locally. Create it first with: ./deploy.sh new $FEAT"

  local SERVICES
  SERVICES=$(services_for_target "$TARGET")

  for SERVICE in $SERVICES; do
    local DEV
    DEV=$(dev_branch "$SERVICE")

    echo ""
    info "Deploying '$FEAT' → '$DEV'..."

    # Fetch latest dev branch
    git fetch origin "$DEV" --quiet

    git checkout "$DEV"
    git pull origin "$DEV" --quiet

    # Attempt merge — handle conflicts gracefully
    if git merge "$FEAT" --no-ff -m "Merge $FEAT into $DEV"; then
      git push origin "$DEV"
      success "$DEV pushed — Railway will redeploy $SERVICE"
    else
      echo ""
      warn "Merge conflict detected in $DEV!"
      warn "Fix the conflicts, then run:"
      warn "  git add ."
      warn "  git commit -m 'Merge $FEAT into $DEV'"
      warn "  git push origin $DEV"
      warn "  git checkout $FEAT   (to resume)"
      echo ""
      error "Stopped at '$DEV' conflict. Other services in '$TARGET' were NOT deployed."
    fi
  done

  # Return to original branch
  git checkout "$ORIGIN" --quiet
  echo ""
  success "Done. Back on '$ORIGIN'"
}

# ── cmd: ship (new + deploy) ──────────────────────────────────────────────────
cmd_ship() {
  local FEAT=$1
  local TARGET=${2:-chat}

  [[ -z "$FEAT" ]] && error "Usage: ./deploy.sh ship <branch-name> [chat|voice|whatsapp|both|all]"

  # Derive source service for branch creation (first service in target)
  local FIRST_SERVICE
  FIRST_SERVICE=$(services_for_target "$TARGET" | awk '{print $1}')

  cmd_new "$FEAT" "$FIRST_SERVICE"
  echo ""
  warn "Branch '$FEAT' created. Make your changes, commit them, then run:"
  warn "  ./deploy.sh deploy $FEAT $TARGET"
}

# ── cmd: status ───────────────────────────────────────────────────────────────
cmd_status() {
  echo ""
  info "Branch status vs dev branches:"
  echo ""
  for SERVICE in chat voice whatsapp; do
    local DEV
    DEV=$(dev_branch "$SERVICE")
    local AHEAD
    AHEAD=$(git log "origin/$DEV..HEAD" --oneline 2>/dev/null | wc -l | tr -d ' ')
    local BEHIND
    BEHIND=$(git log "HEAD..origin/$DEV" --oneline 2>/dev/null | wc -l | tr -d ' ')
    printf "  %-25s  ahead: %s  behind: %s\n" "$DEV" "$AHEAD" "$BEHIND"
  done
  echo ""
  info "Current branch: $(current_branch)"
  echo ""
}

# ── main ──────────────────────────────────────────────────────────────────────
CMD=$1
shift || true

case "$CMD" in
  new)    cmd_new "$@" ;;
  deploy) cmd_deploy "$@" ;;
  ship)   cmd_ship "$@" ;;
  status) cmd_status ;;
  *)
    echo ""
    echo -e "${CYAN}deploy.sh — branch + deploy helper${NC}"
    echo ""
    echo "  Create a branch from a service's dev:"
    echo "    ./deploy.sh new feat/rich-results chat"
    echo "    ./deploy.sh new feat/voice-fix voice"
    echo ""
    echo "  Deploy a branch to a service:"
    echo "    ./deploy.sh deploy feat/rich-results chat"
    echo "    ./deploy.sh deploy feat/voice-fix voice"
    echo "    ./deploy.sh deploy feat/shared-fix both       (chat + voice)"
    echo "    ./deploy.sh deploy feat/shared-fix all        (chat + voice + whatsapp)"
    echo ""
    echo "  Check status:"
    echo "    ./deploy.sh status"
    echo ""
    ;;
esac
