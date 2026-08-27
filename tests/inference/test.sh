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

wait_for_crd "gatewayclasses.gateway.networking.k8s.io"
oc apply -f gatewayclass.yaml

wait_for_crd "dscinitializations.dscinitialization.opendatahub.io"
oc apply -f dsci.yaml

echo "Waiting for DSCI default-dsci to be Ready..."
until oc get dsci default-dsci &>/dev/null; do
  sleep 2
done
oc wait --for=condition=Ready dsci/default-dsci --timeout=300s

wait_for_crd "kuadrants.kuadrant.io"
oc apply -f kuadrant.yaml

echo "Waiting for rhods-operator deployment in redhat-ods-operator namespace..."
oc wait --for=condition=Available deployment/rhods-operator -n redhat-ods-operator --timeout=300s

wait_for_endpoints "rhods-operator-service" "redhat-ods-operator"

wait_for_crd "datascienceclusters.datasciencecluster.opendatahub.io"
oc apply -f dsc.yaml

echo "Waiting for DSC default-dsc to be Ready..."
until oc get dsc default-dsc &>/dev/null; do
  sleep 2
done
oc wait --for=condition=Ready dsc/default-dsc --timeout=300s

if ! oc get secret oauth-proxy-secrets -n redhat-ods-monitoring &>/dev/null; then
  oc create secret generic oauth-proxy-secrets -n redhat-ods-monitoring --from-literal=session_secret="$(openssl rand -base64 32)"
fi
oc apply -f lgtm.yaml

# hack: networkpolicy to allow otlp from all ns
oc apply -f networkpolicy.yaml

./authorino.sh
./maas-gateway.sh
./postgres.sh
wait_for_crd "gateways.gateway.networking.k8s.io"
oc apply -f ai-gateway.yaml

# Custom resource definitions (CRDs) are waited for via wait_for_crd

wait_for_crd "llminferenceservices.serving.kserve.io"
wait_for_endpoints "kserve-webhook-server-service" "redhat-ods-applications"
wait_for_endpoints "llmisvc-webhook-server-service" "redhat-ods-applications"
oc apply -f llmisvc.yaml

wait_for_crd "uiplugins.observability.openshift.io"
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

echo "Waiting for route lgtm in redhat-ods-monitoring namespace..."
oc wait --for=jsonpath='{.spec.host}' route/lgtm -n redhat-ods-monitoring --timeout=300s
LGTM_HOST=$(oc get route lgtm -n redhat-ods-monitoring -o jsonpath='{.spec.host}')
echo "LGTM URL: https://${LGTM_HOST}"

./completions.sh
