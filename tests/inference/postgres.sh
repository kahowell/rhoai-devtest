#!/bin/bash
oc -n redhat-ods-applications get secret postgresql >/dev/null
if [ $? -gt 0 ]; then
  # Use hex (not base64) so the password can never contain URL-unsafe
  # characters like /, +, or = that break DB_CONNECTION_URL parsing below.
  POSTGRES_PASSWORD=$(openssl rand -hex 32)
  oc -n redhat-ods-applications new-app postgresql-ephemeral -p POSTGRESQL_DATABASE=maas -p POSTGRESQL_USER=maas -p POSTGRESQL_PASSWORD=$POSTGRES_PASSWORD
fi

echo "Waiting for secret/postgresql to be created in redhat-ods-applications namespace..."
until oc get secret/postgresql -n redhat-ods-applications &>/dev/null; do
  sleep 2
done

echo "Waiting for deploymentconfig/postgresql to be ready..."
until oc get dc/postgresql -n redhat-ods-applications &>/dev/null; do
  sleep 2
done
oc rollout status dc/postgresql -n redhat-ods-applications --timeout=300s

POSTGRES_PASSWORD=$(oc get secret -n redhat-ods-applications postgresql -o json | jq -r '.data["database-password"]' | base64 -d)
# URL-encode the password regardless of how it was generated: it may be a
# freshly-generated hex password (already URL-safe) or a pre-existing legacy
# password (e.g. base64-generated, possibly containing /, +, or =) left over
# from before this script used hex. Encoding unconditionally keeps
# DB_CONNECTION_URL parseable in both cases.
ENC_POSTGRES_PASSWORD=$(jq -rn --arg v "$POSTGRES_PASSWORD" '$v|@uri')
oc apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  namespace: redhat-ods-applications
  name: maas-db-config
data:
  DB_CONNECTION_URL: $(printf '%s' "postgresql://maas:${ENC_POSTGRES_PASSWORD}@postgresql:5432/maas" | base64 -w0)
EOF
