#!/bin/bash
# A dedicated local environment for the existing 09_crashpod fixture.
set -euo pipefail
umask 077

TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASK_STATE="${HOLMES_STUDY_STATE_DIR:-$HOME/.holmes/study}"
TASK_CLUSTER="holmes-study"
TASK_NODE_IMAGE="kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
export DOCKER_CONTEXT="colima-holmes-study"
export KUBECONFIG="$TASK_STATE/kubeconfig"
mkdir -p "$TASK_STATE"
chmod 700 "$TASK_STATE"
cd "$TASK_ROOT"

usage() {
  echo 'Usage: bash study/local-case.sh {start|setup|inspect|collect|eval|cleanup|stop}'
}

check_cluster() {
  local task_context
  task_context="$(kubectl config current-context)"
  if [ "$task_context" != "kind-$TASK_CLUSTER" ]; then
    echo "Refusing to operate on context '$task_context'; expected kind-$TASK_CLUSTER." >&2
    exit 1
  fi
  kubectl get --raw=/readyz --request-timeout=10s >/dev/null
}

fixture_script() {
  poetry run python - "$1" <<'PY'
import subprocess
import sys
from pathlib import Path

import yaml

fixture = Path('tests/llm/fixtures/test_ask_holmes/09_crashpod/test_case.yaml')
case = yaml.safe_load(fixture.read_text())
subprocess.run(['/bin/bash', '-e', '-c', case[sys.argv[1]]], cwd=fixture.parent, check=True)
PY
}

load_model() {
  local task_config="$TASK_STATE/deepseek.env"
  if [ ! -f "$task_config" ]; then
    echo "Fill $task_config with DEEPSEEK_BASE_URL, DEEPSEEK_MODEL_ID and DEEPSEEK_API_KEY first." >&2
    exit 1
  fi
  # This is the user's own local configuration, outside the Git checkout.
  set -a
  source "$task_config"
  set +a
  : "${DEEPSEEK_BASE_URL:?Fill DEEPSEEK_BASE_URL in the local config}"
  : "${DEEPSEEK_MODEL_ID:?Fill DEEPSEEK_MODEL_ID with the exact API model ID}"
  : "${DEEPSEEK_API_KEY:?Fill DEEPSEEK_API_KEY in the local config}"
  export OPENAI_API_BASE="$DEEPSEEK_BASE_URL"
  export OPENAI_API_KEY="$DEEPSEEK_API_KEY"
  export MODEL="openai/$DEEPSEEK_MODEL_ID"
  # The judge uses the OpenAI-compatible SDK and needs the raw provider ID.
  export CLASSIFIER_MODEL="$DEEPSEEK_MODEL_ID"
  export OVERRIDE_MAX_CONTENT_SIZE=128000
  # Thinking tokens count against this cap. At 4096 the first run ended with an
  # empty final answer, so the local budget is raised for this model.
  export OVERRIDE_MAX_OUTPUT_TOKEN=16384
  # Local adapter for the Judge: this provider rejects the forced tool_choice
  # autoevals sends, so the plugin disables thinking for the Judge request only.
  # The investigation model is unaffected. See local-environment.md.
  export PYTHONPATH="$TASK_ROOT/study:$TASK_STATE${PYTHONPATH:+:$PYTHONPATH}"
  export RUN_LIVE=true
  export ITERATIONS=1
  unset MODEL_LIST_FILE_LOCATION BRAINTRUST_API_KEY BRAINTRUST_SERVICE_TOKEN
  unset AZURE_API_BASE AZURE_API_KEY AZURE_API_VERSION
}

case "${1:-help}" in
  start)
    colima start "$TASK_CLUSTER" --cpu 4 --memory 6 --disk 30 \
      --vm-type vz --runtime docker --mount-type virtiofs \
      --activate=false --downloader curl
    task_clusters="$(kind get clusters)"
    if printf '%s\n' "$task_clusters" | grep -Fxq "$TASK_CLUSTER"; then
      kind export kubeconfig --name "$TASK_CLUSTER" --kubeconfig "$KUBECONFIG"
    else
      # Use the daemon's VM-reachable proxy inside the kind node. A host
      # loopback proxy would point at the node itself and prevent image pulls.
      (
        task_http_proxy="$(docker info --format '{{.HTTPProxy}}')"
        task_https_proxy="$(docker info --format '{{.HTTPSProxy}}')"
        export HTTP_PROXY="$task_http_proxy" HTTPS_PROXY="$task_https_proxy"
        export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY"
        kind create cluster --name "$TASK_CLUSTER" --image "$TASK_NODE_IMAGE" \
          --kubeconfig "$KUBECONFIG" --wait 120s
      )
    fi
    chmod 600 "$KUBECONFIG"
    check_cluster
    kubectl get nodes
    ;;
  setup)
    check_cluster
    fixture_script before_test
    ;;
  inspect)
    check_cluster
    kubectl get deployment,pods -n app-09
    task_pod="$(kubectl get pods -n app-09 -l app=payment-processing-worker -o jsonpath='{.items[0].metadata.name}')"
    # The latest container has already exited in this fixture. Older container
    # logs can disappear during rapid restarts, so read the latest attempt.
    kubectl logs "$task_pod" -n app-09 --tail=20
    ;;
  collect)
    poetry run pytest tests/llm/test_ask_holmes.py -k '09_crashpod' \
      --collect-only -q --no-cov -n 0
    ;;
  eval)
    load_model
    check_cluster
    task_run="$(mktemp -d "$TASK_STATE/eval-09-XXXXXXXX")"
    chmod 700 "$task_run"
    echo "Evaluation log: $task_run/eval.log"
    # The fixture owns setup and cleanup. Keep the log and the report even when
    # the eval fails, and still return pytest's status to the caller.
    task_status=0
    poetry run pytest tests/llm/test_ask_holmes.py -k '09_crashpod' \
      --no-cov --no-retry-on-throttle -n 0 -vv -s -p judge_thinking_adapter 2>&1 \
      | tee "$task_run/eval.log" || task_status=$?
    if [ -f evals_report.md ]; then
      cp evals_report.md "$task_run/evals_report.md"
    fi
    exit "$task_status"
    ;;
  cleanup)
    check_cluster
    fixture_script after_test
    ;;
  stop)
    colima stop "$TASK_CLUSTER"
    ;;
  help|--help|-h)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
