import sys
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "rhoai-devtest" / "config.toml"

DEFAULT_CONFIG_CONTENT = """# rhoai-devtest configuration file
# Contains infrastructure details for ROSA cluster provisioning

oidc_config_id = "REPLACE_WITH_YOUR_OIDC_CONFIG_ID"
installer_role = "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_INSTALLER_ROLE"
support_role = "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_SUPPORT_ROLE"
worker_role = "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_WORKER_ROLE"
subnet_pairs = [
    "subnet-REPLACE_WITH_SUBNET_A,subnet-REPLACE_WITH_SUBNET_B"
]
"""


def has_placeholders(config: dict[str, Any]) -> bool:
    placeholders = ["REPLACE_WITH_", "AWS_ACCOUNT_ID"]

    def check_val(val) -> bool:
        if isinstance(val, str):
            return any(p in val for p in placeholders)
        elif isinstance(val, list):
            return any(check_val(item) for item in val)
        return False

    return any(check_val(v) for v in config.values())


def load_config() -> dict[str, Any]:
    if not DEFAULT_CONFIG_PATH.exists():
        print(f"Config file not found at {DEFAULT_CONFIG_PATH}. Creating default configuration with placeholders...")
        try:
            DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_CONFIG_PATH.write_text(DEFAULT_CONFIG_CONTENT)
            print(f"Default config file successfully created at {DEFAULT_CONFIG_PATH}.")
            print(f"\n[ACTION REQUIRED] Please open {DEFAULT_CONFIG_PATH} and replace all placeholder values with your real AWS and ROSA infrastructure details before using the tool.\n")
            sys.exit(0)
        except OSError as e:
            print(f"Warning: Could not create config file at {DEFAULT_CONFIG_PATH}: {e}", file=sys.stderr)
            print("Using placeholder-laden internal config fallback.", file=sys.stderr)
            config = {
                "oidc_config_id": "REPLACE_WITH_YOUR_OIDC_CONFIG_ID",
                "installer_role": "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_INSTALLER_ROLE",
                "support_role": "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_SUPPORT_ROLE",
                "worker_role": "arn:aws:iam::AWS_ACCOUNT_ID:role/REPLACE_WITH_WORKER_ROLE",
                "subnet_pairs": [
                    "subnet-REPLACE_WITH_SUBNET_A,subnet-REPLACE_WITH_SUBNET_B"
                ]
            }
    else:
        try:
            with open(DEFAULT_CONFIG_PATH, "rb") as f:
                config = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            print(f"Error parsing config file at {DEFAULT_CONFIG_PATH}: {e}", file=sys.stderr)
            sys.exit(1)

    if has_placeholders(config):
        print(f"\n[ERROR] Placeholders detected in configuration file at {DEFAULT_CONFIG_PATH}.\n", file=sys.stderr)
        print("Please replace all placeholder values with your real AWS and ROSA infrastructure details.", file=sys.stderr)
        print(f"Open {DEFAULT_CONFIG_PATH} and update fields like 'oidc_config_id', roles, and 'subnet_pairs'.\n", file=sys.stderr)
        sys.exit(1)

    return config
