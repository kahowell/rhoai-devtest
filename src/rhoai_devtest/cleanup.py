import subprocess

from .auth import ensure_authenticated
from .utils import find_matching_clusters


def handle_cleanup(name_pattern: str, yes_bypass: bool, verbose: bool = False):
    ensure_authenticated(verbose=verbose)

    print(f"Looking for clusters matching '{name_pattern}' ...\n")
    matching_clusters = find_matching_clusters(name_pattern, verbose=verbose)

    if not matching_clusters:
        print("No matching clusters found.")
        return

    print(f"Found {len(matching_clusters)} matching cluster(s):")
    for idx, cname in enumerate(matching_clusters, 1):
        print(f"{idx:<4}  {cname}")
    print()

    if not yes_bypass:
        try:
            confirm = input(f"Delete all {len(matching_clusters)} cluster(s) above? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                print("Aborted.")
                return
        except KeyboardInterrupt:
            print("\nAborted.")
            return

    for cname in matching_clusters:
        print(f"Deleting cluster: {cname}")
        cmd = ["rosa", "delete", "cluster", "--cluster", cname]
        if yes_bypass:
            cmd.append("--yes")
        subprocess.run(cmd, check=False)

    print("Done. All requested clusters are being deleted.")
