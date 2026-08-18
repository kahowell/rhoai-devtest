import json
import os
import re
import subprocess
import sys
import html
import time
from typing import Any
import urllib.parse

import requests

from .auth import ensure_authenticated
from .openshift_cluster import create_openshift_cluster
from .rhoai import install_rhoai
from .rhoai_nightly import install_rhoai_nightly
from .utils import (
    _apply_yaml,
    generate_operator_yaml,
)


def install_operators(target_cluster: str, verbose: bool = False):
    """
    Install required operators on the OpenShift cluster.
    """
    print(f"\nInstalling required operators on cluster '{target_cluster}'...")

    operators_config = [
        {
            "operator": "openshift-cert-manager-operator",
            "namespace": "cert-manager-operator",
            "channel": "stable-v1",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
        },
        {
            "operator": "cluster-observability-operator",
            "namespace": "openshift-cluster-observability-operator",
            "channel": "stable",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
        },
        {
            "operator": "job-set",
            "namespace": "openshift-jobset-operator",
            "channel": "stable-v1.0",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
            "target_namespaces": ["openshift-jobset-operator"],
        },
        {
            "operator": "kueue-operator",
            "namespace": "openshift-kueue-operator",
            "channel": "stable-v1.4",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
        },
        {
            "operator": "leader-worker-set",
            "namespace": "openshift-lws-operator",
            "channel": "stable-v1.0",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
            "target_namespaces": ["openshift-lws-operator"],
        },
        {
            "operator": "mariadb-operator",
            "namespace": "mariadb-operator",
            "channel": "alpha",
            "source": "community-operators",
            "source_namespace": "openshift-marketplace",
            "install_plan_approval": "Manual",
            "starting_csv": "mariadb-operator.v0.29.0",
        },
        {
            "operator": "nfd",
            "namespace": "openshift-nfd",
            "channel": "stable",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
            "target_namespaces": ["openshift-nfd"],
        },
        {
            "operator": "gpu-operator-certified",
            "namespace": "nvidia-gpu-operator",
            "channel": "v25.10",
            "source": "certified-operators",
            "source_namespace": "openshift-marketplace",
            "target_namespaces": ["nvidia-gpu-operator"],
        },
        {
            "operator": "openshift-custom-metrics-autoscaler-operator",
            "namespace": "openshift-keda",
            "channel": "stable",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
        },
        {
            "operator": "opentelemetry-product",
            "namespace": "openshift-opentelemetry-operator",
            "channel": "stable",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
        },
        {
            "operator": "rhcl-operator",
            "namespace": "kuadrant-system",
            "channel": "stable",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
        },
        {
            "operator": "tempo-product",
            "namespace": "openshift-tempo-operator",
            "channel": "stable",
            "source": "redhat-operators",
            "source_namespace": "openshift-marketplace",
        },
    ]

    yaml_blocks = []
    for op in operators_config:
        if verbose:
            print(f"[DEBUG] Preparing YAML for operator '{op['operator']}'...")
        op_yaml = generate_operator_yaml(
            namespace=op["namespace"],
            operator=op["operator"],
            channel=op.get("channel", "stable"),
            source=op.get("source", "redhat-operators"),
            source_namespace=op.get("source_namespace", "openshift-marketplace"),
            install_plan_approval=op.get("install_plan_approval", "Automatic"),
            starting_csv=op.get("starting_csv"),
            target_namespaces=op.get("target_namespaces"),
        )
        yaml_blocks.append(op_yaml)

    # Combine all manifests separated by "---"
    combined_yaml = "\n---\n".join(yaml_blocks)

    if verbose:
        print("[DEBUG] Applying all operator manifests in a single apply call...")
    if not _apply_yaml(combined_yaml, verbose=verbose):
        print("Error: Failed to install prerequisite operators.", file=sys.stderr)
        sys.exit(1)

    print("Operators installation completed successfully.")


def _login_via_web_form(api_url: str, password: str, verbose: bool = False) -> str | None:
    """
    Perform web-form authentication to OpenShift OAuth server,
    extract the sha256 session token, and return it.
    """
    oauth_url = api_url.replace("api.", "oauth.").rstrip("/")
    parsed_oauth = urllib.parse.urlparse(oauth_url)
    hostname = parsed_oauth.hostname
    if not hostname:
        print(f"Error: Could not parse hostname from oauth_url: {oauth_url}", file=sys.stderr)
        return None

    oauth_redirect = f"https://{hostname}:443/oauth/token/display"

    direct_authorize_url = (
        f"https://{hostname}/oauth/authorize"
        f"?client_id=openshift-browser-client"
        f"&idp=htpasswd-idp"
        f"&response_type=code"
        f"&redirect_uri={oauth_redirect}"
    )

    start_time = time.time()
    timeout = 300  # 5 minutes
    interval = 15  # seconds
    attempt = 1

    while True:
        try:
            if verbose:
                print(f"[DEBUG] Web-form login attempt #{attempt}...")
            print(f"Requesting direct IDP authorization URL: {direct_authorize_url}")

            # 1. Prepare requests Session and Headers
            session = requests.Session()
            headers: dict[str, str] = {}

            # 2. Perform GET to direct_authorize_url to load the form and follow redirects
            response = session.get(direct_authorize_url, headers=headers, timeout=30)
            response.raise_for_status()
            html_content = response.text
            final_url = response.url

            if verbose:
                print(f"[DEBUG] Redirected final login form URL: {final_url}")

            # 3. Extract csrf and then tokens
            csrf_match = (
                re.search(r'name=["\']csrf["\'][\s\S]*?value=["\']([^"\']+)["\']', html_content) or
                re.search(r'value=["\']([^"\']+)["\'][\s\S]*?name=["\']csrf["\']', html_content)
            )
            then_match = (
                re.search(r'name=["\']then["\'][\s\S]*?value=["\']([^"\']+)["\']', html_content) or
                re.search(r'value=["\']([^"\']+)["\'][\s\S]*?name=["\']then["\']', html_content)
            )

            if not csrf_match:
                raise ValueError("CSRF token not found in form HTML.")
            if not then_match:
                raise ValueError("'then' token not found in form HTML.")

            csrf_token = csrf_match.group(1)
            then_val = html.unescape(then_match.group(1))

            print("Extracted login token and CSRF metadata successfully.")

            if verbose:
                print(f"[DEBUG] Extracted CSRF token: {csrf_token[:10]}...")
                print(f"[DEBUG] Extracted then token: {then_val[:10]}...")

            # 4. Resolve the form action URL relative to final_url
            action_match = re.search(r'<form\s+[^>]*action=["\']([^"\']+)["\']', html_content)
            action = action_match.group(1) if action_match else "/login"
            login_url = urllib.parse.urljoin(final_url, action)

            if verbose:
                print(f"[DEBUG] Submitting form to: {login_url}")

            # 5. Submit POST to login_url
            payload = {
                "username": "admin",
                "password": password,
                "csrf": csrf_token,
                "then": then_val
            }

            print(f"Submitting credentials POST to: {login_url}")
            if verbose:
                safe_payload = {k: "******" if k == "password" else v for k, v in payload.items()}
                print(f"[DEBUG] POST Payload: {safe_payload}")

            post_response = session.post(login_url, data=payload, headers=headers, timeout=30)
            post_response.raise_for_status()
            post_html = post_response.text

            if verbose:
                print("[DEBUG] Processing intermediate login page...")

            # Extract csrf and code values from the intermediate login page
            csrf_match_2 = (
                re.search(r'name=["\']csrf["\'][\s\S]*?value=["\']([^"\']+)["\']', post_html) or
                re.search(r'value=["\']([^"\']+)["\'][\s\S]*?name=["\']csrf["\']', post_html)
            )
            code_match = (
                re.search(r'name=["\']code["\'][\s\S]*?value=["\']([^"\']+)["\']', post_html) or
                re.search(r'value=["\']([^"\']+)["\'][\s\S]*?name=["\']code["\']', post_html)
            )

            if not csrf_match_2 or not code_match:
                raise ValueError("Could not extract intermediate csrf or code tokens.")

            csrf_token_2 = csrf_match_2.group(1)
            code_val = html.unescape(code_match.group(1))

            # Resolve the secondary form action URL relative to the post_response URL
            action_match_2 = re.search(r'<form\s+[^>]*action=["\']([^"\']+)["\']', post_html)
            action_2 = action_match_2.group(1) if action_match_2 else "/oauth/authorize"
            approval_url = urllib.parse.urljoin(post_response.url, action_2)

            approval_payload = {
                "csrf": csrf_token_2,
                "code": code_val
            }

            # If this is the approval page, we must supply the decision="allow" parameter.
            # If it's already authorized, the page is /oauth/token/display which throws 500 if "decision" is present.
            if "decision" in post_html or "/authorize" in approval_url:
                approval_payload["decision"] = "allow"
                if verbose:
                    print("[DEBUG] Approval/decision required. Appending 'decision': 'allow' to payload.")

            print(f"Submitting confirmation POST to: {approval_url}")
            final_response = session.post(approval_url, data=approval_payload, headers=headers, timeout=30)
            final_response.raise_for_status()
            final_html = final_response.text

            if verbose:
                print("--- Post-Login HTML start ---")
                print(final_html)
                print("--- Post-Login HTML end ---")

            # 6. Extract token starting with sha256~
            token_match = re.search(r'sha256~[A-Za-z0-9_-]+', final_html)
            if not token_match:
                raise ValueError("OpenShift session token ('sha256~...') not found in post-login response HTML.")

            token = token_match.group(0)
            print("Successfully authenticated and extracted session token.")
            if verbose:
                print(f"[DEBUG] Successfully extracted OpenShift login token: {token[:15]}...")
            return token

        except Exception as e:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                print(f"Error: Web-form login failed after retrying for {int(elapsed)} seconds: {e}", file=sys.stderr)
                return None

            print(f"Warning: Web-form login attempt #{attempt} failed: {e}. Retrying in {interval} seconds... (Elapsed: {int(elapsed)}s/{timeout}s)", file=sys.stderr)
            time.sleep(interval)
            attempt += 1


def ensure_kubeconfig_setup(target_cluster: str, verbose: bool = False) -> str | None:
    """
    Ensure the local kubeconfig is configured for the target cluster.
    If not, prompt the user to log in.
    """
    if verbose:
        print(f"[DEBUG] Describing cluster '{target_cluster}' to find API URL...")

    # 1. Use command like `rosa describe cluster --cluster=khowell20260812 -o json` -> .api.url
    res = subprocess.run(
        ["rosa", "describe", "cluster", f"--cluster={target_cluster}", "-o", "json"],
        capture_output=True,
        text=True,
        check=False
    )
    if res.returncode != 0:
        print(f"Error: Failed to describe cluster '{target_cluster}': {res.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    try:
        cluster_info = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON output from 'rosa describe cluster': {e}", file=sys.stderr)
        sys.exit(1)

    api_url = cluster_info.get("api", {}).get("url")
    if not api_url:
        print(f"Error: Could not retrieve API URL for cluster '{target_cluster}' from description.", file=sys.stderr)
        sys.exit(1)

    console_url = cluster_info.get("console", {}).get("url")

    if verbose:
        print(f"[DEBUG] Target cluster API URL: {api_url}")
        if console_url:
            print(f"[DEBUG] Target cluster Console URL: {console_url}")

    # 2. Open kubeconfig (~/.kube/config). Grep for the url. If absent, then we need to login.
    kubeconfig_path = os.path.expanduser("~/.kube/config")
    login_required = True
    if os.path.exists(kubeconfig_path):
        try:
            with open(kubeconfig_path, "r") as f:
                content = f.read()
                if api_url in content:
                    login_required = False
                    if verbose:
                        print(f"[DEBUG] Found API URL '{api_url}' in '{kubeconfig_path}'. Skipping login.")
        except OSError as e:
            if verbose:
                print(f"[DEBUG] Error reading kubeconfig '{kubeconfig_path}': {e}")

    if login_required:
        print(f"Local kubeconfig is not configured for cluster API '{api_url}'. Initiating login...")

        # Check if we have a saved htpasswd password for cluster-admin
        password_file = os.path.expanduser(f"~/.kube/rosa_htpasswd_password_{target_cluster}")
        password = None
        if os.path.exists(password_file):
            try:
                with open(password_file, "r") as f:
                    password = f.read().strip()
            except OSError as e:
                if verbose:
                    print(f"[DEBUG] Error reading password file '{password_file}': {e}")

        if password:
            print(f"Attempting programmatic web-form login to {api_url} as 'admin'...")
            token = _login_via_web_form(api_url, password, verbose=verbose)
            if token:
                print(f"Logging in to {api_url} with extracted session token...")
                login_res = subprocess.run(
                    ["oc", "login", f"--token={token}", f"--server={api_url}"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if login_res.returncode == 0:
                    print("Successfully authenticated and configured local kubeconfig.")
                    return console_url
                else:
                    print(f"Warning: OpenShift login with extracted token failed: {login_res.stderr.strip() or login_res.stdout.strip()}", file=sys.stderr)
            else:
                print("Warning: Failed to extract session token via programmatic login.", file=sys.stderr)

            print("Falling back to manual token login...")

        # 3. To login, use `xdg-open` against oauth url (derive oauth url -> api_url.replace('api.', 'oauth.') + /oauth/token/request).
        oauth_url = api_url.replace("api.", "oauth.")
        oauth_url = oauth_url.rstrip("/") + "/oauth/token/request"

        print(f"Opening OAuth URL to request a login token:\n  {oauth_url}\n")
        try:
            subprocess.run(["xdg-open", oauth_url], check=False)
        except OSError as e:
            if verbose:
                print(f"[DEBUG] Failed to run xdg-open: {e}")

        # 4. ask user for token, use in oc login --token=$token --server=$api_url command.
        try:
            token = input("Please enter your OpenShift login token: ").strip()
        except KeyboardInterrupt:
            print("\nOperation cancelled by user.", file=sys.stderr)
            sys.exit(1)

        if not token:
            print("Error: OpenShift login token cannot be empty.", file=sys.stderr)
            sys.exit(1)

        print(f"Logging in to {api_url}...")
        login_res = subprocess.run(
            ["oc", "login", f"--token={token}", f"--server={api_url}"],
            capture_output=True,
            text=True,
            check=False
        )
        if login_res.returncode != 0:
            print(f"Error: OpenShift login failed:\n{login_res.stderr.strip()}", file=sys.stderr)
            sys.exit(1)

        print("Successfully authenticated and configured local kubeconfig.")
    else:
        print(f"Kubeconfig is already configured for cluster API '{api_url}'.")

    return console_url


def handle_rhoai_cluster(
    name: str | None,
    machine_type: str,
    version: str | None,
    config: dict[str, Any],
    verbose: bool = False,
    rhoai_version: str | None = None,
    replicas: str | None = None,
):
    ensure_authenticated(verbose=verbose)

    target_cluster = create_openshift_cluster(
        name=name,
        machine_type=machine_type,
        version=version,
        config=config,
        verbose=verbose,
        replicas=replicas,
    )
    if not target_cluster:
        print("Error: Failed to obtain an OpenShift cluster.", file=sys.stderr)
        sys.exit(1)

    # Ensure cluster admin access to the user on target_cluster
    username = os.environ.get("USER")
    if not username:
        print("Error: USER environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Ensuring cluster-admin access for user '{username}' on cluster '{target_cluster}'...")
    grant_cmd = [
        "rosa", "grant", "user", "cluster-admin",
        f"--user={username}",
        f"--cluster={target_cluster}"
    ]
    if verbose:
        print(f"[DEBUG] Running command: {' '.join(grant_cmd)}")

    grant_res = subprocess.run(grant_cmd, capture_output=True, text=True, check=False)
    if grant_res.returncode != 0:
        stderr_msg = grant_res.stderr.strip()
        stdout_msg = grant_res.stdout.strip()
        if "already" in stderr_msg.lower() or "already" in stdout_msg.lower():
            if verbose:
                print(f"[DEBUG] User '{username}' already has cluster-admin access: {stderr_msg or stdout_msg}")
        else:
            print(f"Error: Failed to grant cluster-admin access to user '{username}':\n{stderr_msg}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Successfully granted cluster-admin access to user '{username}' on cluster '{target_cluster}'.")

    # Ensure local kubeconfig is set up for target_cluster
    console_url = ensure_kubeconfig_setup(target_cluster, verbose=verbose)

    # Install required operators prior to RHOAI deployment
    install_operators(target_cluster, verbose=verbose)

    # Perform installation based on the rhoai_version option
    if rhoai_version is not None:
        if "quay" in rhoai_version.lower() or "/" in rhoai_version:
            # quay image reference triggers nightly installation
            install_rhoai_nightly(target_cluster, rhoai_version, verbose=verbose)
        else:
            # non-quay image adjusts the subscription object to select a specific RHOAI version
            install_rhoai(target_cluster, version=rhoai_version, verbose=verbose)
    else:
        # standard installation with default version
        install_rhoai(target_cluster, version=None, verbose=verbose)

    if console_url:
        print(f"\nOpenShift Console URL: {console_url}")
