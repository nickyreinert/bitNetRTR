#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="bitNetRTR"
DEFAULT_INSTALL_DIR="${HOME}/.local/share/bitNetRTR"
DEFAULT_REPO_URL="${BITNETRTR_REPO_URL:-}"
DEFAULT_BRANCH="${BITNETRTR_BRANCH:-main}"

REPO_URL="${DEFAULT_REPO_URL}"
REPO_BRANCH="${DEFAULT_BRANCH}"
INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
ASSUME_YES=0
SKIP_DEPS=0

PROJECT_DIR=""

color() {
	local code="$1"
	shift
	printf "\033[%sm%s\033[0m\n" "$code" "$*"
}

info() {
	color "1;34" "[INFO] $*"
}

warn() {
	color "1;33" "[WARN] $*"
}

err() {
	color "1;31" "[ERROR] $*"
}

usage() {
	cat <<'EOF'
bitNetRTR interactive installer/launcher

Usage:
	./bitNetRTR.sh menu
	./bitNetRTR.sh [setup] [options]
	./bitNetRTR.sh app [app.py args...]
	./bitNetRTR.sh native [bitnet.sh args...]

Options:
	--repo <url>         Git repository URL for self-bootstrap.
	--branch <name>      Branch to clone when self-bootstrapping (default: main).
	--install-dir <dir>  Install destination for self-bootstrap.
	--yes                Non-interactive defaults where possible.
	--skip-install-deps  Skip package/dependency installation.
	-h, --help           Show this help.

Examples:
	curl -fsSL <raw-script-url> | bash -s -- --repo https://github.com/you/bitNetRTR.git
	./bitNetRTR.sh
	./bitNetRTR.sh menu
	./bitNetRTR.sh app
	./bitNetRTR.sh native -m models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf -p "hello"
EOF
}

prompt() {
	local message="$1"
	local default="${2:-}"
	local value

	if [[ ${ASSUME_YES} -eq 1 ]]; then
		printf "%s\n" "${default}"
		return 0
	fi

	if [[ -n "${default}" ]]; then
		read -r -p "${message} [${default}]: " value || true
		printf "%s\n" "${value:-${default}}"
	else
		read -r -p "${message}: " value || true
		printf "%s\n" "${value}"
	fi
}

prompt_secret() {
	local message="$1"
	local default="${2:-}"
	local value

	if [[ ${ASSUME_YES} -eq 1 ]]; then
		printf "%s\n" "${default}"
		return 0
	fi

	if [[ -n "${default}" ]]; then
		read -r -s -p "${message} [${default}]: " value || true
		echo
		printf "%s\n" "${value:-${default}}"
	else
		read -r -s -p "${message}: " value || true
		echo
		printf "%s\n" "${value}"
	fi
}

confirm() {
	local message="$1"
	local default="${2:-y}"
	local answer

	if [[ ${ASSUME_YES} -eq 1 ]]; then
		[[ "${default}" == "y" ]]
		return
	fi

	local suffix="[y/N]"
	if [[ "${default}" == "y" ]]; then
		suffix="[Y/n]"
	fi

	read -r -p "${message} ${suffix}: " answer || true
	answer="${answer,,}"

	if [[ -z "${answer}" ]]; then
		[[ "${default}" == "y" ]]
		return
	fi

	[[ "${answer}" == "y" || "${answer}" == "yes" ]]
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

ensure_apt_packages() {
	local packages=("$@")
	local missing=()
	local pkg

	for pkg in "${packages[@]}"; do
		if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
			missing+=("${pkg}")
		fi
	done

	if [[ ${#missing[@]} -eq 0 ]]; then
		return
	fi

	info "Installing missing packages: ${missing[*]}"
	run_sudo apt-get update
	run_sudo apt-get install -y "${missing[@]}"
}

install_nvidia_toolkit_apt() {
	if ! need_cmd nvidia-smi; then
		warn "nvidia-smi not found. GPU mode may fail until NVIDIA drivers are installed."
		return 0
	fi

	if dpkg -s nvidia-container-toolkit >/dev/null 2>&1; then
		return 0
	fi

	if ! confirm "Install NVIDIA Container Toolkit for Docker GPU support?" "y"; then
		warn "Skipping NVIDIA Container Toolkit installation."
		return 0
	fi

	info "Installing nvidia-container-toolkit (APT)"
	ensure_apt_packages curl gnupg

	local distro
	distro=$(. /etc/os-release && echo "${ID}${VERSION_ID}")

	curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
		| run_sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

	curl -fsSL "https://nvidia.github.io/libnvidia-container/${distro}/libnvidia-container.list" \
		| sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
		| run_sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

	run_sudo apt-get update
	run_sudo apt-get install -y nvidia-container-toolkit
	run_sudo nvidia-ctk runtime configure --runtime=docker
	run_sudo systemctl restart docker || true
}

ensure_docker() {
	if need_cmd docker && docker compose version >/dev/null 2>&1; then
		return
	fi

	warn "Docker or Docker Compose plugin not found."

	if [[ -f /etc/debian_version ]]; then
		ensure_apt_packages ca-certificates curl gnupg lsb-release
		ensure_apt_packages docker.io docker-compose-plugin git

		run_sudo systemctl enable docker || true
		run_sudo systemctl start docker || true
	else
		err "Automatic install is only implemented for Debian/Ubuntu right now."
		err "Please install Docker + Compose plugin manually, then re-run ${SCRIPT_NAME}."
		exit 1
	fi
}

ensure_docker_access() {
	if docker info >/dev/null 2>&1; then
		return
	fi

	if id -nG "$USER" | grep -qw docker; then
		warn "Docker daemon still not accessible. You may need to restart the shell session."
		return
	fi

	if confirm "Add user '${USER}' to docker group for non-sudo usage?" "y"; then
		run_sudo usermod -aG docker "$USER"
		warn "Group membership updated. Re-login may be required for non-sudo docker commands."
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
				shift 2
				;;
			--yes)
				ASSUME_YES=1
				shift
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

bootstrap_if_needed() {
	if [[ -f "./docker-compose.yml" && -f "./app.py" ]]; then
		PROJECT_DIR="$(pwd)"
		return
	fi

	info "No BitNetRTR project detected in current directory. Running self-bootstrap."

	if [[ -z "${REPO_URL}" ]]; then
		REPO_URL="$(prompt "Enter BitNetRTR git repository URL" "https://github.com/your-org/bitNetRTR.git")"
	fi

	INSTALL_DIR="$(prompt "Install directory" "${INSTALL_DIR}")"

	if [[ -d "${INSTALL_DIR}/.git" ]]; then
		info "Repository already exists at ${INSTALL_DIR}. Updating..."
		git -C "${INSTALL_DIR}" fetch --all --prune
		git -C "${INSTALL_DIR}" checkout "${REPO_BRANCH}" || true
		git -C "${INSTALL_DIR}" pull --ff-only || true
	else
		mkdir -p "$(dirname "${INSTALL_DIR}")"
		git clone --branch "${REPO_BRANCH}" --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
	fi

	local forwarded_flags=()
	if [[ ${ASSUME_YES} -eq 1 ]]; then
		forwarded_flags+=(--yes)
	fi
	if [[ ${SKIP_DEPS} -eq 1 ]]; then
		forwarded_flags+=(--skip-install-deps)
	fi

	exec "${INSTALL_DIR}/bitNetRTR.sh" --branch "${REPO_BRANCH}" "${forwarded_flags[@]}"
}

install_self_launcher() {
	local bin_dir="${HOME}/.local/bin"
	mkdir -p "${bin_dir}"
	ln -sf "${PROJECT_DIR}/bitNetRTR.sh" "${bin_dir}/bitNetRTR"

	if [[ ":${PATH}:" != *":${bin_dir}:"* ]]; then
		warn "${bin_dir} is not in PATH. Add it to run 'bitNetRTR' globally."
	fi
}

ensure_bitnet_submodule() {
	local project_git_dir="${PROJECT_DIR}/.git"
	local submodule_path="third_party/BitNet"

	if [[ ! -e "${project_git_dir}" ]]; then
		return
	fi

	if [[ ! -f "${PROJECT_DIR}/.gitmodules" ]]; then
		return
	fi

	if ! git -C "${PROJECT_DIR}" config --file .gitmodules --get-regexp path | grep -q " ${submodule_path}$"; then
		return
	fi

	info "Ensuring git submodule ${submodule_path} is initialized and up to date"
	git -C "${PROJECT_DIR}" submodule sync --recursive
	git -C "${PROJECT_DIR}" submodule update --init --recursive -- "${submodule_path}"
}

validate_port() {
	local port="$1"
	[[ "${port}" =~ ^[0-9]+$ ]] || return 1
	((port >= 1 && port <= 65535))
}

write_env_file() {
	local env_path="${PROJECT_DIR}/.env"

	cat >"${env_path}" <<EOF
# Generated by bitNetRTR.sh
DEPLOY_MODE=${DEPLOY_MODE}
COMPUTE_MODE=${COMPUTE_MODE}
API_KEY=${API_KEY}
BITNET_MODEL=${BITNET_MODEL}
BITNET_N_PREDICT=${BITNET_N_PREDICT}
BITNET_THREADS=${BITNET_THREADS}
BITNET_CTX_SIZE=${BITNET_CTX_SIZE}
BITNET_TEMPERATURE=${BITNET_TEMPERATURE}
BITNET_CONVERSATION=${BITNET_CONVERSATION}
BITNET_REPO_DIR=third_party/BitNet
API_BIND_HOST=${API_BIND_HOST}
API_PORT=${API_PORT}
UI_BIND_HOST=${UI_BIND_HOST}
UI_PORT=${UI_PORT}
ALLOWED_ORIGINS=${ALLOWED_ORIGINS}
EOF

	info "Wrote ${env_path}"
}

load_saved_runtime_config() {
	local env_path="${PROJECT_DIR}/.env"
	local runtime_path="${PROJECT_DIR}/docker-compose.runtime.yml"

	if [[ -f "${env_path}" ]]; then
		while IFS= read -r line || [[ -n "${line}" ]]; do
			[[ -z "${line}" ]] && continue
			[[ "${line}" =~ ^[[:space:]]*# ]] && continue
			[[ "${line}" != *"="* ]] && continue

			local key="${line%%=*}"
			local value="${line#*=}"
			key="${key//[[:space:]]/}"

			if [[ "${value}" =~ ^\".*\"$ || "${value}" =~ ^\'.*\'$ ]]; then
				value="${value:1:-1}"
			fi

			case "${key}" in
				DEPLOY_MODE) DEPLOY_MODE="${value}" ;;
				COMPUTE_MODE) COMPUTE_MODE="${value}" ;;
				API_KEY) API_KEY="${value}" ;;
				BITNET_MODEL) BITNET_MODEL="${value}" ;;
				BITNET_N_PREDICT) BITNET_N_PREDICT="${value}" ;;
				BITNET_THREADS) BITNET_THREADS="${value}" ;;
				BITNET_CTX_SIZE) BITNET_CTX_SIZE="${value}" ;;
				BITNET_TEMPERATURE) BITNET_TEMPERATURE="${value}" ;;
				BITNET_CONVERSATION) BITNET_CONVERSATION="${value}" ;;
				API_BIND_HOST) API_BIND_HOST="${value}" ;;
				API_PORT) API_PORT="${value}" ;;
				UI_BIND_HOST) UI_BIND_HOST="${value}" ;;
				UI_PORT) UI_PORT="${value}" ;;
				ALLOWED_ORIGINS) ALLOWED_ORIGINS="${value}" ;;
			esac
		done <"${env_path}"
	fi

	DEPLOY_MODE="${DEPLOY_MODE,,}"
	COMPUTE_MODE="${COMPUTE_MODE,,}"

	if [[ "${DEPLOY_MODE}" != "local" && "${DEPLOY_MODE}" != "proxy" ]]; then
		if [[ "${API_BIND_HOST}" == "127.0.0.1" && "${UI_BIND_HOST}" == "127.0.0.1" ]]; then
			DEPLOY_MODE="proxy"
		else
			DEPLOY_MODE="local"
		fi
	fi

	if [[ "${COMPUTE_MODE}" != "cpu" && "${COMPUTE_MODE}" != "gpu" ]]; then
		if [[ -f "${runtime_path}" ]] && grep -q "^[[:space:]]*gpus:[[:space:]]*all" "${runtime_path}"; then
			COMPUTE_MODE="gpu"
		elif [[ -f "${runtime_path}" ]] && grep -q 'NVIDIA_VISIBLE_DEVICES:[[:space:]]*""' "${runtime_path}"; then
			COMPUTE_MODE="cpu"
		else
			COMPUTE_MODE="gpu"
		fi
	fi

	if ! validate_port "${API_PORT}"; then
		API_PORT="8000"
	fi
	if ! validate_port "${UI_PORT}"; then
		UI_PORT="8080"
	fi

	if [[ "${BITNET_CONVERSATION}" != "true" && "${BITNET_CONVERSATION}" != "false" ]]; then
		BITNET_CONVERSATION="true"
	fi
}

init_runtime_defaults() {
	DEPLOY_MODE="local"
	COMPUTE_MODE="gpu"
	API_BIND_HOST="0.0.0.0"
	UI_BIND_HOST="0.0.0.0"
	API_PORT="8000"
	UI_PORT="8080"
	API_KEY="change-me"
	BITNET_MODEL="models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf"
	BITNET_N_PREDICT="256"
	BITNET_THREADS="2"
	BITNET_CTX_SIZE="2048"
	BITNET_TEMPERATURE="0.8"
	BITNET_CONVERSATION="true"
	ALLOWED_ORIGINS="*"
}

write_runtime_compose() {
	local runtime_path="${PROJECT_DIR}/docker-compose.runtime.yml"

	if [[ "${COMPUTE_MODE}" == "gpu" ]]; then
		cat >"${runtime_path}" <<'EOF'
services:
  bitnet-api:
    gpus: all
    environment:
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,utility
EOF
	else
		cat >"${runtime_path}" <<'EOF'
services:
  bitnet-api:
    environment:
      NVIDIA_VISIBLE_DEVICES: ""
EOF
	fi

	info "Wrote ${runtime_path}"
}

display_config_overview() {
	local api_key_display="(hidden)"
	if [[ -z "${API_KEY}" ]]; then
		api_key_display="(empty)"
	fi

	cat <<EOF

================ BitNetRTR Setup ================
 1) Deployment mode      : ${DEPLOY_MODE}
 2) Compute mode         : ${COMPUTE_MODE}
 3) API host/port        : ${API_BIND_HOST}:${API_PORT}
 4) UI host/port         : ${UI_BIND_HOST}:${UI_PORT}
 5) API key              : ${api_key_display}
 6) Model path           : ${BITNET_MODEL}
 7) Max tokens           : ${BITNET_N_PREDICT}
 8) CPU threads          : ${BITNET_THREADS}
 9) Context size         : ${BITNET_CTX_SIZE}
10) Temperature          : ${BITNET_TEMPERATURE}
11) Conversation mode    : ${BITNET_CONVERSATION}
12) Allowed origins      : ${ALLOWED_ORIGINS}

 d) Done and continue
 q) Quit without changes
==================================================
EOF
}

set_deploy_mode() {
	local deploy_choice
	local deploy_default="1"
	if [[ "${DEPLOY_MODE}" == "proxy" ]]; then
		deploy_default="2"
	fi

	deploy_choice="$(prompt "Deployment mode: 1) local docker, 2) behind reverse proxy" "${deploy_default}")"

	if [[ "${deploy_choice}" == "2" ]]; then
		DEPLOY_MODE="proxy"
		API_BIND_HOST="127.0.0.1"
		UI_BIND_HOST="127.0.0.1"
		if [[ -z "${ALLOWED_ORIGINS:-}" || "${ALLOWED_ORIGINS}" == "*" ]]; then
			ALLOWED_ORIGINS="http://localhost:${UI_PORT}"
		fi
	else
		DEPLOY_MODE="local"
		API_BIND_HOST="0.0.0.0"
		UI_BIND_HOST="0.0.0.0"
		ALLOWED_ORIGINS="*"
	fi
}

set_compute_mode() {
	local compute_choice
	local compute_default="2"
	if [[ "${COMPUTE_MODE}" == "cpu" ]]; then
		compute_default="1"
	fi

	compute_choice="$(prompt "Compute mode: 1) CPU, 2) GPU" "${compute_default}")"
	if [[ "${compute_choice}" == "1" ]]; then
		COMPUTE_MODE="cpu"
	else
		COMPUTE_MODE="gpu"
	fi
}

collect_runtime_config() {
	info "Interactive setup"

	init_runtime_defaults
	load_saved_runtime_config

	if [[ ${ASSUME_YES} -eq 1 ]]; then
		return
	fi

	while true; do
		display_config_overview
		local choice
		choice="$(prompt "Choose item to edit" "d")"

		case "${choice}" in
			1)
				set_deploy_mode
				;;
			2)
				set_compute_mode
				;;
			3)
				API_PORT="$(prompt "API host port" "${API_PORT}")"
				until validate_port "${API_PORT}"; do
					API_PORT="$(prompt "Invalid port. Enter API host port" "8000")"
				done
				;;
			4)
				UI_PORT="$(prompt "UI host port" "${UI_PORT}")"
				until validate_port "${UI_PORT}"; do
					UI_PORT="$(prompt "Invalid port. Enter UI host port" "8080")"
				done
				if [[ "${DEPLOY_MODE}" == "proxy" && "${ALLOWED_ORIGINS}" == "*" ]]; then
					ALLOWED_ORIGINS="http://localhost:${UI_PORT}"
				fi
				;;
			5)
				API_KEY="$(prompt_secret "API key used by X-API-KEY header" "${API_KEY}")"
				;;
			6)
				BITNET_MODEL="$(prompt "Model path (inside repo)" "${BITNET_MODEL}")"
				;;
			7)
				BITNET_N_PREDICT="$(prompt "Max tokens to generate" "${BITNET_N_PREDICT}")"
				;;
			8)
				BITNET_THREADS="$(prompt "CPU threads" "${BITNET_THREADS}")"
				;;
			9)
				BITNET_CTX_SIZE="$(prompt "Context size" "${BITNET_CTX_SIZE}")"
				;;
			10)
				BITNET_TEMPERATURE="$(prompt "Sampling temperature" "${BITNET_TEMPERATURE}")"
				;;
			11)
				BITNET_CONVERSATION="$(prompt "Conversation mode (true/false)" "${BITNET_CONVERSATION}")"
				BITNET_CONVERSATION="${BITNET_CONVERSATION,,}"
				until [[ "${BITNET_CONVERSATION}" == "true" || "${BITNET_CONVERSATION}" == "false" ]]; do
					BITNET_CONVERSATION="$(prompt "Please enter true or false" "true")"
					BITNET_CONVERSATION="${BITNET_CONVERSATION,,}"
				done
				;;
			12)
				ALLOWED_ORIGINS="$(prompt "CORS allowed origins" "${ALLOWED_ORIGINS}")"
				;;
			d|D)
				break
				;;
			q|Q)
				warn "Cancelled by user."
				exit 0
				;;
			*)
				warn "Unknown selection: ${choice}"
				;;
		esac
	done
}

launch_stack() {
	local compose_cmd=(docker compose -f docker-compose.yml -f docker-compose.runtime.yml)

	if confirm "Build and start containers now?" "y"; then
		(cd "${PROJECT_DIR}" && "${compose_cmd[@]}" up --build -d)
		info "Stack started."

		if [[ "${DEPLOY_MODE}" == "proxy" ]]; then
			info "Proxy mode: services are bound to localhost only."
		fi

		info "UI:  http://${UI_BIND_HOST}:${UI_PORT}"
		info "API: http://${API_BIND_HOST}:${API_PORT}"
		info "Use 'docker compose -f docker-compose.yml -f docker-compose.runtime.yml logs -f' for logs."
	else
		info "Skipped container startup."
	fi
}

compose_cmd() {
	printf '%s\n' "docker compose -f docker-compose.yml -f docker-compose.runtime.yml"
}

MENU_ITEMS=(
	"Configure persisted settings"
	"Start stack"
	"Restart stack (all services)"
	"Restart API service only"
	"Restart UI service only"
	"Stop stack"
	"Rebuild + start stack"
	"View logs (follow)"
	"Download model(s) (container)"
	"Sync/update git submodules"
	"Update wrapper repo (git pull)"
	"Run FastAPI app directly"
	"Run native BitNet CLI"
	"Build BitNet dependency (container)"
	"Quit"
)

TUI_SELECTED=1
TUI_MESSAGE="Use Up/Down arrows to navigate, Enter to run, q to quit."
TUI_STATUS_STACK="unknown"
TUI_STATUS_CONTAINERS="n/a"
TUI_STATUS_REQ_TOTAL="0"
TUI_STATUS_REQ_CHAT="0"
TUI_STATUS_REQ_HEALTH="0"
TUI_STATUS_CPU="n/a"
TUI_STATUS_MEM="n/a"
TUI_STATUS_GPU="n/a"
TUI_STATUS_MODE="n/a"
TUI_STATUS_API="n/a"
TUI_STATUS_UI="n/a"
TUI_CONTAINER_LINES=()
TUI_LAST_REFRESH=0
TUI_REFRESH_INTERVAL=2
TUI_RAW_MODE=0

tui_color() {
	local code="$1"
	if [[ -t 1 ]]; then
		printf '\033[%sm' "${code}"
	fi
}

tui_reset() {
	tui_color "0"
}

tui_set_raw_mode() {
	if [[ ${TUI_RAW_MODE} -eq 0 ]]; then
		stty -echo -icanon time 0 min 0
		TUI_RAW_MODE=1
	fi
}

tui_unset_raw_mode() {
	if [[ ${TUI_RAW_MODE} -eq 1 ]]; then
		stty sane
		TUI_RAW_MODE=0
	fi
}

tui_cleanup() {
	tui_unset_raw_mode
	tput sgr0 2>/dev/null || true
	tput cnorm 2>/dev/null || true
	printf '\n'
}

tui_pad() {
	local text="$1"
	local width="$2"
	if (( width <= 0 )); then
		printf ''
		return
	fi
	if (( ${#text} > width )); then
		printf '%s' "${text:0:width}"
	else
		printf '%-*s' "${width}" "${text}"
	fi
}

tui_print_at() {
	local row="$1"
	local col="$2"
	local text="$3"
	tput cup "${row}" "${col}"
	printf '%s' "${text}"
}

tui_update_host_stats() {
	local cpu_line total idle usage
	cpu_line="$(awk '/^cpu / {print $2,$3,$4,$5,$6,$7,$8,$9,$10,$11}' /proc/stat 2>/dev/null || true)"
	if [[ -n "${cpu_line}" ]]; then
		read -r u n s i io irq sirq st _ _ <<<"${cpu_line}"
		total=$((u + n + s + i + io + irq + sirq + st))
		idle=$((i + io))
		if [[ -n "${TUI_PREV_TOTAL:-}" && -n "${TUI_PREV_IDLE:-}" ]]; then
			local dt=$((total - TUI_PREV_TOTAL))
			local di=$((idle - TUI_PREV_IDLE))
			if (( dt > 0 )); then
				usage=$(( (1000 * (dt - di) / dt + 5) / 10 ))
				TUI_STATUS_CPU="${usage}%"
			fi
		fi
		TUI_PREV_TOTAL="${total}"
		TUI_PREV_IDLE="${idle}"
	fi

	local mem_total mem_available mem_used mem_percent
	mem_total="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
	mem_available="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
	if [[ "${mem_total}" =~ ^[0-9]+$ && "${mem_available}" =~ ^[0-9]+$ && ${mem_total} -gt 0 ]]; then
		mem_used=$((mem_total - mem_available))
		mem_percent=$(( (100 * mem_used) / mem_total ))
		TUI_STATUS_MEM="${mem_percent}% (${mem_used}k/${mem_total}k)"
	fi

	if need_cmd nvidia-smi; then
		local gpu_line
		gpu_line="$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -n1 || true)"
		if [[ -n "${gpu_line}" ]]; then
			local gpu_util gpu_mem_used gpu_mem_total gpu_temp
			IFS=',' read -r gpu_util gpu_mem_used gpu_mem_total gpu_temp <<<"${gpu_line}"
			gpu_util="${gpu_util// /}"
			gpu_mem_used="${gpu_mem_used// /}"
			gpu_mem_total="${gpu_mem_total// /}"
			gpu_temp="${gpu_temp// /}"
			TUI_STATUS_GPU="${gpu_util}% mem ${gpu_mem_used}/${gpu_mem_total} MiB temp ${gpu_temp}C"
		else
			TUI_STATUS_GPU="nvidia-smi unavailable"
		fi
	else
		TUI_STATUS_GPU="not detected"
	fi
}

tui_update_compose_stats() {
	local ps_running services_total
	ps_running="$(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml ps --status running --services 2>/dev/null || true)"
	services_total="$(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml ps --services 2>/dev/null || true)"

	local running_count total_count
	running_count="$(printf '%s\n' "${ps_running}" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
	total_count="$(printf '%s\n' "${services_total}" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"

	if [[ -n "${running_count}" && -n "${total_count}" && "${total_count}" != "0" ]]; then
		TUI_STATUS_STACK="running ${running_count}/${total_count}"
		TUI_STATUS_CONTAINERS="${running_count}/${total_count}"
	else
		TUI_STATUS_STACK="stopped"
		TUI_STATUS_CONTAINERS="0/0"
	fi

	local ps_table
	ps_table="$(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml ps 2>/dev/null || true)"
	mapfile -t TUI_CONTAINER_LINES < <(printf '%s\n' "${ps_table}" | sed -n '2,7p')

	local log_window request_logs
	log_window="90s"
	request_logs="$(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml logs --since "${log_window}" --no-color bitnet-api 2>/dev/null || true)"
	TUI_STATUS_REQ_TOTAL="$(grep -Ec 'GET /|POST /' <<<"${request_logs}" || true)"
	TUI_STATUS_REQ_CHAT="$(grep -Ec 'GET /chat|POST /chat' <<<"${request_logs}" || true)"
	TUI_STATUS_REQ_HEALTH="$(grep -Ec 'GET /healthz' <<<"${request_logs}" || true)"
}

tui_update_status() {
	TUI_STATUS_MODE="${DEPLOY_MODE}/${COMPUTE_MODE}"
	TUI_STATUS_API="${API_BIND_HOST}:${API_PORT}"
	TUI_STATUS_UI="${UI_BIND_HOST}:${UI_PORT}"
	tui_update_host_stats
	tui_update_compose_stats
}

tui_render_screen() {
	local cols rows split left_width right_col right_width
	cols="$(tput cols)"
	rows="$(tput lines)"

	if (( cols < 90 || rows < 24 )); then
		clear
		tui_color "1;33"
		printf 'Terminal is too small (%sx%s). Resize to at least 90x24.\n' "${cols}" "${rows}"
		tui_reset
		printf 'Press q to quit.\n'
		return
	fi

	split=40
	left_width=$((split - 2))
	right_col=$((split + 2))
	right_width=$((cols - right_col - 1))

	clear
	tui_color "1;36"
	tui_print_at 0 0 "$(tui_pad " bitNetRTR Dashboard " "${split}")"
	tui_reset
	tui_color "0;36"
	tui_print_at 0 "${split}" "|"
	tui_reset
	tui_color "1;36"
	tui_print_at 0 "${right_col}" "$(tui_pad " Live Status " "${right_width}")"
	tui_reset

	local r
	for ((r = 1; r < rows; r++)); do
		tui_color "0;36"
		tui_print_at "${r}" "${split}" "|"
		tui_reset
	done

	tui_color "1;34"
	tui_print_at 1 1 "$(tui_pad "Menu (Up/Down, Enter)" "${left_width}")"
	tui_reset

	local i row marker label
	row=3
	for ((i = 0; i < ${#MENU_ITEMS[@]}; i++)); do
		label="${MENU_ITEMS[$i]}"
		if (( i + 1 == TUI_SELECTED )); then
			marker=">"
			tui_color "1;30;46"
			tui_print_at "${row}" 1 "$(tui_pad " ${marker} ${label}" "${left_width}")"
			tui_reset
		else
			tui_print_at "${row}" 1 "$(tui_pad "   ${label}" "${left_width}")"
		fi
		((row++))
	done

	local footer_row
	footer_row=$((rows - 3))
	tui_color "1;35"
	tui_print_at "${footer_row}" 1 "$(tui_pad "${TUI_MESSAGE}" "${left_width}")"
	tui_reset
	tui_print_at $((footer_row + 1)) 1 "$(tui_pad "q=quit  r=refresh" "${left_width}")"

	tui_color "1;34"
	tui_print_at 2 "${right_col}" "$(tui_pad "Runtime" "${right_width}")"
	tui_reset
	tui_print_at 3 "${right_col}" "$(tui_pad "Stack: ${TUI_STATUS_STACK}" "${right_width}")"
	tui_print_at 4 "${right_col}" "$(tui_pad "Containers: ${TUI_STATUS_CONTAINERS}" "${right_width}")"
	tui_print_at 5 "${right_col}" "$(tui_pad "Mode: ${TUI_STATUS_MODE}" "${right_width}")"
	tui_print_at 6 "${right_col}" "$(tui_pad "API: ${TUI_STATUS_API}" "${right_width}")"
	tui_print_at 7 "${right_col}" "$(tui_pad "UI:  ${TUI_STATUS_UI}" "${right_width}")"

	tui_color "1;34"
	tui_print_at 9 "${right_col}" "$(tui_pad "Requests (last ~90s)" "${right_width}")"
	tui_reset
	tui_print_at 10 "${right_col}" "$(tui_pad "Total: ${TUI_STATUS_REQ_TOTAL}" "${right_width}")"
	tui_print_at 11 "${right_col}" "$(tui_pad "Chat: ${TUI_STATUS_REQ_CHAT}" "${right_width}")"
	tui_print_at 12 "${right_col}" "$(tui_pad "Healthz: ${TUI_STATUS_REQ_HEALTH}" "${right_width}")"

	tui_color "1;34"
	tui_print_at 14 "${right_col}" "$(tui_pad "Host Metrics" "${right_width}")"
	tui_reset
	tui_print_at 15 "${right_col}" "$(tui_pad "CPU: ${TUI_STATUS_CPU}" "${right_width}")"
	tui_print_at 16 "${right_col}" "$(tui_pad "Mem: ${TUI_STATUS_MEM}" "${right_width}")"
	tui_print_at 17 "${right_col}" "$(tui_pad "GPU: ${TUI_STATUS_GPU}" "${right_width}")"

	tui_color "1;34"
	tui_print_at 19 "${right_col}" "$(tui_pad "Containers" "${right_width}")"
	tui_reset
	local line_row=20
	if (( ${#TUI_CONTAINER_LINES[@]} == 0 )); then
		tui_print_at "${line_row}" "${right_col}" "$(tui_pad "No compose status available" "${right_width}")"
	else
		for label in "${TUI_CONTAINER_LINES[@]}"; do
			if (( line_row >= rows - 1 )); then
				break
			fi
			tui_print_at "${line_row}" "${right_col}" "$(tui_pad "${label}" "${right_width}")"
			((line_row++))
		done
	fi

	tput cup $((rows - 1)) 0
}

tui_run_action() {
	local action_id="$1"
	local action_name="${MENU_ITEMS[$((action_id - 1))]}"

	tui_unset_raw_mode
	tput cnorm
	clear
	tui_color "1;34"
	printf '[ACTION] %s\n\n' "${action_name}"
	tui_reset

	local ok=1
	case "${action_id}" in
		1)
			collect_runtime_config && write_env_file && write_runtime_compose || ok=0
			load_saved_runtime_config
			;;
		2)
			start_stack || ok=0
			;;
		3)
			restart_stack || ok=0
			;;
		4)
			restart_service bitnet-api || ok=0
			;;
		5)
			restart_service bitnet-ui || ok=0
			;;
		6)
			stop_stack || ok=0
			;;
		7)
			rebuild_stack || ok=0
			;;
		8)
			follow_logs || ok=0
			;;
		9)
			download_models_in_container || ok=0
			;;
		10)
			ensure_bitnet_submodule || ok=0
			;;
		11)
			update_repo || ok=0
			;;
		12)
			run_app_backend
			;;
		13)
			run_native_bitnet
			;;
		14)
			build_bitnet_dependency_in_container || ok=0
			;;
		15)
			info "Bye."
			exit 0
			;;
		*)
			ok=0
			;;
	esac

	if [[ ${ok} -eq 1 ]]; then
		TUI_MESSAGE="Done: ${action_name}"
	else
		TUI_MESSAGE="Failed: ${action_name}"
	fi

	printf '\nPress Enter to return to dashboard...'
	read -r _
	tui_set_raw_mode
	tput civis
	TUI_LAST_REFRESH=0
}

tui_read_key() {
	local key rest
	if ! read -rsn1 -t 0.2 key; then
		printf ''
		return
	fi

	if [[ "${key}" == $'\e' ]]; then
		read -rsn1 -t 0.001 rest || true
		if [[ "${rest}" == "[" ]]; then
			read -rsn1 -t 0.001 rest || true
			case "${rest}" in
				A)
					printf 'UP'
					return
					;;
				B)
					printf 'DOWN'
					return
					;;
			esac
		fi
		printf 'ESC'
		return
	fi

	if [[ "${key}" == "" || "${key}" == $'\n' ]]; then
		printf 'ENTER'
		return
	fi

	printf '%s' "${key}"
}

tui_menu_loop() {
	trap tui_cleanup EXIT INT TERM
	tput civis
	tui_set_raw_mode

	while true; do
		local now
		now="$(date +%s)"
		if (( now - TUI_LAST_REFRESH >= TUI_REFRESH_INTERVAL )); then
			tui_update_status
			TUI_LAST_REFRESH="${now}"
		fi

		tui_render_screen

		local key
		key="$(tui_read_key)"
		case "${key}" in
			UP)
				if (( TUI_SELECTED > 1 )); then
					((TUI_SELECTED--))
				else
					TUI_SELECTED=${#MENU_ITEMS[@]}
				fi
				;;
			DOWN)
				if (( TUI_SELECTED < ${#MENU_ITEMS[@]} )); then
					((TUI_SELECTED++))
				else
					TUI_SELECTED=1
				fi
				;;
			ENTER)
				tui_run_action "${TUI_SELECTED}"
				;;
			q|Q)
				info "Bye."
				return
				;;
			r|R)
				TUI_LAST_REFRESH=0
				TUI_MESSAGE="Dashboard refreshed"
				;;
			esac
	done
}

ensure_runtime_files() {
	init_runtime_defaults
	load_saved_runtime_config
	write_env_file
	write_runtime_compose
}

stack_status_text() {
	if docker compose -f docker-compose.yml -f docker-compose.runtime.yml ps --status running >/dev/null 2>&1; then
		local running_count
		running_count="$(docker compose -f docker-compose.yml -f docker-compose.runtime.yml ps --status running --services | wc -l | tr -d ' ')"
		printf 'running(%s services)' "${running_count}"
		return
	fi
	printf 'unknown'
}

display_main_menu() {
	cat <<EOF

================ BitNetRTR Control ================
 Project     : ${PROJECT_DIR}
 Stack       : $(stack_status_text)
 Deploy mode : ${DEPLOY_MODE}
 Compute     : ${COMPUTE_MODE}
 API         : ${API_BIND_HOST}:${API_PORT}
 UI          : ${UI_BIND_HOST}:${UI_PORT}

 1) Configure persisted settings
 2) Start stack
 3) Restart stack (all services)
 4) Restart API service only
 5) Restart UI service only
 6) Stop stack
 7) Rebuild + start stack
 8) View logs (follow)
 9) Download model(s) (container)
10) Sync/update git submodules
11) Update wrapper repo (git pull)
12) Run FastAPI app directly
13) Run native BitNet CLI
14) Build BitNet dependency (container)
 q) Quit
===================================================
EOF
}

download_models_in_container() {
	ensure_runtime_files
	info "Download selected model(s) inside container"

	cat <<'EOF'
Supported model sources:
  1) 1bitLLM/bitnet_b1_58-large               -> models/bitnet_b1_58-large
  2) 1bitLLM/bitnet_b1_58-3B                  -> models/bitnet_b1_58-3B
  3) HF1BitLLM/Llama3-8B-1.58-100B-tokens     -> models/Llama3-8B-1.58-100B-tokens
  4) tiiuae/Falcon3-1B-Instruct-1.58bit       -> models/Falcon3-1B-Instruct-1.58bit
  5) tiiuae/Falcon3-3B-Instruct-1.58bit       -> models/Falcon3-3B-Instruct-1.58bit
  6) tiiuae/Falcon3-7B-Instruct-1.58bit       -> models/Falcon3-7B-Instruct-1.58bit
  7) tiiuae/Falcon3-10B-Instruct-1.58bit      -> models/Falcon3-10B-Instruct-1.58bit

Enter one or more numbers (comma/space separated), e.g.:
  1,3,7
EOF

	local selection
	selection="$(prompt "Model selection" "1")"
	if [[ -z "${selection}" ]]; then
		warn "No selection provided."
		return 1
	fi

	local -a picked_ids=()
	local token
	selection="${selection//,/ }"
	for token in ${selection}; do
		case "${token}" in
			1|2|3|4|5|6|7)
				picked_ids+=("${token}")
				;;
			*)
				warn "Ignoring invalid selection: ${token}"
				;;
		esac
	done

	if [[ ${#picked_ids[@]} -eq 0 ]]; then
		err "No valid model selections."
		return 1
	fi

	# Ensure API container exists and has required tooling context.
	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d bitnet-api)

	local id hf_repo model_dir cmd
	for id in "${picked_ids[@]}"; do
		hf_repo=""
		model_dir=""
		case "${id}" in
			1)
				hf_repo="1bitLLM/bitnet_b1_58-large"
				model_dir="models/bitnet_b1_58-large"
				;;
			2)
				hf_repo="1bitLLM/bitnet_b1_58-3B"
				model_dir="models/bitnet_b1_58-3B"
				;;
			3)
				hf_repo="HF1BitLLM/Llama3-8B-1.58-100B-tokens"
				model_dir="models/Llama3-8B-1.58-100B-tokens"
				;;
			4)
				hf_repo="tiiuae/Falcon3-1B-Instruct-1.58bit"
				model_dir="models/Falcon3-1B-Instruct-1.58bit"
				;;
			5)
				hf_repo="tiiuae/Falcon3-3B-Instruct-1.58bit"
				model_dir="models/Falcon3-3B-Instruct-1.58bit"
				;;
			6)
				hf_repo="tiiuae/Falcon3-7B-Instruct-1.58bit"
				model_dir="models/Falcon3-7B-Instruct-1.58bit"
				;;
			7)
				hf_repo="tiiuae/Falcon3-10B-Instruct-1.58bit"
				model_dir="models/Falcon3-10B-Instruct-1.58bit"
				;;
		esac

		info "Downloading ${hf_repo} -> ${model_dir}"
		cmd=$(cat <<EOS
set -e
cd /app/BitNet/third_party/BitNet
python3 setup_env.py --hf-repo ${hf_repo} --model-dir /app/BitNet/${model_dir} --quant-type i2_s
EOS
)

		(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml exec -T bitnet-api bash -lc "${cmd}")
		info "Downloaded: ${model_dir}"
	done

	info "Model download(s) completed."
}

build_bitnet_dependency_in_container() {
	ensure_runtime_files
	info "Preparing BitNet dependency build inside container"

	# Ensure API container exists so we can build in the same runtime environment.
	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d bitnet-api)

	local build_script
	build_script=$(cat <<'EOS'
set -e
cd /app/BitNet/third_party/BitNet

git config --global --add safe.directory /app/BitNet/third_party/BitNet || true
git config --global --add safe.directory /app/BitNet/third_party/BitNet/3rdparty/llama.cpp || true
git config --global --add safe.directory /app/BitNet/third_party/BitNet/3rdparty/llama.cpp/ggml/src/kompute || true

python3 -m pip install --no-cache-dir 3rdparty/llama.cpp/gguf-py >/dev/null 2>&1 || true

arch="$(uname -m)"
if [[ "$arch" == "x86_64" || "$arch" == "amd64" || "$arch" == "x86" ]]; then
	if [[ ! -f include/bitnet-lut-kernels.h || ! -f include/kernel_config.ini ]]; then
		python3 utils/codegen_tl2.py --model bitnet_b1_58-3B --BM 160,320,320 --BK 96,96,96 --bm 32,32,32
	fi
	if command -v g++ >/dev/null 2>&1 && command -v gcc >/dev/null 2>&1; then
		cmake -B build -DBITNET_X86_TL2=OFF -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ -DCMAKE_CXX_FLAGS=-fpermissive
	else
		cmake -B build -DBITNET_X86_TL2=OFF -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
	fi
elif [[ "$arch" == "aarch64" || "$arch" == "arm64" ]]; then
	if [[ ! -f include/bitnet-lut-kernels.h || ! -f include/kernel_config.ini ]]; then
		python3 utils/codegen_tl1.py --model bitnet_b1_58-3B --BM 160,320,320 --BK 64,128,64 --bm 32,64,32
	fi
	if command -v g++ >/dev/null 2>&1 && command -v gcc >/dev/null 2>&1; then
		cmake -B build -DBITNET_ARM_TL1=OFF -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ -DCMAKE_CXX_FLAGS=-fpermissive
	else
		cmake -B build -DBITNET_ARM_TL1=OFF -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
	fi
else
	echo "Unsupported architecture for orchestrated build: $arch" >&2
	exit 1
fi

cmake --build build --config Release

if [[ ! -x build/bin/llama-cli && ! -x build/bin/Release/llama-cli.exe ]]; then
	echo "llama-cli not found after build" >&2
	exit 1
fi
EOS
)

	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml exec -T bitnet-api bash -lc "${build_script}")
	info "BitNet dependency build completed inside container."
}

start_stack() {
	ensure_runtime_files
	build_bitnet_dependency_in_container
	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d)
	info "Stack started. UI: http://${UI_BIND_HOST}:${UI_PORT} API: http://${API_BIND_HOST}:${API_PORT}"
}

restart_stack() {
	ensure_runtime_files
	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml restart)
	info "All services restarted."
}

restart_service() {
	local service="$1"
	ensure_runtime_files
	if [[ "${service}" == "bitnet-api" ]]; then
		build_bitnet_dependency_in_container
	fi
	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml restart "${service}")
	info "Service restarted: ${service}"
}

stop_stack() {
	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml stop)
	info "Stack stopped."
}

rebuild_stack() {
	ensure_runtime_files
	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml up --build -d)
	build_bitnet_dependency_in_container
	info "Stack rebuilt and started."
}

follow_logs() {
	ensure_runtime_files
	info "Press Ctrl+C to stop following logs and return to menu."
	(cd "${PROJECT_DIR}" && docker compose -f docker-compose.yml -f docker-compose.runtime.yml logs -f)
}

update_repo() {
	info "Updating wrapper repository"
	git -C "${PROJECT_DIR}" fetch --all --prune
	git -C "${PROJECT_DIR}" pull --ff-only || true
	ensure_bitnet_submodule
}

interactive_control_menu() {
	if [[ ${SKIP_DEPS} -eq 0 ]]; then
		if [[ -f /etc/debian_version ]]; then
			ensure_apt_packages git curl ca-certificates
		fi
		ensure_docker
		ensure_docker_access
	fi

	install_self_launcher
	ensure_bitnet_submodule
	init_runtime_defaults
	load_saved_runtime_config

	while true; do
		display_main_menu
		local choice
		choice="$(prompt "Choose action" "2")"
		case "${choice}" in
			1)
				collect_runtime_config
				write_env_file
				write_runtime_compose
				;;
			2)
				start_stack
				;;
			3)
				restart_stack
				;;
			4)
				restart_service bitnet-api
				;;
			5)
				restart_service bitnet-ui
				;;
			6)
				stop_stack
				;;
			7)
				rebuild_stack
				;;
			8)
				follow_logs
				;;
			9)
				download_models_in_container
				;;
			10)
				ensure_bitnet_submodule
				;;
			11)
				update_repo
				;;
			12)
				run_app_backend
				;;
			13)
				run_native_bitnet
				;;
			14)
				build_bitnet_dependency_in_container
				;;
			q|Q)
				info "Bye."
				exit 0
				;;
			*)
				warn "Unknown selection: ${choice}"
				;;
		esac
	done
}

whiptail_status_snapshot() {
	tui_update_status
	cat <<EOF
BitNetRTR Runtime Snapshot

Stack:      ${TUI_STATUS_STACK}
Containers: ${TUI_STATUS_CONTAINERS}
Mode:       ${TUI_STATUS_MODE}
API:        ${TUI_STATUS_API}
UI:         ${TUI_STATUS_UI}

Requests (~90s)
- Total:    ${TUI_STATUS_REQ_TOTAL}
- Chat:     ${TUI_STATUS_REQ_CHAT}
- Healthz:  ${TUI_STATUS_REQ_HEALTH}

Host
- CPU:      ${TUI_STATUS_CPU}
- Memory:   ${TUI_STATUS_MEM}
- GPU:      ${TUI_STATUS_GPU}
EOF
}

run_live_monitor() {
	local compose=(docker compose -f docker-compose.yml -f docker-compose.runtime.yml)
	info "Launching monitor. Press q to quit monitor."
	(cd "${PROJECT_DIR}" && watch -n 1 "
echo '=== BitNetRTR Monitor ===';
echo;
${compose[*]} ps;
echo;
echo '--- Recent API requests (90s) ---';
${compose[*]} logs --since 90s --no-color bitnet-api 2>/dev/null | grep -E 'GET /|POST /' | tail -n 20;
echo;
echo '--- Container resource usage ---';
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}'
")
}

whiptail_menu_loop() {
	local title="BitNetRTR Control"
	local choice

	while true; do
		choice="$(whiptail \
			--title "${title}" \
			--menu "Choose an action" 24 92 15 \
			"1"  "Configure persisted settings" \
			"2"  "Start stack" \
			"3"  "Restart stack (all services)" \
			"4"  "Restart API service only" \
			"5"  "Restart UI service only" \
			"6"  "Stop stack" \
			"7"  "Rebuild + start stack" \
			"8"  "View logs (follow)" \
			"9"  "Download model(s) (container)" \
			"10" "Sync/update git submodules" \
			"11" "Update wrapper repo (git pull)" \
			"12" "Run FastAPI app directly" \
			"13" "Run native BitNet CLI" \
			"14" "Build BitNet dependency (container)" \
			"15" "Show status snapshot" \
			"16" "Live monitor (watch)" \
			"q"  "Quit" \
			3>&1 1>&2 2>&3)"

		local exit_code=$?
		if [[ ${exit_code} -ne 0 || "${choice}" == "q" ]]; then
			info "Bye."
			return
		fi

		case "${choice}" in
			1)
				collect_runtime_config
				write_env_file
				write_runtime_compose
				load_saved_runtime_config
				;;
			2)
				start_stack
				;;
			3)
				restart_stack
				;;
			4)
				restart_service bitnet-api
				;;
			5)
				restart_service bitnet-ui
				;;
			6)
				stop_stack
				;;
			7)
				rebuild_stack
				;;
			8)
				follow_logs
				;;
			9)
				download_models_in_container
				;;
			10)
				ensure_bitnet_submodule
				;;
			11)
				update_repo
				;;
			12)
				run_app_backend
				;;
			13)
				run_native_bitnet
				;;
			14)
				build_bitnet_dependency_in_container
				;;
			15)
				whiptail --title "BitNetRTR Status" --msgbox "$(whiptail_status_snapshot)" 24 92
				;;
			16)
				run_live_monitor
				;;
			esac
	done
}

run_app_backend() {
	local app_args=("$@")
	ensure_bitnet_submodule
	exec python3 "${PROJECT_DIR}/app.py" "${app_args[@]}"
}

run_native_bitnet() {
	local native_args=("$@")
	local bitnet_repo_dir="${PROJECT_DIR}/third_party/BitNet"
	ensure_bitnet_submodule

	if [[ ! -d "${bitnet_repo_dir}" ]]; then
		err "BitNet repo not found: ${bitnet_repo_dir}"
		exit 1
	fi

	cd "${bitnet_repo_dir}"
	if [[ ! -x "./bitnet.sh" ]]; then
		err "Missing executable: ${bitnet_repo_dir}/bitnet.sh"
		exit 1
	fi

	exec ./bitnet.sh "${native_args[@]}"
}

main() {
	local run_mode="setup"
	local arg_count=$#
	case "${1:-}" in
		menu)
			run_mode="menu"
			shift
			;;
		setup)
			shift
			;;
		app|app.py)
			run_mode="app"
			shift
			;;
		native)
			run_mode="native"
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
	esac

	if [[ "${run_mode}" == "menu" || ( ${arg_count} -eq 0 && -t 0 ) ]]; then
		bootstrap_if_needed
		PROJECT_DIR="$(cd "${PROJECT_DIR}" && pwd)"
		info "Using project: ${PROJECT_DIR}"
		interactive_control_menu
	fi

	if [[ "${run_mode}" != "setup" ]]; then
		bootstrap_if_needed
		PROJECT_DIR="$(cd "${PROJECT_DIR}" && pwd)"
		info "Using project: ${PROJECT_DIR}"
		if [[ "${run_mode}" == "app" ]]; then
			run_app_backend "$@"
		fi
		run_native_bitnet "$@"
	fi

	parse_args "$@"

	bootstrap_if_needed
	PROJECT_DIR="$(cd "${PROJECT_DIR}" && pwd)"

	info "Using project: ${PROJECT_DIR}"

	if [[ ${SKIP_DEPS} -eq 0 ]]; then
		if [[ -f /etc/debian_version ]]; then
			ensure_apt_packages git curl ca-certificates
		fi
		ensure_docker
		ensure_docker_access
	fi

	install_self_launcher
	ensure_bitnet_submodule
	collect_runtime_config
	write_env_file
	write_runtime_compose

	if [[ "${COMPUTE_MODE}" == "gpu" && ${SKIP_DEPS} -eq 0 && -f /etc/debian_version ]]; then
		install_nvidia_toolkit_apt
	fi

	launch_stack
}

main "$@"
