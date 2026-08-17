import json
import subprocess
import sys
import time
from typing import Any

from .auth import ensure_authenticated
from .utils import find_matching_clusters, get_default_match_name, get_latest_rosa_version, get_default_cluster_name


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


def create_openshift_cluster(
    name: str | None,
    machine_type: str,
    version: str | None,
    config: dict[str, Any],
    verbose: bool = False,
    replicas: str | None = None,
) -> str | None:
    ensure_authenticated(verbose=verbose)

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
                    return name
                elif state in ("error", "failed", "uninstalling"):
                    print(f"Cluster '{name}' entered a failed state: '{state}'.", file=sys.stderr)
                    return None

                time.sleep(30)
        else:
            print(f"Failed with subnet pair: {pair}, trying next...")

    print("All subnet pairs failed. Cluster provisioning could not be initiated.", file=sys.stderr)
    return None
