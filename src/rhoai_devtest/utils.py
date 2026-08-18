import getpass
import json
import subprocess
import sys
from datetime import datetime


def parse_version(v_str: str) -> tuple[int, ...]:
    v_str = v_str.removeprefix("openshift-v")
    parts = []
    for part in v_str.split('.'):
        digits = []
        for char in part:
            if char.isdigit():
                digits.append(char)
            else:
                break
        if digits:
            parts.append(int("".join(digits)))
        else:
            parts.append(0)
    return tuple(parts)


def get_latest_rosa_version(verbose: bool = False) -> str:
    print("Querying latest available OpenShift/ROSA version...")
    try:
        result = subprocess.run(
            ["rosa", "list", "versions", "-o", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        versions_data = json.loads(result.stdout)
        versions = []
        for v in versions_data:
            if isinstance(v, dict) and "id" in v:
                versions.append(v["id"])

        if not versions:
            raise ValueError("No versions found in rosa list versions output")

        sorted_versions = sorted(versions, key=parse_version)
        latest_version = sorted_versions[-1]
        latest_version = latest_version.removeprefix("openshift-v")

        if verbose:
            print(f"[DEBUG] Selected latest ROSA/OpenShift version: {latest_version}")
        return latest_version
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as e:
        print(f"Error querying ROSA versions: {e}", file=sys.stderr)
        sys.exit(1)


def validate_rosa_version(version: str, verbose: bool = False) -> None:
    print(f"Validating OpenShift version '{version}' against available ROSA versions...")
    try:
        result = subprocess.run(
            ["rosa", "list", "versions", "-o", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        versions_data = json.loads(result.stdout)
        available = set()
        for v in versions_data:
            if isinstance(v, dict) and "id" in v:
                raw_id = v["id"]
                available.add(raw_id)
                available.add(raw_id.removeprefix("openshift-v"))

        if version not in available:
            print(f"Error: OpenShift version '{version}' is not available in ROSA.", file=sys.stderr)
            print("Available ROSA versions:", file=sys.stderr)
            clean_versions = sorted({v.removeprefix("openshift-v") for v in available}, key=parse_version)
            for cv in clean_versions:
                print(f"  - {cv}", file=sys.stderr)
            sys.exit(1)

        if verbose:
            print(f"[DEBUG] OpenShift version '{version}' is valid.")
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as e:
        print(f"Error querying ROSA versions for validation: {e}", file=sys.stderr)
        sys.exit(1)


def find_matching_clusters(pattern: str, verbose: bool = False) -> list[str]:
    try:
        res = subprocess.run(["rosa", "list", "cluster", "-o", "json"], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            if verbose:
                print(f"[DEBUG] rosa list cluster failed: {res.stderr}")
            return []

        clusters_data = json.loads(res.stdout)
        matching = []
        for c in clusters_data:
            if isinstance(c, dict) and "name" in c:
                name = c["name"]
                if pattern in name:
                    matching.append(name)
        return matching
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        if verbose:
            print(f"[DEBUG] Error listing clusters: {e}")
        return []


def get_default_cluster_name() -> str:
    username = getpass.getuser()
    date_str = datetime.now().astimezone().strftime("%Y%m%d")
    return f"{username}{date_str}"


def get_default_match_name() -> str:
    return getpass.getuser()


def _apply_yaml(yaml_content: str, verbose: bool = False) -> bool:
    try:
        res = subprocess.run(
            ["oc", "apply", "-f", "-"],
            input=yaml_content,
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode != 0:
            if verbose:
                print(f"[DEBUG] Failed to apply YAML:\n{yaml_content}\nError: {res.stderr}")
            return False
        if verbose:
            print(f"[DEBUG] Applied YAML successfully:\n{res.stdout}")
        return True
    except FileNotFoundError:
        print("Error: 'oc' command-line tool is not installed or not in PATH.", file=sys.stderr)
        sys.exit(1)


def generate_operator_yaml(
    namespace: str,
    operator: str,
    channel: str = "stable",
    source: str = "redhat-operators",
    source_namespace: str = "openshift-marketplace",
    install_plan_approval: str = "Automatic",
    starting_csv: str | None = None,
    target_namespaces: list[str] | None = None,
) -> str:
    """
    Generate the YAML manifest (Namespace, OperatorGroup, Subscription) for an operator.
    """
    # 1. Create Namespace
    namespace_yaml = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
"""

    # 2. Create OperatorGroup
    if target_namespaces is not None:
        target_ns_list = "\n".join(f"    - {ns}" for ns in target_namespaces)
        spec_content = f"spec:\n  targetNamespaces:\n{target_ns_list}"
    else:
        spec_content = "spec:\n  upgradeStrategy: Default"

    operator_group_yaml = f"""apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: {operator}
  namespace: {namespace}
{spec_content}
"""

    # 3. Create Subscription
    starting_csv_line = f"\n  startingCSV: {starting_csv}" if starting_csv else ""
    subscription_yaml = f"""apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: {operator}
  namespace: {namespace}
spec:
  channel: {channel}
  installPlanApproval: {install_plan_approval}
  name: {operator}
  source: {source}
  sourceNamespace: {source_namespace}{starting_csv_line}
"""

    return f"{namespace_yaml.strip()}\n---\n{operator_group_yaml.strip()}\n---\n{subscription_yaml.strip()}"


def install_operator(
    target_cluster: str,
    namespace: str,
    operator: str,
    channel: str = "stable",
    source: str = "redhat-operators",
    source_namespace: str = "openshift-marketplace",
    install_plan_approval: str = "Automatic",
    starting_csv: str | None = None,
    target_namespaces: list[str] | None = None,
    verbose: bool = False,
):
    print(f"Installing operator '{operator}' into namespace '{namespace}' on cluster '{target_cluster}'...")

    yaml_content = generate_operator_yaml(
        namespace=namespace,
        operator=operator,
        channel=channel,
        source=source,
        source_namespace=source_namespace,
        install_plan_approval=install_plan_approval,
        starting_csv=starting_csv,
        target_namespaces=target_namespaces,
    )

    if verbose:
        print(f"[DEBUG] Creating operator '{operator}' resources...")
    if not _apply_yaml(yaml_content, verbose=verbose):
        print(f"Error: Failed to install operator '{operator}' in namespace '{namespace}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Operator '{operator}' installed successfully in namespace '{namespace}'.")
