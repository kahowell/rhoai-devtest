import subprocess
import sys


def check_aws_auth(verbose: bool = False) -> None:
    if verbose:
        print("[DEBUG] Checking AWS authentication status...")
    try:
        # Run aws sts get-caller-identity to check if authenticated
        res = subprocess.run(["aws", "sts", "get-caller-identity"], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            print("Error: Not authenticated to AWS.", file=sys.stderr)
            print("Please log in by running:", file=sys.stderr)
            print("  export $(rh-aws-saml-login --output env $AWS_ACCOUNT_NAME)", file=sys.stderr)
            sys.exit(1)
        elif verbose:
            print(f"[DEBUG] AWS check succeeded:\n{res.stdout.strip()}")
    except FileNotFoundError:
        print("Error: 'aws' command-line tool is not installed or not in PATH.", file=sys.stderr)
        sys.exit(1)


def check_ocm_auth(verbose: bool = False) -> None:
    if verbose:
        print("[DEBUG] Checking OCM authentication status...")
    try:
        # Run ocm whoami to check if authenticated
        res = subprocess.run(["ocm", "whoami"], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            print("Error: Not authenticated with OCM.", file=sys.stderr)
            print("Please log in by running:", file=sys.stderr)
            print("  ocm login --url production --use-auth-code", file=sys.stderr)
            sys.exit(1)
        elif verbose:
            print(f"[DEBUG] OCM check succeeded:\n{res.stdout.strip()}")
    except FileNotFoundError:
        print("Error: 'ocm' command-line tool is not installed or not in PATH.", file=sys.stderr)
        sys.exit(1)


def ensure_authenticated(verbose: bool = False) -> None:
    check_aws_auth(verbose=verbose)
    check_ocm_auth(verbose=verbose)
