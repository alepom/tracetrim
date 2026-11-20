"""Seal or verify study inputs and the separate, private allocation fingerprint."""
import argparse

from experiments.paths import ROOT, STUDY_TRACES
from .coordinator import PRIVATE, LOCK, digest, load
from .worlds import DATA, write


def manifest():
    deps = ["protocol-v3.md", "experiments/prices.json", "requirements-study.txt", "pyproject.toml",
            "experiments/prompts/c4-system.md", "experiments/prompts/c5-system.md",
            "experiments/caps.py", "experiments/paths.py", "experiments/checks.py",
            "experiments/tools.py", "experiments/v04.py", "experiments/v04_preconditions.py",
            "experiments/simulated/server_c4.py", "experiments/simulated/server_c5.py"]
    paths = {ROOT / name for name in deps}
    paths.update((ROOT / "src/tracetrim").rglob("*.py"))
    paths.update((ROOT / "experiments/v3").glob("*.py"))
    paths.add(ROOT / "experiments/v3/rubric.md")
    paths.update(DATA.rglob("*.json"))
    return {"format": "tracetrim/protocol-v3@1",
            "files": {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)},
            "anonymous_mapping_sha256": digest(PRIVATE / "mapping.json")}


def verify():
    expected = load(LOCK)
    actual = manifest()
    if expected != actual:
        raise ValueError("Frozen materials or private mapping differ")
    training = load(DATA / "training.json")
    if any(digest(STUDY_TRACES / name) != value for name, value in training.items()):
        raise ValueError("Phase A inputs differ")
    return len(expected["files"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seal", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.seal:
        if LOCK.exists():
            raise FileExistsError(LOCK)
        write(LOCK, manifest())
    print(f"Verified {verify()} frozen files and the private mapping")
