BASE=$(dirname $0)
cd $BASE

# Ensure scripts have executable permissions
chmod +x authorino.sh maas-gateway.sh postgres.sh completions.sh

# Helper function to wait for service endpoints to be populated
wait_for_endpoints() {
  local svc_name=$1
  local ns=$2
  echo "Waiting for endpoints of service $svc_name in namespace $ns..."
  until oc get svc -n "$ns" "$svc_name" &>/dev/null; do
    sleep 2
  done
  until [ -n "$(oc get endpoints -n "$ns" "$svc_name" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null)" ]; do
    sleep 2
  done
  echo "Endpoints for $svc_name are ready!"
}

oc apply -f gatewayclass.yaml
oc apply -f dsci.yaml

echo "Waiting for DSCI default-dsci to be Ready..."
until oc get dsci default-dsci &>/dev/null; do
  sleep 2
done
oc wait --for=condition=Ready dsci/default-dsci --timeout=300s

oc apply -f kuadrant.yaml

echo "Waiting for rhods-operator deployment in redhat-ods-operator namespace..."
oc wait --for=condition=Available deployment/rhods-operator -n redhat-ods-operator --timeout=300s

wait_for_endpoints "rhods-operator-service" "redhat-ods-operator"

oc apply -f dsc.yaml

echo "Waiting for DSC default-dsc to be Ready..."
until oc get dsc default-dsc &>/dev/null; do
  sleep 2
done
oc wait --for=condition=Ready dsc/default-dsc --timeout=300s

oc apply -f lgtm.yaml

# hack: networkpolicy to allow otlp from all ns
oc apply -f networkpolicy.yaml

echo "Waiting for service/authorino-authorino-authorization in kuadrant-system namespace..."
until oc get service/authorino-authorino-authorization -n kuadrant-system &>/dev/null; do
  sleep 2
done

echo "Waiting for deployment/authorino in kuadrant-system namespace..."
until oc get deployment/authorino -n kuadrant-system &>/dev/null; do
  sleep 2
done
oc wait --for=condition=Available deployment/authorino -n kuadrant-system --timeout=300s

./authorino.sh
./maas-gateway.sh
./postgres.sh
oc apply -f ai-gateway.yaml

# Wait for custom resource definitions (CRDs) to be established before applying custom resources
wait_for_crd() {
  local crd_name=$1
  echo "Waiting for CRD $crd_name to be created..."
  until oc get crd "$crd_name" &>/dev/null; do
    sleep 2
  done
  echo "Waiting for CRD $crd_name to be established..."
  oc wait --for=condition=Established crd/"$crd_name" --timeout=300s
}

wait_for_crd "llminferenceservices.serving.kserve.io"
wait_for_endpoints "kserve-webhook-server-service" "redhat-ods-applications"
wait_for_endpoints "llmisvc-webhook-server-service" "redhat-ods-applications"
oc apply -f llmisvc.yaml

oc apply -f coo-uiplugins.yaml

wait_for_crd "tenants.maas.opendatahub.io"
wait_for_endpoints "maas-controller-webhook-service" "redhat-ods-applications"
oc apply -f maastenant.yaml

wait_for_crd "maasauthpolicies.maas.opendatahub.io"
oc apply -f maasauthpolicy.yaml

wait_for_crd "maasmodelrefs.maas.opendatahub.io"
oc apply -f maasmodelref.yaml

wait_for_crd "maassubscriptions.maas.opendatahub.io"
oc apply -f maassubscription.yaml

./completions.sh
