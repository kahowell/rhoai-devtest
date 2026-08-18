import argparse
import sys

from .cleanup import handle_cleanup
from .config import load_config
from .openshift_cluster import create_openshift_cluster, setup_htpasswd_idp
from .rhoai_cluster import handle_rhoai_cluster
from .utils import get_default_match_name


def main():
    parser = argparse.ArgumentParser(
        description="rhoai-devtest — CLI tool for spinning up and tearing down ROSA clusters for RHOAI testing"
    )

    # Global options
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output logging for subprocesses"
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="Available sub-commands")

    # openshift-cluster parser
    openshift_parser = subparsers.add_parser(
        "openshift-cluster",
        help="Provision an OpenShift cluster"
    )
    openshift_parser.add_argument(
        "--name",
        default=None,
        help="Name of the cluster (default: use existing cluster if found, fail otherwise)"
    )
    openshift_parser.add_argument(
        "--machine-type",
        default="m5.2xlarge",
        help="Instance type for the compute node pool (default: m5.2xlarge)"
    )
    openshift_parser.add_argument(
        "--version",
        default=None,
        help="OpenShift version to deploy (default: latest available)"
    )
    openshift_parser.add_argument(
        "--replicas",
        default=None,
        help="Number of compute replicas to provision"
    )

    # rhoai-cluster parser
    rhoai_parser = subparsers.add_parser(
        "rhoai-cluster",
        help="Provision a RHOAI cluster"
    )
    rhoai_parser.add_argument(
        "--name",
        default=None,
        help="Name of the cluster (default: use existing cluster if found, fail otherwise)"
    )
    rhoai_parser.add_argument(
        "--machine-type",
        default="m5.2xlarge",
        help="Instance type for the compute node pool if provisioning a new cluster"
    )
    rhoai_parser.add_argument(
        "--version",
        default=None,
        help="OpenShift version to deploy if provisioning a new cluster"
    )
    rhoai_parser.add_argument(
        "--rhoai-version",
        default=None,
        help="RHOAI version or nightly image reference to deploy (non-quay image/version for specific RHOAI version, quay image for nightly build)"
    )
    rhoai_parser.add_argument(
        "--replicas",
        default=None,
        help="Number of compute replicas to provision if provisioning a new cluster"
    )

    # cleanup parser
    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Remove existing clusters"
    )
    cleanup_parser.add_argument(
        "--name",
        default=get_default_match_name(),
        help="Name pattern to match clusters for cleanup (default: username)"
    )
    cleanup_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Bypass confirmation prompt and delete clusters automatically"
    )

    # setup-idp parser
    setup_idp_parser = subparsers.add_parser(
        "setup-idp",
        help="Configure htpasswd identity provider and grant cluster-admin on an existing cluster"
    )
    setup_idp_parser.add_argument(
        "--name",
        default=None,
        help="Name of the existing cluster (default: infer from username)"
    )

    args = parser.parse_args()

    config = load_config()

    if args.command == "openshift-cluster":
        res = create_openshift_cluster(
            name=args.name,
            machine_type=args.machine_type,
            version=args.version,
            config=config,
            verbose=args.verbose,
            replicas=args.replicas
        )
        if not res:
            sys.exit(1)
    elif args.command == "rhoai-cluster":
        handle_rhoai_cluster(
            name=args.name,
            machine_type=args.machine_type,
            version=args.version,
            config=config,
            verbose=args.verbose,
            rhoai_version=args.rhoai_version,
            replicas=args.replicas
        )
    elif args.command == "cleanup":
        handle_cleanup(
            name_pattern=args.name,
            yes_bypass=args.yes,
            verbose=args.verbose
        )
    elif args.command == "setup-idp":
        setup_htpasswd_idp(
            cluster_name=args.name,
            verbose=args.verbose
        )


if __name__ == "__main__":
    main()
