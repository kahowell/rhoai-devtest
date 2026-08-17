# rhoai-devtest — ROSA & RHOAI Cluster Automation

`rhoai-devtest` is a modular, modern CLI tool for provisioning and tearing down Red Hat OpenShift Service on AWS (ROSA) and Red Hat OpenShift AI (RHOAI) clusters for development and testing.

It simplifies the process of AWS and OCM authentication verification, cluster version discovery, automated subnet-pair failover, and interactive cluster cleanups.

---

## Prerequisites

Ensure the following tools are installed and present in your system's `$PATH`:

- [**`ocm`**](https://github.com/openshift-online/ocm-cli) — OpenShift Cluster Manager CLI
- [**`rosa`**](https://github.com/openshift/rosa) — Red Hat OpenShift Service on AWS CLI
- [**`aws`**](https://aws.amazon.com/cli/) — AWS Command Line Interface
- [**`rh-aws-saml-login`**](https://github.com/redhat-developer/rh-aws-saml-login) — AWS SAML authentication tool

---

## Installation

You can install `rhoai-devtest` in editable development mode from the root of this project:

```bash
pip install -e .
```

Or using **`uv`**:

```bash
uv pip install -e .
```

This installs the `rhoai-devtest` binary executable into your standard Python script path.

---

## Configuration

On its very first run, `rhoai-devtest` will automatically bootstrap a default configuration file in your home directory:

```bash
~/.config/rhoai-devtest/config.toml
```

This file contains default infrastructure details. You can customize roles, OIDC configurations, and available subnet pairs directly:

```toml
# ~/.config/rhoai-devtest/config.toml
# Contains infrastructure details for ROSA cluster provisioning

oidc_config_id = "REPLACE_WITH_YOUR_OIDC_CONFIG_ID"
installer_role = "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_INSTALLER_ROLE"
support_role = "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_SUPPORT_ROLE"
worker_role = "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_WORKER_ROLE"
subnet_pairs = [
    "subnet-REPLACE_WITH_SUBNET_A,subnet-REPLACE_WITH_SUBNET_B"
]
```

---

## Authentication

Before running any CLI commands, ensure that you are authenticated with both AWS and OpenShift Cluster Manager (OCM). `rhoai-devtest` checks your authentication status prior to performing any cluster operations.

If you are not logged in, please authenticate using:

1. **AWS CLI** (via `rh-aws-saml-login`):
   ```bash
   export $(rh-aws-saml-login --output env $AWS_ACCOUNT_NAME)
   ```
2. **OCM CLI**:
   ```bash
   ocm login --url production --use-auth-code
   ```

---

## Usage

```bash
rhoai-devtest [-h] [-v] {openshift-cluster,rhoai-cluster,cleanup} ...
```

### Global Options
* `-v, --verbose`: Enables detailed debugging logs showing output from all underlying subprocess invocations (`rosa`, `ocm`, `rh-aws-saml-login`).

---

### Commands

#### 1. `openshift-cluster`
Provisions a Red Hat OpenShift cluster.
```bash
rhoai-devtest openshift-cluster [--name NAME] [--machine-type TYPE] [--version VERSION]
```

* `--name`: The name of the cluster. If specified and the cluster already exists, it is reused. If specified and it does not exist, a new cluster is provisioned. If `--name` is not specified, an existing active cluster matching your system username is used, or the command fails if none are found.
* `--machine-type`: Compute machine type for node pools. Defaults to `m5.2xlarge`.
* `--version`: Specific OpenShift version. Defaults to the latest available ROSA release.

This command verifies AWS and OCM authentication status, sorts and discovers the latest stable OpenShift versions, and rotates through configured subnet pairs until one successfully accepts the cluster creation request.

#### 2. `rhoai-cluster`
Provisions a RHOAI development cluster.
```bash
rhoai-devtest rhoai-cluster [--name NAME] [--machine-type TYPE] [--version VERSION] [--nightly-image IMAGE]
```

* `--name`: Name of the cluster. If specified and the cluster already exists, it is reused. If specified and it does not exist, a new cluster is provisioned. If `--name` is not specified, an existing active cluster matching your system username is used, or the command fails if none are found.
* `--machine-type`: Compute machine type if a new cluster is provisioned.
* `--version`: OpenShift version if a new cluster is provisioned.
* `--nightly-image`: Installs a nightly instance of RHOAI using the specified nightly OCI image reference (e.g. quay.io/rhoai/rhoai-fbc-fragment:rhoai-3.5@sha256:...) instead of a normal released version.

#### 3. `cleanup`
Safely destroys existing development and test clusters.
```bash
rhoai-devtest cleanup [--name MATCH] [-y]
```

* `--name`: The pattern to look for when identifying clusters to destroy. Defaults to your system username (matches all clusters containing your username).
* `-y, --yes`: Bypasses the confirmation prompt to delete clusters automatically. Excellent for automation and CI/CD pipelines.

---

## Package Architecture

The codebase is split into modular components for ease of maintenance:

```
src/rhoai_devtest/
├── __init__.py           # Package entrypoint (exposes main())
├── auth.py               # AWS SAML and OCM logins
├── cleanup.py            # Teardown logic
├── cli.py                # Command Line parser definition & routing
├── config.py             # Config directory setup and TOML parsing
├── openshift_cluster.py  # OpenShift / ROSA cluster provisioning
├── rhoai.py              # Normal RHOAI installation of released product
├── rhoai_cluster.py      # Coordinator for cluster provisioning & RHOAI deployment
├── rhoai_nightly.py      # RHOAI nightly installation
└── utils.py              # Core helpers (defaults, version discovery)
```
