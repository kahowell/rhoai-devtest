import base64
import json
import secrets
import subprocess
import time
from importlib.resources import files

_RESOURCE_PACKAGE = "rhoai_devtest.resources"


class ResourceInstallationError(RuntimeError):
    """Raised when a preset resource cannot be installed or becomes ready."""


def _run(command: list[str], *, input_text: str | None = None, verbose: bool = False) -> subprocess.CompletedProcess[str]:
    if verbose:
        print(f"[DEBUG] Running: {' '.join(command)}")
    result = subprocess.run(command, input=input_text, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ResourceInstallationError(f"Command failed ({' '.join(command)}): {detail}")
    return result


def _resource(name: str) -> str:
    return files(_RESOURCE_PACKAGE).joinpath(name).read_text()


def _apply_resource(name: str, *, verbose: bool = False) -> None:
    print(f"Applying resource {name}...")
    _run(["oc", "apply", "-f", "-"], input_text=_resource(name), verbose=verbose)


def _wait_for(command: list[str], description: str, *, timeout: int = 300, verbose: bool = False) -> None:
    print(f"Waiting for {description}...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return
        time.sleep(2)
    raise ResourceInstallationError(f"Timed out waiting for {description}")


def _wait_for_crd(name: str, *, verbose: bool = False) -> None:
    _wait_for(["oc", "get", "crd", name], f"CRD {name}", verbose=verbose)
    _run(["oc", "wait", "--for=condition=Established", f"crd/{name}", "--timeout=300s"], verbose=verbose)


def _wait_for_resource(kind: str, name: str, namespace: str | None = None, *, verbose: bool = False) -> None:
    command = ["oc", "get", kind, name]
    if namespace:
        command.extend(["-n", namespace])
    _wait_for(command, f"{kind}/{name}", verbose=verbose)


def _wait_for_endpoints(service: str, namespace: str, *, verbose: bool = False) -> None:
    _wait_for(["oc", "get", "svc", service, "-n", namespace], f"service {service} in {namespace}", verbose=verbose)
    print(f"Waiting for endpoints for {service} in {namespace}...")
    deadline = time.monotonic() + 300
    command = ["oc", "get", "endpoints", service, "-n", namespace, "-o", "jsonpath={.subsets[*].addresses[*].ip}"]
    while time.monotonic() < deadline:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return
        time.sleep(2)
    raise ResourceInstallationError(f"Timed out waiting for endpoints for {service} in {namespace}")


def _ensure_oauth_secret(*, verbose: bool = False) -> None:
    result = subprocess.run(
        ["oc", "get", "secret", "oauth-proxy-secrets", "-n", "redhat-ods-monitoring"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _run(
            [
                "oc", "create", "secret", "generic", "oauth-proxy-secrets",
                "-n", "redhat-ods-monitoring",
                "--from-literal=session_secret=" + base64.b64encode(secrets.token_bytes(32)).decode(),
            ],
            verbose=verbose,
        )


def _install_gateway(*, verbose: bool = False) -> None:
    domain = _run(
        ["oc", "get", "ingresses.config.openshift.io", "cluster", "-o", "jsonpath={.spec.domain}"],
        verbose=verbose,
    ).stdout.strip()
    cert_name = _run(
        [
            "oc", "get", "ingresscontroller", "default", "-n", "openshift-ingress-operator",
            "-o", "jsonpath={.spec.defaultCertificate.name}",
        ],
        verbose=verbose,
    ).stdout.strip()
    gateway = f"""apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: maas-default-gateway
  namespace: openshift-ingress
  annotations:
    opendatahub.io/managed: \"false\"
    security.opendatahub.io/authorino-tls-bootstrap: \"true\"
spec:
  gatewayClassName: openshift-default
  listeners:
  - name: http
    hostname: maas.{domain}
    port: 80
    protocol: HTTP
    allowedRoutes:
      namespaces:
        from: All
  - name: https
    hostname: maas.{domain}
    port: 443
    protocol: HTTPS
    allowedRoutes:
      namespaces:
        from: All
    tls:
      certificateRefs:
      - group: \"\"
        kind: Secret
        name: {cert_name}
      mode: Terminate
"""
    _run(["oc", "apply", "-f", "-"], input_text=gateway, verbose=verbose)
    _run(["oc", "wait", "-n", "openshift-ingress", "--for=condition=Programmed", "gateway/maas-default-gateway", "--timeout=300s"], verbose=verbose)


def _install_postgres(*, verbose: bool = False) -> None:
    namespace = "redhat-ai-gateway-infra"
    existing = subprocess.run(["oc", "get", "secret", "postgresql", "-n", namespace], capture_output=True, text=True, check=False)
    if existing.returncode != 0:
        password = secrets.token_hex(32)
        _run(
            ["oc", "new-app", "postgresql-ephemeral", "-n", namespace,
             "-p", "POSTGRESQL_DATABASE=maas", "-p", "POSTGRESQL_USER=maas", f"-p", f"POSTGRESQL_PASSWORD={password}"],
            verbose=verbose,
        )
    _wait_for_resource("secret", "postgresql", namespace, verbose=verbose)
    _run(["oc", "rollout", "status", "dc/postgresql", "-n", namespace, "--timeout=300s"], verbose=verbose)
    secret = json.loads(_run(["oc", "get", "secret", "postgresql", "-n", namespace, "-o", "json"], verbose=verbose).stdout)
    password = base64.b64decode(secret["data"]["database-password"]).decode()
    encoded_url = base64.b64encode(f"postgresql://maas:{password}@postgresql:5432/maas".encode()).decode()
    _run(
        ["oc", "apply", "-f", "-"],
        input_text=f"""apiVersion: v1
kind: Secret
metadata:
  namespace: {namespace}
  name: maas-db-config
data:
  DB_CONNECTION_URL: {encoded_url}
""",
        verbose=verbose,
    )


def _configure_authorino(*, verbose: bool = False) -> None:
    _wait_for_resource("service", "authorino-authorino-authorization", "kuadrant-system", verbose=verbose)
    _run(
        ["oc", "annotate", "service", "authorino-authorino-authorization", "-n", "kuadrant-system",
         "service.beta.openshift.io/serving-cert-secret-name=authorino-server-cert", "--overwrite"],
        verbose=verbose,
    )
    _wait_for_resource("authorino", "authorino", "kuadrant-system", verbose=verbose)
    _run(
        ["oc", "patch", "authorino", "authorino", "-n", "kuadrant-system", "--type=merge",
         '-p={"spec":{"listener":{"tls":{"enabled":true,"certSecretRef":{"name":"authorino-server-cert"}}}}}'],
        verbose=verbose,
    )
    _run(
        ["oc", "set", "env", "deployment/authorino", "-n", "kuadrant-system",
         "SSL_CERT_FILE=/etc/ssl/certs/openshift-service-ca/service-ca-bundle.crt",
         "REQUESTS_CA_BUNDLE=/etc/ssl/certs/openshift-service-ca/service-ca-bundle.crt"],
        verbose=verbose,
    )


def _install_common(dsc_resource: str, *, verbose: bool = False, include_gatewayclass: bool = False) -> None:
    if include_gatewayclass:
        _wait_for_crd("gatewayclasses.gateway.networking.k8s.io", verbose=verbose)
        _apply_resource("maas-gatewayclass.yaml", verbose=verbose)

    _wait_for_crd("dscinitializations.dscinitialization.opendatahub.io", verbose=verbose)
    _apply_resource("dsci.yaml", verbose=verbose)
    _run(["oc", "wait", "--for=condition=Ready", "dsci/default-dsci", "--timeout=300s"], verbose=verbose)

    _wait_for_crd("datascienceclusters.datasciencecluster.opendatahub.io", verbose=verbose)
    _apply_resource(dsc_resource, verbose=verbose)
    _run(["oc", "wait", "--for=condition=Ready", "dsc/default-dsc", "--timeout=300s"], verbose=verbose)

    _ensure_oauth_secret(verbose=verbose)
    _apply_resource("lgtm.yaml", verbose=verbose)
    _apply_resource("networkpolicy.yaml", verbose=verbose)


def install_preset_resources(preset: str, *, verbose: bool = False) -> None:
    """Install the workload resources for a selected RHOAI development preset."""
    print(f"Installing preset resources for '{preset}'...")
    if preset not in {"default", "llm-d-o11y", "maas-o11y"}:
        raise ResourceInstallationError(f"Unknown preset: {preset}")

    is_maas = preset == "maas-o11y"
    dsc_resources = {
        "default": "default-dsc.yaml",
        "llm-d-o11y": "llmd-dsc.yaml",
        "maas-o11y": "maas-dsc.yaml",
    }
    _install_common(dsc_resources[preset], verbose=verbose, include_gatewayclass=is_maas)

    if is_maas:
        _wait_for_crd("kuadrants.kuadrant.io", verbose=verbose)
        _apply_resource("maas-kuadrant.yaml", verbose=verbose)
        _configure_authorino(verbose=verbose)
        _install_gateway(verbose=verbose)
        _install_postgres(verbose=verbose)
        _wait_for_crd("gateways.gateway.networking.k8s.io", verbose=verbose)
        _apply_resource("maas-ai-gateway.yaml", verbose=verbose)

    _apply_resource("namespace.yaml", verbose=verbose)

    _wait_for_crd("llminferenceservices.serving.kserve.io", verbose=verbose)
    _wait_for_endpoints("kserve-webhook-server-service", "redhat-ods-applications", verbose=verbose)
    _wait_for_endpoints("llmisvc-webhook-server-service", "redhat-ods-applications", verbose=verbose)
    _apply_resource("maas-llmisvc.yaml" if is_maas else "llmd-llmisvc.yaml", verbose=verbose)

    _wait_for_crd("uiplugins.observability.openshift.io", verbose=verbose)
    _apply_resource("coo-uiplugins.yaml", verbose=verbose)

    if is_maas:
        _wait_for_crd("tenants.maas.opendatahub.io", verbose=verbose)
        _wait_for_endpoints("maas-controller-webhook-service", "redhat-ods-applications", verbose=verbose)
        _apply_resource("maas-tenant.yaml", verbose=verbose)
        _wait_for_crd("maasauthpolicies.maas.opendatahub.io", verbose=verbose)
        _apply_resource("maas-auth-policy.yaml", verbose=verbose)
        _wait_for_crd("maasmodelrefs.maas.opendatahub.io", verbose=verbose)
        _apply_resource("maas-model-ref.yaml", verbose=verbose)
        _wait_for_crd("maassubscriptions.maas.opendatahub.io", verbose=verbose)
        _apply_resource("maas-subscription.yaml", verbose=verbose)

    print(f"Preset '{preset}' resources installed successfully.")
