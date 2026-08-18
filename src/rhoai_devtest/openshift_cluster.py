import json
import os
import subprocess
import sys
import time
from typing import Any

from .auth import ensure_authenticated
from .utils import find_matching_clusters, get_default_match_name, get_latest_rosa_version, get_default_cluster_name, validate_rosa_version


def setup_htpasswd_idp(cluster_name: str | None, verbose: bool = False) -> None:
    if cluster_name is None:
        search_pattern = get_default_match_name()
        print(f"No cluster name specified. Checking for existing cluster matching '{search_pattern}'...")
        matching = find_matching_clusters(search_pattern, verbose=verbose)

        valid_matching = []
        for cname in matching:
            state = _get_cluster_state(cname, verbose=verbose)
            if state == "uninstalling":
                continue
            valid_matching.append(cname)

        if not valid_matching:
            print(f"Error: No existing active clusters found matching '{search_pattern}'.", file=sys.stderr)
            sys.exit(1)

        target_cluster = valid_matching[0]
        print(f"Inferred target cluster: '{target_cluster}'")
    else:
        target_cluster = cluster_name

    # 1. Generate randomized password
    try:
        password_res = subprocess.run(
            ["openssl", "rand", "--base64", "32"],
            capture_output=True,
            text=True,
            check=True
        )
        password = password_res.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"Error: Failed to generate randomized password using openssl: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Configuring htpasswd identity provider for cluster '{target_cluster}'...")

    # 2. Save password locally
    password_dir = os.path.expanduser("~/.kube")
    password_file = os.path.join(password_dir, f"rosa_htpasswd_password_{target_cluster}")
    try:
        os.makedirs(password_dir, exist_ok=True)
        with open(password_file, "w") as f:
            f.write(password)
        # Set file permissions to 600 (read/write by owner only) for security
        os.chmod(password_file, 0o600)
        if verbose:
            print(f"[DEBUG] Saved generated password to '{password_file}'")
    except OSError as e:
        print(f"Warning: Failed to save password to file '{password_file}': {e}", file=sys.stderr)

    # 3. Create IDP command
    create_cmd = [
        "rosa", "create", "idp",
        "-c", target_cluster,
        "--type=htpasswd",
        "--name=htpasswd-idp",
        f"--users=admin:{password}",
        "-y"
    ]
    if verbose:
        safe_cmd = [f"--users=admin:******" if arg.startswith("--users=") else arg for arg in create_cmd]
        print(f"[DEBUG] Running command: {' '.join(safe_cmd)}")

    res = subprocess.run(create_cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        print(f"Error: Failed to create htpasswd identity provider: {res.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    else:
        print("Successfully created htpasswd identity provider.")

    # 4. Grant cluster-admin to admin user
    grant_cmd = [
        "rosa", "grant", "user", "cluster-admin",
        "--user=admin",
        "-c", target_cluster
    ]
    if verbose:
        print(f"[DEBUG] Running command: {' '.join(grant_cmd)}")

    grant_res = subprocess.run(grant_cmd, capture_output=True, text=True, check=False)
    if grant_res.returncode != 0:
        stderr_msg = grant_res.stderr.strip()
        stdout_msg = grant_res.stdout.strip()
        if "already" in stderr_msg.lower() or "already" in stdout_msg.lower():
            if verbose:
                print(f"[DEBUG] User 'admin' already has cluster-admin access: {stderr_msg or stdout_msg}")
        else:
            print(f"Error: Failed to grant cluster-admin access to 'admin' user:\n{stderr_msg}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Successfully granted cluster-admin access to user 'admin' on cluster '{target_cluster}'.")


def _get_cluster_state(name: str, verbose: bool = False) -> str | None:
    desc_cmd = ["rosa", "describe", "cluster", "-c", name, "-o", "json"]
    if verbose:
        print(f"[DEBUG] Running command: {' '.join(desc_cmd)}")

    desc_res = subprocess.run(desc_cmd, capture_output=True, text=True, check=False)
    if desc_res.returncode != 0:
        return None

    try:
        cluster_info = json.loads(desc_res.stdout)
    except json.JSONDecodeError:
        return None

    state = cluster_info.get("state")
    if not state:
        state = cluster_info.get("status", {}).get("state")
    return state


def _get_cluster_version(name: str, verbose: bool = False) -> str | None:
    desc_cmd = ["rosa", "describe", "cluster", "-c", name, "-o", "json"]
    if verbose:
        print(f"[DEBUG] Running command: {' '.join(desc_cmd)}")

    desc_res = subprocess.run(desc_cmd, capture_output=True, text=True, check=False)
    if desc_res.returncode != 0:
        return None

    try:
        cluster_info = json.loads(desc_res.stdout)
    except json.JSONDecodeError:
        return None

    version_info = cluster_info.get("version")
    if isinstance(version_info, dict):
        cv = version_info.get("raw_id") or version_info.get("id")
        if cv:
            return cv.removeprefix("openshift-v")
    elif isinstance(version_info, str):
        return version_info.removeprefix("openshift-v")
    return None


def create_openshift_cluster(
    name: str | None,
    machine_type: str,
    version: str | None,
    config: dict[str, Any],
    verbose: bool = False,
    replicas: str | None = None,
) -> str | None:
    ensure_authenticated(verbose=verbose)

    if version:
        validate_rosa_version(version, verbose=verbose)

    # Determine if we need to use an existing cluster or create a new one.
    if name is None:
        # --name was not specified. We must use an existing cluster.
        search_pattern = get_default_match_name()
        print(f"Checking for existing cluster matching '{search_pattern}'...")
        matching = find_matching_clusters(search_pattern, verbose=verbose)

        valid_matching = []
        for cname in matching:
            state = _get_cluster_state(cname, verbose=verbose)
            if state == "uninstalling":
                print(f"Ignoring cluster '{cname}' because it is in 'uninstalling' state.")
                continue
            valid_matching.append(cname)

        if valid_matching:
            target_cluster = valid_matching[0]
            print(f"Using existing cluster '{target_cluster}'.")

            if version:
                existing_version = _get_cluster_version(target_cluster, verbose=verbose)
                if existing_version:
                    clean_requested = version.removeprefix("openshift-v")
                    clean_existing = existing_version.removeprefix("openshift-v")
                    if clean_requested != clean_existing:
                        print(f"Error: Requested OpenShift version '{version}' does not match existing cluster '{target_cluster}' version '{existing_version}'.", file=sys.stderr)
                        sys.exit(1)
                else:
                    print(f"Warning: Could not retrieve OpenShift version of existing cluster '{target_cluster}' for comparison.")

            return target_cluster

        # No existing cluster found. Fall back to generating a default cluster name to create a new one.
        name = get_default_cluster_name()
        print(f"No existing cluster found matching '{search_pattern}' (excluding 'uninstalling' clusters).")
        print(f"Proceeding to provision a new cluster with default name '{name}'...")

    # If name is specified, check if it already exists.
    print(f"Checking if cluster '{name}' already exists...")
    matching = find_matching_clusters(name, verbose=verbose)
    if name in matching:
        state = _get_cluster_state(name, verbose=verbose)
        if state == "uninstalling":
            print(f"Cluster '{name}' already exists but is in 'uninstalling' state. Ignoring existing cluster.")
        else:
            print(f"Cluster '{name}' already exists. Reusing existing cluster.")

            if version:
                existing_version = _get_cluster_version(name, verbose=verbose)
                if existing_version:
                    clean_requested = version.removeprefix("openshift-v")
                    clean_existing = existing_version.removeprefix("openshift-v")
                    if clean_requested != clean_existing:
                        print(f"Error: Requested OpenShift version '{version}' does not match existing cluster '{name}' version '{existing_version}'.", file=sys.stderr)
                        sys.exit(1)
                else:
                    print(f"Warning: Could not retrieve OpenShift version of existing cluster '{name}' for comparison.")

            return name

    if not version:
        version = get_latest_rosa_version(verbose=verbose)

    oidc_config_id = config.get("oidc_config_id")
    installer_role = config.get("installer_role")
    support_role = config.get("support_role")
    worker_role = config.get("worker_role")
    subnet_pairs = config.get("subnet_pairs", [])

    if not all([oidc_config_id, installer_role, support_role, worker_role, subnet_pairs]):
        print("Missing required infrastructure parameters in configuration file.", file=sys.stderr)
        sys.exit(1)

    replicas_str = f" and replicas '{replicas}'" if replicas else ""
    print(f"Starting cluster provisioning for '{name}' with machine type '{machine_type}'{replicas_str} and OpenShift version '{version}'")

    for pair in subnet_pairs:
        print(f"Trying subnet pair: {pair}")
        cmd = [
            "rosa", "create", "cluster",
            "--sts",
            f"--oidc-config-id={oidc_config_id}",
            f"--cluster-name={name}",
            "--mode=auto",
            "--hosted-cp",
            f"--subnet-ids={pair}",
            f"--compute-machine-type={machine_type}",
            f"--role-arn={installer_role}",
            f"--support-role-arn={support_role}",
            f"--worker-iam-role={worker_role}",
            f"--version={version}"
        ]
        if replicas is not None:
            cmd.append(f"--replicas={replicas}")
        if verbose:
            print(f"[DEBUG] Running command: {' '.join(cmd)}")

        res = subprocess.run(cmd, check=False)
        if res.returncode == 0:
            print(f"Successfully initiated cluster creation for '{name}' on subnet pair '{pair}'!")

            print(f"Polling for cluster '{name}' readiness...")
            while True:
                desc_cmd = ["rosa", "describe", "cluster", "-c", name, "-o", "json"]
                if verbose:
                    print(f"[DEBUG] Running command: {' '.join(desc_cmd)}")

                desc_res = subprocess.run(desc_cmd, capture_output=True, text=True, check=False)
                if desc_res.returncode != 0:
                    print(f"Warning: Failed to describe cluster '{name}'. Retrying in 30 seconds...")
                    time.sleep(30)
                    continue

                try:
                    cluster_info = json.loads(desc_res.stdout)
                except json.JSONDecodeError as e:
                    print(f"Warning: Failed to parse cluster description JSON: {e}. Retrying in 30 seconds...")
                    time.sleep(30)
                    continue

                state = cluster_info.get("state")
                if not state:
                    state = cluster_info.get("status", {}).get("state")

                if not state:
                    print("Warning: Cluster state not found in response. Retrying in 30 seconds...")
                    time.sleep(30)
                    continue

                print(f"Cluster '{name}' state: '{state}'")
                if state == "ready":
                    print(f"Cluster '{name}' is ready!")
                    setup_htpasswd_idp(name, verbose=verbose)
                    return name
                elif state in ("error", "failed", "uninstalling"):
                    print(f"Cluster '{name}' entered a failed state: '{state}'.", file=sys.stderr)
                    return None

                time.sleep(30)
        else:
            print(f"Failed with subnet pair: {pair}, trying next...")

    print("All subnet pairs failed. Cluster provisioning could not be initiated.", file=sys.stderr)
    return None
