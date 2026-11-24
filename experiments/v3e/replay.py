"""The rules added to the text previews after protocol v3e, replayed on its runs (no spend, no model).

  python -m experiments.v3e.replay          # results/v3e/replay.json; reads the private traces in out/v3e/

After the runs, 1.5 changed the previews in three ways: markup removed and whitespace collapsed before the cut,
texts over TEXT_LONG readable characters shown by their size, and a bound of TEXT_SHARE of the tokens the cut saves.
Each result of v3e is passed again through the frozen prudent policy P of protocol v3 with the current library,
with the previews of its arm (P2: 0, P2T: 40), and compared with what the agent received in the run (`sent`, call_id
aside). Identical lines mean the result of v3e holds for the library that ships. It also reports what share of the
saving the previews used, the number that sets TEXT_SHARE.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import median

from experiments.paths import ROOT
from experiments.v3 import live
from tracetrim.engine import TEXT_LABEL, TEXT_SHARE, Trimmer
from tracetrim.runtime import Policy

from .run import ARMS, PREVIEW, slots, where

PREVIEWS = {"P2": 0, "P2T": PREVIEW}


def same_id(text: str) -> str:
    return re.sub(r"call_id=c[0-9a-f]{8}", "call_id=c00000000", text)


def trimmer(task: str, chars: int) -> Trimmer:
    policy = Policy.load(live.DATA / "policies" / task[:2] / "P" / "config.json")
    policy.text_preview = chars
    return Trimmer(policy)


def replay() -> dict:
    w = where()
    rows = {a: {"results": 0, "identical": 0, "with_previews": 0} for a in ARMS}
    shares, different = [], []
    for s in slots():
        if not w.trace(s).exists():
            continue
        task, arm = s.split("/")[0], s.rsplit("/", 1)[1]
        for line in w.trace(s).read_text().splitlines():
            e = json.loads(line)
            if e.get("kind") != "call" or not isinstance(e.get("output"), str) or "sent" not in e:
                continue
            tr = trimmer(task, PREVIEWS[arm])
            text = same_id(tr.observe(e["tool"], e["output"]).text)
            r = rows[arm]
            r["results"] += 1
            r["identical"] += text == same_id(e["sent"])
            if text != same_id(e["sent"]) and len(different) < 5:
                different.append(s)
            if TEXT_LABEL in text:
                r["with_previews"] += 1
                base = trimmer(task, 0)
                without = same_id(base.observe(e["tool"], e["output"]).text)
                whole = tr.last_whole[2]
                shares.append((tr.count(text) - tr.count(without)) / (whole - tr.count(without)))
    return {"protocol": "protocol-v3e.md", "arms": rows, "different_examples": different,
            "share_of_saving_used_by_previews": {"results": len(shares), "median": round(median(shares), 3),
                                                 "max": round(max(shares), 3)} if shares else None,
            "text_share_bound": TEXT_SHARE}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "results/v3e/replay.json")
    a = p.parse_args(argv)
    res = replay()
    a.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    for arm, r in res["arms"].items():
        print(f"{arm:4} results {r['results']:5}  identical to what the agent received {r['identical']:5}  "
              f"with previews {r['with_previews']:4}")
    print(f"share of the saving used by the previews: {res['share_of_saving_used_by_previews']}; bound {TEXT_SHARE}")


if __name__ == "__main__":
    main()
