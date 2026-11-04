#!/usr/bin/env python3
"""The paper's data figures, as pgfplots code generated from results/ and traces/.

  .venv/bin/python -m experiments.paper_figures

Writes paper/generated/fig_*.tex. Like paper_numbers.py, nothing is typed by
hand: every bar comes from a results file or from the released traces, so a
figure changes only when the data does. The schematic figures (architecture,
usage detection) carry no numbers and live in paper/figures/.

  fig_savings.tex     tool-output tokens removed, same conversations: tracetrim
                      0.1 (prudent), tracetrim 0.2 (safeguards), Headroom, and
                      re-serialisation alone (the lossless part of any saving)
  fig_tools.tex       where the tokens go, per tool, in the two released
                      simulated cases: kept vs removed on held-out runs (0.2)
  fig_live.tex        live runs: billed input tokens per run, raw vs tracetrim

Colours: the first three slots of the categorical palette, validated together
(CVD and normal vision) with the dataviz validator; values are printed on the
marks, so no reading depends on colour alone.
"""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
GEN = ROOT / "paper" / "generated"

# Case names without the table's dagger (the caption says which are simulated).
CASE = {"C1": "Editorial plan", "C2": "Content gaps", "C3": "Literature scan", "C4": "Billing support",
        "C5": "CI triage"}
COLORS = (r"\definecolor{ttgray}{HTML}{9A9893}\definecolor{ttblue}{HTML}{2A78D6}\definecolor{ttorange}{HTML}{EB6834}"
          r"\definecolor{ttaqua}{HTML}{1BAF7A}\definecolor{ttgrid}{HTML}{DDDCD8}\definecolor{ttmuted}{HTML}{52514E}")
AXIS = ("axis x line*=bottom, axis y line*=left, axis line style={draw=ttmuted}, tick style={draw=none}, major grid style={draw=ttgrid}, "
        "tick label style={font=\\footnotesize}, label style={font=\\footnotesize}, "
        "legend style={font=\\footnotesize, draw=none, fill=none}, legend cell align=left")


def load(name):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def savings():
    b, s2, ser = load("phase_b.json"), load("study2.json"), load("reserialize.json")
    rows = []
    for c in ("C1", "C2", "C3", "C4", "C5"):          # y dir=reverse: C1 at the top
        raw = b["cases"][c]["arms"]["raw"]["tool_tokens"]
        v01 = 100 * (1 - b["cases"][c]["arms"]["prudent"]["tool_tokens"] / raw)
        v02 = 100 * (1 - s2["cases"][c]["arms"]["v02"]["tool_tokens"] / raw)
        h = b["cases"][c]["headroom"]
        assert h["tool_tokens"] == raw, "Headroom must be measured on the same conversations"
        hr = 100 * (1 - h["headroom"] / raw)
        rs = 100 * ser["cases"][c]["replay"]["saving"]
        assert ser["cases"][c]["replay"]["raw"] == raw, "re-serialisation must be measured on the same conversations"
        rows.append((CASE[c], v01, v02, hr, rs))
    names = ",".join(f"{{{n}}}" for n, *_ in rows)

    def plot(i, color, label):
        coords = " ".join(f"({r[i]:.4f},{{{r[0]}}})" for r in rows)     # rounded once, by pgf
        return (f"\\addplot[xbar, bar width=6.5pt, fill={color}, draw=white, line width=0.6pt, "
                f"nodes near coords={{\\pgfmathprintnumber[fixed,precision=0]{{\\pgfplotspointmeta}}\\%}}, "
                f"nodes near coords style={{font=\\scriptsize, text=black, anchor=west}}] "
                f"coordinates {{{coords}}};\n\\addlegendentry{{{label}}}\n")

    return (COLORS + "\n\\begin{tikzpicture}\n\\begin{axis}[" + AXIS + ", width=0.92\\linewidth, height=11.4cm, "
            "xbar=1.5pt, xmin=0, xmax=100, area legend, xmajorgrids, xlabel={tool-output tokens removed (\\%)}, "
            f"symbolic y coords={{{names}}}, ytick=data, y dir=reverse, enlarge y limits=0.14, "
            "legend style={at={(0.5,1.02)}, anchor=south, legend columns=2, /tikz/every even column/.append style={column sep=8pt}}, "
            "reverse legend]\n"
            # With y dir=reverse the last plot is drawn at the top of each group.
            + plot(4, "ttgray", "re-serialisation only (lossless)")
            + plot(3, "ttorange", "Headroom")
            + plot(1, "ttaqua", "tracetrim 0.1 (prudent)")
            + plot(2, "ttblue", "tracetrim 0.2 (safeguards)")
            + "\\end{axis}\n\\end{tikzpicture}\n")


def tools():
    from tracetrim import report as rp
    from tracetrim.traces import load as load_trace
    from tracetrim.usage import analyse
    panels = []
    for c in ("C4", "C5"):
        from .paths import TRACES
        paths = sorted((TRACES / c).glob("A*.jsonl"))
        traces = [load_trace(p) for p in paths]
        d = rp.compute([t.name for t in traces], [analyse(t, comma_grouping=False) for t in traces], protect_ids=True)
        rows = [(name, t["loo_prudent"], t["loo_raw"] - t["loo_prudent"]) for name, t in d["tools"].items()
                if t["loo_raw"]]
        rows.sort(key=lambda r: -(r[1] + r[2]))
        rows = rows[:6]
        k = lambda n: f"{n / 1000:.1f}"
        # Numeric y with explicit labels: tool names carry underscores, which
        # symbolic coordinates would have to survive as TeX.
        labels = ",".join("{\\texttt{\\scriptsize " + n.replace("_", "\\_") + "}}" for n, *_ in rows)
        kept = " ".join(f"({k(r[1])},{i})" for i, r in enumerate(rows))
        removed = " ".join(f"({k(r[2])},{i})" for i, r in enumerate(rows))
        total = sum(r[1] + r[2] for r in rows)
        removed_share = 100 * sum(r[2] for r in rows) / total
        panels.append(
            "\\begin{axis}[" + AXIS + f", name={c}, width=0.78\\linewidth, height={1.0 + 0.62 * len(rows):.2f}cm, "
            "xbar stacked, bar width=7pt, xmin=0, xmajorgrids, "
            "xlabel={thousand tokens, held-out runs}, "
            f"ytick={{0,...,{len(rows) - 1}}}, yticklabels={{{labels}}}, y dir=reverse, enlarge y limits=0.12, "
            f"title={{\\footnotesize {CASE[c]}: {removed_share:.0f}\\% removed}}, "
            + ("legend to name=toolslegend, legend columns=2, area legend, " if c == "C4" else "")
            + ("at={(C4.south west)}, anchor=north west, yshift=-2.1cm, " if c == "C5" else "")
            + "]\n"
            f"\\addplot[fill=ttblue, draw=white, line width=0.6pt] coordinates {{{kept}}};\n"
            + ("\\addlegendentry{kept}\n" if c == "C4" else "")
            + f"\\addplot[fill=ttorange, draw=white, line width=0.6pt] coordinates {{{removed}}};\n"
            + ("\\addlegendentry{removed}\n" if c == "C4" else "")
            + "\\end{axis}\n")
    return (COLORS + "\n\\begin{tikzpicture}\n" + "".join(panels)
            + "\\node[anchor=north] at ($(C5.south)+(0,-0.95)$) {\\pgfplotslegendfromname{toolslegend}};\n"
            "\\end{tikzpicture}\n")


def live():
    c = load("phase_c.json")
    panels = []
    for i, case in enumerate(("C1", "C4")):
        arms = c["cases"][case]
        pts = []
        for arm, y, color in (("raw", "raw", "ttorange"), ("prudent", "tracetrim", "ttblue")):
            vals = arms[arm]["billed_input"]
            med = statistics.median(vals)
            pts.append((y, color, vals, med))
        xmax = max(v for _, _, vals, _ in pts for v in vals) / 1000 * 1.18
        body = ""
        for y, color, vals, med in pts:
            body += (f"\\addplot[only marks, mark=*, mark size=2.4pt, draw=white, line width=0.5pt, fill={color}] "
                     f"coordinates {{{' '.join(f'({v / 1000:.1f},{y})' for v in vals)}}};\n"
                     f"\\addplot[mark=|, mark size=5pt, line width=1.6pt, draw=black] coordinates {{({med / 1000:.1f},{y})}} "
                     f"node[above=3pt, font=\\scriptsize] {{median {med / 1000:.0f}k}};\n")
        panels.append(
            "\\begin{axis}[" + AXIS + f", name=P{i}, width=0.47\\linewidth, height=3.6cm, xmin=0, xmax={xmax:.0f}, "
            "xmajorgrids, symbolic y coords={tracetrim,raw}, ytick={tracetrim,raw}, enlarge y limits=0.6, "
            "xlabel={billed input tokens per run (thousands)}, "
            f"title={{\\footnotesize {CASE[case]}}}"
            + (", at={(P0.east)}, anchor=west, xshift=1.6cm" if i else "") + "]\n" + body + "\\end{axis}\n")
    return COLORS + "\n\\begin{tikzpicture}\n" + "".join(panels) + "\\end{tikzpicture}\n"


def main():
    for name, fn in (("fig_savings", savings), ("fig_tools", tools), ("fig_live", live)):
        (GEN / f"{name}.tex").write_text("% Generated by experiments/paper_figures.py. Do not edit.\n" + fn(),
                                         encoding="utf-8")
        print(f"  written: paper/generated/{name}.tex")


if __name__ == "__main__":
    main()
