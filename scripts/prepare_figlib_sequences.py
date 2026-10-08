#!/usr/bin/env python3
"""Pin the HPWREN FIgLib camera sequences used by the `wildfire_smoke_watch` scenario.

FIgLib (HPWREN Fire Ignition images Library, UC San Diego) is a set of ~520 recordings from fixed
fire-lookout cameras. Each recording is one `.tgz` of 81 JPEG frames, one a minute, from 40
minutes before a smoke plume becomes visible to 40 minutes after; the frame file name
`<unix_ts>_<signed offset seconds>.jpg` is the ground truth (offset 0 = plume first visible).
Terms: free to use, "a credit reference to https://www.hpwren.ucsd.edu/ in derivative work".

No frame is committed to this repo. `scenario.yaml` pins each archive by SHA-256, and dl-training
downloads them on first use. This script is how that block is produced:

    uv run --with httpx python scripts/prepare_figlib_sequences.py --candidates
        list the colour ("-mobo-c") cameras with their archive size, one sequence per viewpoint
    uv run --with httpx python scripts/prepare_figlib_sequences.py
        download the reviewed SELECTION (cached), print the `source:` YAML with every digest

Camera-disjoint by construction: a *station* ("syp", whatever direction it looks) appears in exactly
one archive, so the test score means "a lookout the model has never seen". Downloads are cached under
`~/.cache/ai-circus/figlib/` (override with --cache).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

BASE = "https://cdn.hpwren.ucsd.edu/HPWREN-FIgLib-Data"
TAR_INDEX = f"{BASE}/Tar/index.html"
DEFAULT_CACHE = Path.home() / ".cache/ai-circus/figlib"
TIMEOUT = httpx.Timeout(600.0, connect=30.0)

# The reviewed selection: split -> archives (without .tgz). Filled in after looking at the frames.
def _pick(*picks: str) -> list[str]:
    """`"20170520 FIRE om-s"` -> `20170520_FIRE_om-s-mobo-c`."""
    return [f"{d}_{fire}_{view}-mobo-c" for d, fire, view in (p.split() for p in picks)]


SELECTION: dict[str, list[str]] = {
    # The wall: six lookouts the model never sees, each with a plume that is unmistakable by ~+25 min.
    # The held-out pool the Watch Wall picks from: 8 fires + 3 no-fire controls (the default wall is set in
    # scenario.yaml: four fires and two controls).
    "test": _pick(
        "20250107 PalisadesFire dwpgm-s",
        "20200611 skyline lp-n",
        "20240909 BridgeFire starr-n",
        "20260715 ThornFire ws-n",
        "20240719 ForkFire wilson-e",
        "20170708 Whittier syp-n",
        # Fainter plumes — the picker's harder fires.
        "20201127 Hawkfire pi-w",
        "20240828 KeysFire bh-w",
        # No-fire controls (CLEAR_ONLY): the pre-ignition half of a clear, daytime, unseen lookout.
        "20180602 Alison sp-s",
        "20190716 FIRE so-w",
        "20250501 FelipeFire ml-n",
    ),
    "validation": _pick("20240825 TenajaFire buff-n", "20250709 SteeleFire om-n"),
    "train": _pick(
        "20250801 BernardoFire bl-n",
        "20251102 ScissorsFire mp-n",
        "20240909 BridgeFire stgo-e",
        "20250107 PalisadesFire 69bravo-e",
        "20180704 Benton hp-n",
        "20260722 CreelmanFire cp-w",
        "20200829 inside-Mexico mlo-s",
        "20240625 DelMarFire tdlln-n",
        "20241108 GardenFire rm-s",
        "20241031 QuarryFire sm-w",
        "20240927 86Fire tp-e",
        "20240718 EggStructureFire bm-s",
    ),
}


# Test recordings used only up to the moment of ignition: a camera where nothing ever happens, so any
# alert on it is a false alarm. (Every FIgLib recording is built around an ignition; its first 40
# minutes are the only fire-free footage in the set.)
CLEAR_ONLY = {"20180602_Alison_sp-s-mobo-c", "20190716_FIRE_so-w-mobo-c", "20250501_FelipeFire_ml-n-mobo-c"}


def viewpoint(archive: str) -> str:
    """`20170708_Whittier_syp-n-mobo-c` -> `syp-n` (station + direction)."""
    return "-".join(archive.rsplit("_", 1)[-1].split("-")[:2])


def list_archives(client: httpx.Client) -> list[str]:
    html = client.get(TAR_INDEX).text
    return sorted(set(re.findall(r'href=([0-9]{8}_[^<>"\s]+?)\.tgz', html)))


def archive_size(client: httpx.Client, name: str) -> int:
    return int(client.head(f"{BASE}/Tar/{name}.tgz", follow_redirects=True).headers.get("content-length", 0))


def candidates(client: httpx.Client) -> None:
    colour = [a for a in list_archives(client) if a.endswith("-mobo-c")]
    with ThreadPoolExecutor(8) as pool:
        sizes = dict(zip(colour, pool.map(lambda a: archive_size(client, a), colour), strict=True))
    by_view: dict[str, list[str]] = {}
    for name in colour:
        by_view.setdefault(viewpoint(name), []).append(name)
    print(f"{len(colour)} colour sequences, {len(by_view)} viewpoints")
    for view, names in sorted(by_view.items()):
        print(f"{view:10s} " + "  ".join(f"{n.split('_')[0]}_{n.split('_')[1]}({sizes[n] >> 20}MB)" for n in names))


def download(client: httpx.Client, name: str, cache: Path) -> Path:
    dest = cache / f"{name}.tgz"
    if not dest.exists():
        cache.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        with client.stream("GET", f"{BASE}/Tar/{name}.tgz", follow_redirects=True) as response:
            response.raise_for_status()
            with part.open("wb") as out:
                for chunk in response.iter_bytes(1 << 20):
                    out.write(chunk)
        part.rename(dest)
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def emit(client: httpx.Client, cache: Path) -> None:
    stations = [viewpoint(a).split("-")[0] for names in SELECTION.values() for a in names]
    if not any(SELECTION.values()):
        sys.exit("SELECTION is empty: run --candidates, look at the frames, then fill it in.")
    if len(stations) != len(set(stations)):
        sys.exit("A station appears in more than one archive: the test cameras must be unseen.")
    jobs = [(split, a) for split, names in SELECTION.items() for a in names]
    with ThreadPoolExecutor(4) as pool:
        paths = list(pool.map(lambda j: download(client, j[1], cache), jobs))
    print("  source:")
    print("    type: http_frame_sequences")
    print(f'    base_url: "{BASE}/Tar"')
    print("    positive_from_offset: 0")
    print("    ambiguous_seconds: 600")
    print('    negative_label: "0"')
    print('    positive_label: "1"')
    print("    archives:")
    for split, name in jobs:
        clear = ", clear_only: true" if name in CLEAR_ONLY else ""
        print(f'      - {{file: "{name}.tgz", split: {split}, camera: "{viewpoint(name)}"{clear}}}')
    print("    sha256:")
    for (_, name), path in zip(jobs, paths, strict=True):
        print(f'      "{name}.tgz": "{sha256(path)}"')
    print(f"\n# {sum(p.stat().st_size for p in paths) >> 20} MB in {len(paths)} archives", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", action="store_true", help="list colour cameras and exit")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    with httpx.Client(timeout=TIMEOUT) as client:
        candidates(client) if args.candidates else emit(client, args.cache)


if __name__ == "__main__":
    main()
