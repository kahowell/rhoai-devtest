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
RESPONSE_FILE=$(mktemp)
HTTP_STATUS=$(curl -sS -k -o "$RESPONSE_FILE" -w "%{http_code}" \
  -H "Authorization: Bearer ${OC_TOKEN}" \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{"name": "validation-key", "description": "Key for validation", "expiresIn": "1h", "subscription": "facebook-opt-125m-cpu-subscription"}' \
  "${MAAS_API_URL}/maas-api/v1/api-keys" || echo "000")

API_KEY_RESPONSE=$(cat "$RESPONSE_FILE" 2>/dev/null || echo "")
rm -f "$RESPONSE_FILE"

API_KEY=$(echo "$API_KEY_RESPONSE" | jq -r .key 2>/dev/null || echo "")
if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ]; then
  echo "Error: Failed to obtain a valid API key." >&2
  echo "Target URL: ${MAAS_API_URL}/maas-api/v1/api-keys" >&2
  echo "HTTP Status Code: ${HTTP_STATUS}" >&2
  if [ -n "$API_KEY_RESPONSE" ]; then
    echo "Response received:" >&2
    echo "$API_KEY_RESPONSE" >&2
  else
    echo "Response received: <empty body>" >&2
  fi
  exit 1
fi

echo "API key obtained: ${API_KEY:0:20}..."

sleep 5

echo "Fetching models..."
RESPONSE_FILE=$(mktemp)
HTTP_STATUS=$(curl -sS -k -o "$RESPONSE_FILE" -w "%{http_code}" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  "${MAAS_API_URL}/maas-api/v1/models" || echo "000")

MODELS=$(cat "$RESPONSE_FILE" 2>/dev/null || echo "")
rm -f "$RESPONSE_FILE"

if [ -z "$MODELS" ] || [ "$MODELS" = "null" ] || [ "$HTTP_STATUS" != "200" ]; then
  echo "Error: Failed to fetch models list." >&2
  echo "Target URL: ${MAAS_API_URL}/maas-api/v1/models" >&2
  echo "HTTP Status Code: ${HTTP_STATUS}" >&2
  if [ -n "$MODELS" ]; then
    echo "Response received:" >&2
    echo "$MODELS" >&2
  else
    echo "Response received: <empty body>" >&2
  fi
  exit 1
fi

echo $MODELS

MODEL_URL=$(echo "$MODELS" | jq -r '.data[0].url' 2>/dev/null || echo "")
if [ -z "$MODEL_URL" ] || [ "$MODEL_URL" = "null" ]; then
  echo "Error: Failed to obtain model URL." >&2
  echo "Models response was:" >&2
  echo "$MODELS" | jq . 2>/dev/null || echo "$MODELS" >&2
  exit 1
fi

MODEL_ID=$(echo "$MODELS" | jq -r '.data[0].id')

echo "Model URL: $MODEL_URL"

echo "Sending completions request..."
RESPONSE_FILE=$(mktemp)
HTTP_STATUS=$(curl -sS -k -o "$RESPONSE_FILE" -w "%{http_code}" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'$MODEL_ID'",
    "prompt": "San Francisco is a",
    "max_tokens": 7,
    "temperature": 0
  }' \
  "${MODEL_URL}v1/completions" || echo "000")

COMPLETION_RESPONSE=$(cat "$RESPONSE_FILE" 2>/dev/null || echo "")
rm -f "$RESPONSE_FILE"

if [ "$HTTP_STATUS" != "200" ]; then
  echo "Error: Completions request failed." >&2
  echo "Target URL: ${MODEL_URL}v1/completions" >&2
  echo "HTTP Status Code: $HTTP_STATUS" >&2
  if [ -n "$COMPLETION_RESPONSE" ]; then
    echo "Response received:" >&2
    echo "$COMPLETION_RESPONSE" >&2
  else
    echo "Response received: <empty body>" >&2
  fi
  exit 1
else
  echo "$COMPLETION_RESPONSE"
fi
