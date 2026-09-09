#!/bin/bash
NAMESPACE=redhat-ai-gateway-infra
oc -n $NAMESPACE get secret postgresql >/dev/null
if [ $? -gt 0 ]; then
  POSTGRES_PASSWORD=$(openssl rand -hex 32)
  oc -n $NAMESPACE new-app postgresql-ephemeral -p POSTGRESQL_DATABASE=maas -p POSTGRESQL_USER=maas -p POSTGRESQL_PASSWORD=$POSTGRES_PASSWORD
fi

echo "Waiting for secret/postgresql to be created in $NAMESPACE namespace..."
until oc get secret/postgresql -n $NAMESPACE &>/dev/null; do
  sleep 2
done

echo "Waiting for deploymentconfig/postgresql to be ready..."
until oc get dc/postgresql -n $NAMESPACE &>/dev/null; do
  sleep 2
done
oc rollout status dc/postgresql -n $NAMESPACE --timeout=300s

POSTGRES_PASSWORD=$(oc get secret -n $NAMESPACE postgresql -o json | jq -r '.data["database-password"]' | base64 -d)
oc apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  namespace: redhat-ai-gateway-infra
  name: maas-db-config
data:
  DB_CONNECTION_URL: $(echo "postgresql://maas:${POSTGRES_PASSWORD}@postgresql:5432/maas" | base64 -w0)
EOF
