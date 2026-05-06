#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

GENERATED_PKL="${SCRIPT_DIR}/generated_tool_config.pkl"
CONFIG_PKL="${SCRIPT_DIR}/tool_configuration.pkl"
OUTPUT_DIR="${SCRIPT_DIR}/build"
OUTPUT_FILE="${1:-${OUTPUT_DIR}/tool_configuration.yaml}"

if ! command -v uv >/dev/null 2>&1; then
  echo "Missing required command: uv" >&2
  exit 1
fi

if ! command -v pkl >/dev/null 2>&1; then
  echo "Missing required command: pkl" >&2
  exit 1
fi

mkdir -p "$(dirname "${OUTPUT_FILE}")"

uv run --project "${REPO_ROOT}/generator" \
  python "${SCRIPT_DIR}/generate_tool_config.py" \
  --output "${GENERATED_PKL}"

pkl format --write "${GENERATED_PKL}" "${CONFIG_PKL}" "${SCRIPT_DIR}/tool_config_schema.pkl"
pkl eval "${GENERATED_PKL}" >/dev/null
pkl eval "${CONFIG_PKL}" >/dev/null
pkl eval -f yaml -o "${OUTPUT_FILE}" "${CONFIG_PKL}"

echo "Wrote ${OUTPUT_FILE}"
