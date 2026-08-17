CLUSTER_DOMAIN=$(kubectl get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
MAAS_API_URL="https://maas.${CLUSTER_DOMAIN}"
API_KEY_RESPONSE=$(curl -sSk \
  -H "Authorization: Bearer $(oc whoami -t)" \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{"name": "validation-key", "description": "Key for validation", "expiresIn": "1h", "subscription": "facebook-opt-125m-cpu-subscription"}' \
  "${MAAS_API_URL}/maas-api/v1/api-keys") && \
API_KEY=$(echo $API_KEY_RESPONSE | jq -r .key) && \
echo "API key obtained: ${API_KEY:0:20}..."

MODELS=$(curl -sSk ${HOST}/maas-api/v1/models \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_KEY" | jq -r .)

MODEL_URL=$(echo $MODELS | jq -r '.data[0].url') && \
echo "Model URL: $MODEL_URL"

curl -k $MODEL_URL/v1/completions \
-H "Authorization: Bearer $API_KEY" \
-H "Content-Type: application/json" \
-d '{
  "model": "facebook/opt-125m",
  "prompt": "San Francisco is a",
  "max_tokens": 7,
  "temperature": 0
}'
