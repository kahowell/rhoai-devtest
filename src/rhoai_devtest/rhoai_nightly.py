import base64
import getpass
import json
import os
import subprocess
import sys
import time

from .utils import _apply_yaml, install_operator


def get_pull_secret(verbose: bool = False) -> str:
    # Check for secret in environment or prompt
    secret = os.environ.get("RHOAI_QUAY_PULL_SECRET")
    if not secret:
        secret = getpass.getpass("Enter quay.io/rhoai pull secret (base64 username:password): ").strip()
        if not secret:
            print("Error: Pull secret cannot be empty.", file=sys.stderr)
            sys.exit(1)

    # Let's quickly validate that it is valid base64
    try:
        base64.b64decode(secret.strip(), validate=True)
    except (ValueError, TypeError):
        # Some base64 credentials might not pass strict validation due to missing padding, try standard b64decode
        try:
            base64.b64decode(secret.strip())
        except (ValueError, TypeError) as decode_err:
            print(f"Error: Pull secret is not valid base64. {decode_err}", file=sys.stderr)
            sys.exit(1)

    return secret.strip()


def _is_mirror_listed(data) -> bool:
    items = []
    if isinstance(data, dict):
        if data.get("kind") == "List":
            items = data.get("items", [])
        else:
            items = [data]
    elif isinstance(data, list):
        items = data

    for item in items:
        if not isinstance(item, dict):
            continue
        spec = item.get("spec")
        if not isinstance(spec, dict):
            continue
        image_digest_mirrors = spec.get("imageDigestMirrors")
        if not isinstance(image_digest_mirrors, list):
            continue
        for mirror_entry in image_digest_mirrors:
            if not isinstance(mirror_entry, dict):
                continue
            mirrors = mirror_entry.get("mirrors")
            if not isinstance(mirrors, list):
                continue
            for m in mirrors:
                if isinstance(m, str):
                    m_clean = m.strip().rstrip("/").lower()
                    if m_clean == "quay.io/rhoai":
                        return True
    return False


def install_rhoai_nightly(target_cluster: str, nightly_image: str, verbose: bool = False):
    """
    Install a nightly instance of RHOAI using the specified nightly OCI image.
    """
    print(f"\nInstalling RHOAI nightly on '{target_cluster}'...")

    # Step 1: Prompt/Get credentials (already base64 encoded username:password string)
    secret = get_pull_secret(verbose=verbose)

    # Step 2: Create image-mirror
    print(f"Creating image-mirror on ROSA cluster '{target_cluster}'...")
    cmd_mirror = [
        "rosa", "create", "image-mirror",
        "--source=registry.redhat.io/rhoai",
        "--mirrors=quay.io/rhoai",
        f"--cluster={target_cluster}"
    ]
    if verbose:
        print(f"[DEBUG] Running: {' '.join(cmd_mirror)}")
    res_mirror = subprocess.run(cmd_mirror, capture_output=True, text=True, check=False)
    if res_mirror.returncode != 0:
        error_msg = res_mirror.stderr or ""
        if "already exists" in error_msg:
            print("Image mirror for source 'registry.redhat.io/rhoai' already exists for cluster. Skipping...")
        else:
            print(f"Error: Failed to create image mirror on ROSA cluster. {error_msg}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Image mirror created successfully on ROSA cluster.")

    # Step 2.5: Poll imagedigestmirrorsets.config.openshift.io to ensure quay.io/rhoai is listed
    print("Polling imagedigestmirrorsets.config.openshift.io to ensure 'quay.io/rhoai' is listed...")
    timeout = 600
    interval = 10
    start_time = time.time()
    mirror_found = False

    while time.time() - start_time < timeout:
        cmd_get = ["oc", "get", "imagedigestmirrorsets.config.openshift.io", "-o", "json"]
        if verbose:
            print(f"[DEBUG] Running: {' '.join(cmd_get)}")
        res_get = subprocess.run(cmd_get, capture_output=True, text=True, check=False)
        if res_get.returncode == 0:
            try:
                data = json.loads(res_get.stdout)
                if _is_mirror_listed(data):
                    mirror_found = True
                    break
            except json.JSONDecodeError as err:
                if verbose:
                    print(f"[DEBUG] Failed to parse JSON from imagedigestmirrorsets: {err}")
        else:
            if verbose:
                print(f"[DEBUG] Failed to get imagedigestmirrorsets: {res_get.stderr.strip()}")

        time.sleep(interval)

    if not mirror_found:
        print("Error: Timed out waiting for 'quay.io/rhoai' to be listed in imagedigestmirrorsets.", file=sys.stderr)
        sys.exit(1)
    print("Verified 'quay.io/rhoai' is listed in imagedigestmirrorsets.")

    # Step 3: Create additional-pull-secret in kube-system namespace
    print("Creating additional-pull-secret in kube-system namespace...")
    config_dict = {
        "auths": {
            "quay.io/rhoai": {
                "auth": secret
            }
        }
    }
    config_json = json.dumps(config_dict)
    base64_config_json = base64.b64encode(config_json.encode("utf-8")).decode("utf-8")

    secret_yaml = f"""apiVersion: v1
kind: Secret
metadata:
  name: additional-pull-secret
  namespace: kube-system
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: {base64_config_json}
"""
    if not _apply_yaml(secret_yaml, verbose=verbose):
        print("Error: Failed to apply additional-pull-secret.", file=sys.stderr)
        sys.exit(1)
    print("Secret 'additional-pull-secret' applied successfully in 'kube-system' namespace.")

    # Step 4: Create custom catalog with required nightly image reference
    catalog_yaml = f"""apiVersion: operators.coreos.com/v1alpha1
kind: CatalogSource
metadata:
  name: rhoai-catalog-dev
  namespace: openshift-marketplace
spec:
  displayName: Red Hat OpenShift AI
  publisher: RHOAI Development Catalog
  image: {nightly_image}
  sourceType: grpc
"""
    print(f"Creating CatalogSource 'rhoai-catalog-dev' with image '{nightly_image}'...")
    if not _apply_yaml(catalog_yaml, verbose=verbose):
        print("Error: Failed to create CatalogSource 'rhoai-catalog-dev'.", file=sys.stderr)
        sys.exit(1)

    # Step 5: Install operator using source 'rhoai-catalog-dev' and channel 'beta'
    print("Installing RHOAI nightly operator (rhods-operator)...")
    install_operator(
        target_cluster=target_cluster,
        namespace="redhat-ods-operator",
        operator="rhods-operator",
        channel="beta",
        source="rhoai-catalog-dev",
        verbose=verbose,
    )
    print("RHOAI nightly installation completed successfully.")
