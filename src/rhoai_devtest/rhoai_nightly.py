import base64
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

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


def _bundle_version_key(bundle_name: str) -> tuple:
    """Sort key for CSV names like 'rhods-operator.3.6.0-ea.2'.

    A plain release (3.6.0) sorts above any pre-release of the same version
    (3.6.0-ea.2), mirroring OLM's semver upgrade ordering.
    """
    version = bundle_name.split(".", 1)[1] if "." in bundle_name else bundle_name
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[-.](.+))?$", version)
    if not match:
        return ((0,), 0, version)
    nums = tuple(int(p) for p in match.group(1).split("."))
    pre = match.group(2)
    # Final releases sort above pre-releases of the same numeric version.
    return (nums, 0 if pre else 1, pre or "")


def _pick_channel_from_configs(configs_dir: Path, package: str = "rhods-operator") -> tuple[str, str]:
    """Pick the OLM channel whose head carries the newest bundle.

    CI builds publish fresh bundles under the stable-<version> channels while
    the 'beta' channel keeps only the last EA build, so the channel cannot be
    hardcoded. Reads the file-based catalog extracted from the fragment image
    and returns (channel, head_bundle).
    """
    bundle_names = set()
    channels: dict[str, list[str]] = {}
    for catalog_file in sorted(configs_dir.rglob("*.yaml")):
        with catalog_file.open() as fh:
            for doc in yaml.safe_load_all(fh):
                if not isinstance(doc, dict) or doc.get("package") != package:
                    continue
                schema = doc.get("schema")
                if schema == "olm.bundle":
                    bundle_names.add(doc["name"])
                elif schema == "olm.channel":
                    entries = [e["name"] for e in doc.get("entries", []) if isinstance(e, dict) and "name" in e]
                    if entries:
                        channels.setdefault(doc["name"], []).extend(entries)

    if not channels:
        raise ValueError(f"No olm.channel entries for package '{package}' under {configs_dir}")

    heads = {ch: max(entries, key=_bundle_version_key) for ch, entries in channels.items()}
    newest_bundle = max(bundle_names or set(heads.values()), key=_bundle_version_key)
    # Prefer the most specific channel: stable-<major>.<minor> of the newest
    # bundle, falling back to the first channel offering it (alphabetical).
    offering = sorted(ch for ch, head in heads.items() if head == newest_bundle)
    preferred = f"stable-{'.'.join(newest_bundle.split('.', 2)[1:3])}"
    channel = preferred if preferred in offering else offering[0]
    return channel, heads[channel]


def _extract_fbc_configs(nightly_image: str, secret: str, verbose: bool = False) -> Path:
    """Pull the FBC fragment image and extract its declarative config root.

    The fragment serves /configs (see the catalog Dockerfile's
    'operators.operatorframework.io.index.configs.v1' label). Uses the same
    quay.io/rhoai pull secret as the cluster-side pulls. Returns the extracted
    configs directory; the caller is responsible for removing its parent.
    """
    authfile = Path(tempfile.mkdtemp(prefix="rhoai-devtest-auth-")) / "auth.json"
    authfile.write_text(
        json.dumps({"auths": {"quay.io": {"auth": secret}}})
    )
    authfile.chmod(0o600)
    extract_root = Path(tempfile.mkdtemp(prefix="rhoai-devtest-fbc-"))
    try:
        cmd = [
            "oc", "image", "extract", nightly_image,
            "--path", f"/configs:{extract_root}",
            "--registry-config", str(authfile),
            "--confirm",
        ]
        if verbose:
            print(f"[DEBUG] Running: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to extract FBC configs from '{nightly_image}': {res.stderr.strip()}"
            )
        configs_dirs = [d for d in extract_root.rglob("*") if d.is_dir() and any(d.glob("*.yaml"))]
        if not configs_dirs:
            raise RuntimeError(f"No catalog YAML found under {extract_root}")
        return extract_root
    finally:
        authfile.unlink(missing_ok=True)
        authfile.parent.rmdir()


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

    # Step 5: Determine the appropriate channel from the fragment's own catalog.
    # The 'beta' channel only ever carries the last EA build; CI builds publish
    # fresh bundles under stable-<version> channels, so inspect the image.
    print(f"Extracting catalog from nightly image to determine install channel...")
    try:
        extract_root = _extract_fbc_configs(nightly_image, secret, verbose=verbose)
    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
    try:
        channel, head_bundle = _pick_channel_from_configs(extract_root)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
    finally:
        shutil.rmtree(extract_root, ignore_errors=True)
    print(f"Selected channel '{channel}' (head bundle: {head_bundle}).")

    # Step 6: Install operator using source 'rhoai-catalog-dev' and the selected channel
    print("Installing RHOAI nightly operator (rhods-operator)...")
    install_operator(
        target_cluster=target_cluster,
        namespace="redhat-ods-operator",
        operator="rhods-operator",
        channel=channel,
        source="rhoai-catalog-dev",
        verbose=verbose,
    )
    print("RHOAI nightly installation completed successfully.")
