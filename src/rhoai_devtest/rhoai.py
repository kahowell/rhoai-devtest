import json
import subprocess
import sys
import time

from .utils import install_operator


def validate_and_resolve_rhoai_version(version: str, verbose: bool = False) -> tuple[str, str]:
    """
    Validate the specified RHOAI version against the cluster's PackageManifest
    and resolve the correct channel and startingCSV.

    If the version is invalid or does not exist, raise an error and terminate.
    """
    print(f"Validating RHOAI version '{version}' against the cluster PackageManifest...")

    # Run the oc get packagemanifest command
    cmd = ["oc", "get", "packagemanifest", "rhods-operator", "-n", "openshift-marketplace", "-o", "json"]
    if verbose:
        print(f"[DEBUG] Running: {' '.join(cmd)}")

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            print(f"Error: Failed to query PackageManifest 'rhods-operator' on the cluster:\n{res.stderr.strip() if res.stderr else 'unknown error'}", file=sys.stderr)
            sys.exit(1)

        manifest = json.loads(res.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError, KeyError, ValueError, TypeError) as e:
        print(f"Error: Failed to execute or parse 'oc get packagemanifest': {e}", file=sys.stderr)
        sys.exit(1)

    channels = manifest.get("status", {}).get("channels", [])

    available_versions = set()
    available_csvs = set()
    available_channels = []

    for ch in channels:
        ch_name = ch.get("name", "")
        if ch_name:
            available_channels.append(ch_name)

        current_csv = ch.get("currentCSV", "")
        if current_csv:
            available_csvs.add(current_csv)
            if ".v" in current_csv:
                available_versions.add(current_csv.split(".v")[-1])

        entries = ch.get("entries", [])
        for entry in entries:
            entry_name = entry.get("name", "")
            entry_version = entry.get("version", "")
            if entry_name:
                available_csvs.add(entry_name)
            if entry_version:
                available_versions.add(entry_version)
            elif entry_name and ".v" in entry_name:
                available_versions.add(entry_name.split(".v")[-1])

    # Try to match the user's requested version
    target_csv_exact = f"rhods-operator.v{version}"

    for ch in channels:
        ch_name = ch.get("name", "")
        current_csv = ch.get("currentCSV", "")

        # Check if the channel's current CSV matches exactly
        if current_csv == target_csv_exact or (current_csv and current_csv.endswith(f".v{version}")):
            if verbose:
                print(f"[DEBUG] Exact match found! Channel: '{ch_name}', CSV: '{current_csv}'")
            return ch_name, current_csv

        # Check channel entries
        entries = ch.get("entries", [])
        for entry in entries:
            entry_name = entry.get("name", "")
            entry_version = entry.get("version", "")
            if entry_version == version or entry_name == target_csv_exact:
                if verbose:
                    print(f"[DEBUG] Exact match found in entries! Channel: '{ch_name}', CSV: '{entry_name}'")
                return ch_name, entry_name

    # Check prefix matches (e.g. they specify "2.13" and the manifest has "2.13.0")
    prefix_matches = []
    for ch in channels:
        ch_name = ch.get("name", "")
        current_csv = ch.get("currentCSV", "")
        entries = ch.get("entries", [])

        if current_csv and current_csv.startswith(target_csv_exact):
            prefix_matches.append((ch_name, current_csv))

        for entry in entries:
            entry_name = entry.get("name", "")
            if entry_name and entry_name.startswith(target_csv_exact):
                prefix_matches.append((ch_name, entry_name))

    if prefix_matches:
        prefix_matches.sort(key=lambda x: x[1], reverse=True)
        resolved_channel, resolved_csv = prefix_matches[0]
        print(f"Version '{version}' matched available CSV '{resolved_csv}' in channel '{resolved_channel}'.")
        return resolved_channel, resolved_csv

    # Fail validation
    print(f"\nError: RHOAI version '{version}' is not available on this cluster.", file=sys.stderr)
    print("Available channels in marketplace:", file=sys.stderr)
    for chan in sorted(available_channels):
        print(f"  - {chan}", file=sys.stderr)
    print("\nAvailable versions in marketplace:", file=sys.stderr)

    # Sort versions semantically
    try:
        sorted_vers = sorted(available_versions, key=lambda x: [int(i) if i.isdigit() else i for i in x.split('.')])
    except (ValueError, TypeError, AttributeError, IndexError):
        sorted_vers = sorted(available_versions)

    for ver in sorted_vers:
        print(f"  - {ver}", file=sys.stderr)

    sys.exit(1)


def wait_for_and_approve_install_plan(namespace: str, subscription_name: str, verbose: bool = False):
    """
    Wait for the InstallPlan resource to be created for the specified Subscription
    and automatically approve it by patching the resource.
    """
    print(f"Waiting for InstallPlan for Subscription '{subscription_name}' in namespace '{namespace}'...")
    timeout = 300  # 5 minutes
    interval = 5
    elapsed = 0

    while elapsed < timeout:
        cmd = ["oc", "get", "subscription", subscription_name, "-n", namespace, "-o", "json"]
        if verbose:
            print(f"[DEBUG] Running: {' '.join(cmd)}")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                sub_data = json.loads(res.stdout)
                status = sub_data.get("status", {})

                # Check for installplan or installPlanRef
                install_plan_ref = status.get("installPlanRef") or status.get("installplan")
                if install_plan_ref and isinstance(install_plan_ref, dict):
                    ip_name = install_plan_ref.get("name")
                    if ip_name:
                        print(f"Found InstallPlan '{ip_name}'. Approving it...")

                        # Patch the InstallPlan to approve it
                        patch_cmd = [
                            "oc", "patch", "installplan", ip_name,
                            "-n", namespace,
                            "--type", "merge",
                            "-p", '{"spec":{"approved":true}}'
                        ]
                        if verbose:
                            print(f"[DEBUG] Running: {' '.join(patch_cmd)}")

                        patch_res = subprocess.run(patch_cmd, capture_output=True, text=True, check=False)
                        if patch_res.returncode == 0:
                            print(f"Successfully approved InstallPlan '{ip_name}'.")
                            return
                        else:
                            print(f"Error: Failed to patch InstallPlan '{ip_name}': {patch_res.stderr.strip() if patch_res.stderr else 'unknown error'}", file=sys.stderr)
                            sys.exit(1)
            else:
                if verbose:
                    print(f"[DEBUG] oc get subscription failed with exit code {res.returncode}: {res.stderr.strip() if res.stderr else 'no output'}")
        except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError, KeyError, ValueError, TypeError) as e:
            if verbose:
                print(f"[DEBUG] Exception encountered while checking subscription: {e}")

        time.sleep(interval)
        elapsed += interval

    print(f"Error: Timed out waiting for InstallPlan for Subscription '{subscription_name}' in namespace '{namespace}' after {timeout} seconds.", file=sys.stderr)
    sys.exit(1)


def install_rhoai(target_cluster: str, version: str | None = None, verbose: bool = False):
    """
    Install standard released version of RHOAI.
    """
    print(f"\nInstalling RHOAI standard released version on '{target_cluster}'...")

    if version:
        channel, starting_csv = validate_and_resolve_rhoai_version(version, verbose=verbose)
        install_plan_approval = "Manual"
        print(f"Configured installation with channel '{channel}', startingCSV '{starting_csv}', and disabled automatic updates (installPlanApproval=Manual).")
    else:
        channel = "stable-3.x"
        starting_csv = None
        install_plan_approval = "Automatic"
        print(f"Configured installation with default channel '{channel}' and automatic updates enabled.")

    install_operator(
        target_cluster=target_cluster,
        namespace="redhat-ods-operator",
        operator="rhods-operator",
        channel=channel,
        starting_csv=starting_csv,
        install_plan_approval=install_plan_approval,
        verbose=verbose,
    )

    if install_plan_approval == "Manual":
        wait_for_and_approve_install_plan(
            namespace="redhat-ods-operator",
            subscription_name="rhods-operator",
            verbose=verbose,
        )
