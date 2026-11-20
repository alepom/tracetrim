"""The coordinator's commands after the campaign, with the frozen-file check of live.py.

  python -m experiments.v3.judging pack         # out/v3/pass-1/ and pass-2/: the two blind card sets
  python -m experiments.v3.judging adjudicate   # after scores-1.json and scores-2.json: out/v3/pass-3/
  python -m experiments.v3.judging finish       # after scores-3.json (if any): out/v3/judged.json

The same operations as `python -m experiments.v3.coordinator`, whose own check
(`freeze --verify`) refuses the files added after the freeze (live.py, analysis.py
and this one): here every frozen file must be unchanged, the private mapping must
match its sealed fingerprint, and nothing else may have been added. The mapping is
never printed or opened by this script beyond the coordinator's own use.
"""
import argparse
import json

from . import coordinator
from .live import verify_frozen
from .worlds import DATA


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["pack", "adjudicate", "finish"])
    a = p.parse_args(argv)
    verify_frozen()
    expected = coordinator.load(coordinator.LOCK)["anonymous_mapping_sha256"]
    private = coordinator.PRIVATE
    if a.command == "finish":
        print("Judged answers:", coordinator.finish(private, expected))
        return
    if a.command == "pack":
        n = coordinator.packets(private, private / "traces", DATA / "reference", expected)
        cards = {f"pass-{i}": len(list((private / f"pass-{i}").glob("*.json"))) for i in (1, 2)}
        # Kept private until the scores are locked (protocol: report the count after blind scoring).
        (private / "cues.json").write_text(json.dumps({"answers_with_cues": n, **cards}) + "\n")
        print("Cards written:", cards, "- the count of answers with arm cues is in out/v3/cues.json")
    else:
        print("Answers for third pass:", coordinator.adjudicate(private, private / "traces", DATA / "reference",
                                                              expected))


if __name__ == "__main__":
    main()
