#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO_URL="${BITNETRTR_REPO_URL:-https://github.com/nickyreinert/bitNetRTR.git}"
DEFAULT_BRANCH="${BITNETRTR_BRANCH:-main}"
DEFAULT_INSTALL_DIR="${BITNETRTR_INSTALL_DIR:-${HOME}/.local/share/bitNetRTR}"

REPO_URL="${DEFAULT_REPO_URL}"
REPO_BRANCH="${DEFAULT_BRANCH}"
INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
SKIP_DEPS=0

info() {
  printf '\033[1;34m[INFO] %s\033[0m\n' "$*"
}

err() {
  printf '\033[1;31m[ERROR] %s\033[0m\n' "$*" >&2
}

usage() {
  cat <<'EOF'
bitNetRTR bootstrap installer

Usage:
  ./install.sh [options]

Options:
  --repo <url>         Repository URL to clone.
  --branch <name>      Branch to install (default: main).
  --install-dir <dir>  Install destination (default: ~/.local/share/bitNetRTR).
  --skip-install-deps  Skip dependency installation inside bitNetRTR.sh.
  -h, --help           Show this help.

Example:
  curl -fsSL https://raw.githubusercontent.com/nickyreinert/bitNetRTR/main/install.sh | bash
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

run_sudo() {
  if need_cmd sudo; then
    sudo "$@"
  else
    "$@"
  fi
}

ensure_git() {
  if need_cmd git; then
    return
  fi

  if [[ -f /etc/debian_version ]] && need_cmd apt-get; then
    info "git not found. Installing git via apt-get."
    run_sudo apt-get update
    run_sudo apt-get install -y git ca-certificates
    return
  fi

  err "git is required but not installed. Install git and retry."
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo)
        REPO_URL="${2:-}"
        shift 2
        ;;
      --branch)
        REPO_BRANCH="${2:-}"
        shift 2
        ;;
      --install-dir)
        INSTALL_DIR="${2:-}"
        shift 2
        ;;
      --skip-install-deps)
        SKIP_DEPS=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        err "Unknown option: $1"
        usage
        exit 1
        ;;
    esac
  done
}

clone_or_update_repo() {
  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    info "Repository already exists at ${INSTALL_DIR}. Updating."
    git -C "${INSTALL_DIR}" fetch --all --prune
    git -C "${INSTALL_DIR}" checkout "${REPO_BRANCH}"
    git -C "${INSTALL_DIR}" pull --ff-only
    return
  fi

  info "Cloning ${REPO_URL} into ${INSTALL_DIR}."
  mkdir -p "$(dirname "${INSTALL_DIR}")"
  git clone --branch "${REPO_BRANCH}" --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
}

run_project_setup() {
  local forwarded_flags=(--yes --branch "${REPO_BRANCH}")
  if [[ ${SKIP_DEPS} -eq 1 ]]; then
    forwarded_flags+=(--skip-install-deps)
  fi

  info "Running bitNetRTR setup in non-interactive mode."
  exec "${INSTALL_DIR}/bitNetRTR.sh" "${forwarded_flags[@]}"
}

main() {
  parse_args "$@"
  ensure_git
  clone_or_update_repo
  run_project_setup
}

main "$@"