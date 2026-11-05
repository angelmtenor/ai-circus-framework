#!/usr/bin/env python3
"""Move trained deep_learning artifacts between two stacks, as one tar file.

Train on a GPU machine, serve on another (dl-inference only needs a CPU):

    gpu-machine$ make dl-artifacts-export SCENARIOS=symptom_triage,chest_xray_pneumonia FILE=dl-models.tar
    this-machine$ make dl-artifacts-import FILE=dl-models.tar
    this-machine$ kubectl -n ai-circus rollout restart deployment/dl-inference

Copies every object of the org's prefix in each scenario's bucket except `raw/` (the
source data, which a stack re-downloads). The manifest goes last on import, the same
contract dl-training's own upload follows: dl-inference never sees a half-copied model.
The org is scoped explicitly (ORG_ID, default `demo`, dl-inference's shared fallback org).

Run through the Makefile: it provides the object-store credentials from .env and the
dl-training environment (boto3 + ai_circus_shared).
"""

from __future__ import annotations

import argparse
import io
import os
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from ai_circus_shared.deep_learning import DL_METADATA_KEY as MANIFEST
from ai_circus_shared.scenario_schema import resolve_scenarios
from ai_circus_shared.storage import ObjectStore

SKIPPED_PREFIX = "raw/"
FORWARD_PORT = 18334


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _endpoint() -> tuple[str, subprocess.Popen[bytes] | None]:
    """OBJECT_STORE_ENDPOINT if set, else a temporary port-forward to the k3s SeaweedFS."""
    if endpoint := os.environ.get("OBJECT_STORE_ENDPOINT"):
        return endpoint, None
    forward = subprocess.Popen(
        ["kubectl", "-n", "ai-circus", "port-forward", "svc/seaweedfs", f"{FORWARD_PORT}:8333"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        if _reachable("127.0.0.1", FORWARD_PORT):
            return f"http://127.0.0.1:{FORWARD_PORT}", forward
        time.sleep(0.5)
    forward.terminate()
    sys.exit("No SeaweedFS reachable: start the stack or set OBJECT_STORE_ENDPOINT.")


def _store(endpoint: str, bucket: str, *, create: bool) -> ObjectStore:
    return ObjectStore.connect(
        endpoint_url=endpoint,
        access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
        secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
        bucket=bucket,
        create_bucket=create,
    )


def export_artifacts(endpoint: str, buckets: dict[str, str], org: str, path: Path) -> None:
    """Write `<slug>/<key>` members for every scenario that has a trained model."""
    with tarfile.open(path, "w") as tar:
        for slug, bucket in buckets.items():
            store = _store(endpoint, bucket, create=True)
            if not store.exists(org, MANIFEST):
                print(f"⚠️  {slug}: no trained model for org={org}, skipped")
                continue
            keys = [k for k in store.list(org) if not k.startswith(SKIPPED_PREFIX)]
            for key in keys:
                data = store.get(org, key)
                info = tarfile.TarInfo(f"{slug}/{key}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            print(f"✓ {slug}: {len(keys)} objects")


def import_artifacts(endpoint: str, buckets: dict[str, str], org: str, path: Path) -> None:
    """Upload a tar written by export_artifacts, each scenario's manifest last."""
    with tarfile.open(path, "r") as tar:
        members: dict[str, list[tarfile.TarInfo]] = {}
        for member in tar.getmembers():
            slug, _, key = member.name.partition("/")
            if not member.isfile() or not key or ".." in Path(key).parts:
                sys.exit(f"Refusing an unexpected archive member: {member.name!r}")
            if slug not in buckets:
                sys.exit(f"{slug!r} is not a deep_learning scenario of this repo.")
            members.setdefault(slug, []).append(member)
        for slug, group in members.items():
            store = _store(endpoint, buckets[slug], create=True)
            group.sort(key=lambda m: m.name.partition("/")[2] == MANIFEST)  # manifest last
            if group[-1].name.partition("/")[2] != MANIFEST:
                sys.exit(f"{slug}: the archive has no {MANIFEST}.")
            for member in group:
                source = tar.extractfile(member)
                assert source is not None  # isfile() was checked above
                store.put(org, member.name.partition("/")[2], source.read())
            print(f"✓ {slug}: {len(group)} objects")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["export", "import"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--scenarios", default="", help="comma-separated slugs (empty = every deep_learning scenario)")
    parser.add_argument("--org", default=os.environ.get("DL_ARTIFACTS_ORG", "demo"))
    parser.add_argument("--scenarios-dir", type=Path, default=Path(__file__).resolve().parent.parent / "scenarios")
    args = parser.parse_args()

    definitions = resolve_scenarios(args.scenarios_dir, args.scenarios, "deep_learning")
    buckets = {slug: d.deep_learning.bucket for slug, d in definitions.items() if d.deep_learning}
    endpoint, forward = _endpoint()
    try:
        if args.action == "export":
            export_artifacts(endpoint, buckets, args.org, args.file)
        else:
            import_artifacts(endpoint, buckets, args.org, args.file)
    finally:
        if forward:
            forward.terminate()


if __name__ == "__main__":
    main()
