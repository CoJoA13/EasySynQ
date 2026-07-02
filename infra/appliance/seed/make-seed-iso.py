"""Build the NoCloud cloud-init seed ISO (volume label `cidata`) for the EasySynQ appliance.

Pure-python via pycdlib (run with: uv run --with pycdlib python3 make-seed-iso.py ...), so the
appliance build needs no root, no mtools/genisoimage. cloud-init reads user-data/meta-data from
the labeled ISO; the provision script mounts the same ISO by label to fetch the repo + provision
bundles (NoCloud itself only consumes the two config files).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pycdlib


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--user-data", required=True)
    p.add_argument("--meta-data", required=True)
    p.add_argument("--extra", action="append", default=[], help="extra file(s) for the ISO root")
    p.add_argument("--version", default="dev", help="repo sha stamped into version.txt")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    iso = pycdlib.PyCdlib()
    # Joliet + Rock Ridge so Linux mounts show the real long filenames.
    iso.new(interchange_level=4, joliet=3, rock_ridge="1.09", vol_ident="cidata")

    def add(path: Path, name: str) -> None:
        iso_name = "/" + name.upper().replace("-", "_").replace(".", "_")[:20] + ".;1"
        iso.add_file(str(path), iso_name, rr_name=name, joliet_path=f"/{name}")

    add(Path(args.user_data), "user-data")
    add(Path(args.meta_data), "meta-data")
    for extra in args.extra:
        add(Path(extra), Path(extra).name)

    version = Path(args.out).with_suffix(".version.txt")
    version.write_text(args.version + "\n")
    add(version, "version.txt")

    iso.write(args.out)
    iso.close()
    version.unlink()
    print(f"seed ISO written: {args.out}")


if __name__ == "__main__":
    main()
