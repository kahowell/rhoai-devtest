#!/bin/bash
set -euo pipefail

# Retrieve cluster domain
CLUSTER_DOMAIN=$(kubectl get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}' 2>/dev/null || true)
if [ -z "$CLUSTER_DOMAIN" ]; then
  echo "Error: Failed to retrieve CLUSTER_DOMAIN. Is kubectl configured and logged in?" >&2
  exit 1
fi

MAAS_API_URL="https://maas.${CLUSTER_DOMAIN}"

# Obtain OpenShift token
OC_TOKEN=$(oc whoami -t 2>/dev/null || true)
if [ -z "$OC_TOKEN" ]; then
  echo "Error: Failed to obtain OpenShift token. Are you logged in via 'oc'?" >&2
  exit 1
fi

echo "Waiting for MaaSSubscription facebook-opt-125m-cpu-subscription to be Active..."
until oc get maassubscription -n models-as-a-service facebook-opt-125m-cpu-subscription &>/dev/null; do
  echo "MaaSSubscription does not exist yet. Waiting..."
  sleep 2
done
oc wait --for=jsonpath='{.status.phase}'=Active maassubscription/facebook-opt-125m-cpu-subscription -n models-as-a-service --timeout=300s

echo "Obtaining API key..."
API_KEY_RESPONSE=$(curl -sSk \
  -H "Authorization: Bearer ${OC_TOKEN}" \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{"name": "validation-key", "description": "Key for validation", "expiresIn": "1h", "subscription": "facebook-opt-125m-cpu-subscription"}' \
  "${MAAS_API_URL}/maas-api/v1/api-keys")

API_KEY=$(echo "$API_KEY_RESPONSE" | jq -r .key || echo "")
if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ]; then
  echo "Error: Failed to obtain a valid API key." >&2
  echo "Response received: $API_KEY_RESPONSE" >&2
  exit 1
fi

echo "API key obtained: ${API_KEY:0:20}..."

echo "Fetching models..."
MODELS=$(curl -sSk "${MAAS_API_URL}/maas-api/v1/models" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_KEY")

if [ -z "$MODELS" ] || [ "$MODELS" = "null" ]; then
  echo "Error: Failed to fetch models list." >&2
  exit 1
fi

MODEL_URL=$(echo "$MODELS" | jq -r '.data[0].url')
if [ -z "$MODEL_URL" ] || [ "$MODEL_URL" = "null" ]; then
  echo "Error: Failed to obtain model URL." >&2
  echo "Models response was:" >&2
  echo "$MODELS" | jq . >&2
  exit 1
fi

echo "Model URL: $MODEL_URL"

echo "Sending completions request..."
curl -k "$MODEL_URL/v1/completions" \
-H "Authorization: Bearer $API_KEY" \
-H "Content-Type: application/json" \
-d '{
  "model": "facebook/opt-125m",
  "prompt": "San Francisco is a",
  "max_tokens": 7,
  "temperature": 0
}'
