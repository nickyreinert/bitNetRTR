#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO_URL="${BITNETRTR_REPO_URL:-https://github.com/nickyreinert/bitNetRTR.git}"
DEFAULT_BRANCH="${BITNETRTR_BRANCH:-main}"
DEFAULT_INSTALL_DIR="${BITNETRTR_INSTALL_DIR:-${HOME}/.local/share/bitNetRTR}"

REPO_URL="${DEFAULT_REPO_URL}"
REPO_BRANCH="${DEFAULT_BRANCH}"
INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
INSTALL_DIR_FROM_ARG=0
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
  --skip-install-deps  Deprecated (installer always skips host dependency installation).
  -h, --help           Show this help.

Example:
  curl -fsSL https://raw.githubusercontent.com/nickyreinert/bitNetRTR/main/install.sh | bash
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

ensure_git() {
  if ! need_cmd git; then
    err "git is required but not installed. Install git and retry."
    exit 1
  fi
}

ensure_docker_compose() {
  if ! need_cmd docker; then
    err "docker is required but not installed. Install Docker and retry."
    exit 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    err "docker compose plugin is required but not available. Install it and retry."
    exit 1
  fi
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
        INSTALL_DIR_FROM_ARG=1
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

choose_install_dir() {
  if [[ ${INSTALL_DIR_FROM_ARG} -eq 1 ]]; then
    return
  fi

  local prompt_fd=""
  if [[ -r /dev/tty ]]; then
    prompt_fd="/dev/tty"
  fi

  if [[ -z "${prompt_fd}" ]]; then
    info "No interactive terminal detected. Using default install dir: ${INSTALL_DIR}"
    return
  fi

  printf '\nChoose install location:\n' > "${prompt_fd}"
  printf '  1) %s\n' "${DEFAULT_INSTALL_DIR}" > "${prompt_fd}"
  printf '  2) Current folder (%s)\n' "$(pwd)" > "${prompt_fd}"

  local choice=""
  while true; do
    printf 'Select [1/2] (default: 1): ' > "${prompt_fd}"
    read -r choice < "${prompt_fd}" || true
    choice="${choice:-1}"
    case "${choice}" in
      1)
        INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
        break
        ;;
      2)
        INSTALL_DIR="$(pwd)"
        break
        ;;
      *)
        printf 'Invalid option. Please choose 1 or 2.\n' > "${prompt_fd}"
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
  local forwarded_flags=(--yes --branch "${REPO_BRANCH}" --skip-install-deps)

  info "Running bitNetRTR setup in non-interactive Docker-only mode."
  exec "${INSTALL_DIR}/bitNetRTR.sh" "${forwarded_flags[@]}"
}

main() {
  parse_args "$@"
  choose_install_dir
  ensure_git
  ensure_docker_compose
  clone_or_update_repo
  run_project_setup
}

main "$@"