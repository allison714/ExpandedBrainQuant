"""Write the double-label report (bleed-through and efficiency) from the CSVs of double_label.py.

Run double_label.py first; this only reads its results. The report is one file, pictures
included, so it can sit anywhere: by default on the Desktop.

  double_label_report.py [folder to write it in]
"""
import base64
import csv
import html
import statistics
import sys
from datetime import date
from pathlib import Path

import convert_to_zarr as core

core.load_settings()
base = Path(core.DEFAULT_SHOTS).parent / "Zarr Comparisons" / "Double Label"
target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "OneDrive" / "Desktop"
if not target.is_dir():
    target = Path.home() / "Desktop"
esc = html.escape
num = lambda v: None if v in (None, "") else float(v)


def read(name):
    return list(csv.DictReader(open(base / name, encoding="utf-8-sig")))


def table(heads, rows, cls=""):
    out = [f'<div class="wrap"><table class="{cls}"><thead><tr>' + "".join(f"<th>{esc(h)}</th>" for h in heads) + "</tr></thead><tbody>"]
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{c if str(c).startswith('<') else esc(str(c))}</td>" for c in r) + "</tr>")
    return "\n".join(out) + "</tbody></table></div>"


def picture(name, caption):
    data = base64.b64encode((base / name).read_bytes()).decode("ascii")
    return f'<figure><img src="data:image/png;base64,{data}" alt="{esc(caption)}"><figcaption>{esc(caption)}</figcaption></figure>'


def tag(verdict):
    word = verdict.split(":")[0]
    cls = "down" if word.startswith("NOT RULED OUT") else "up" if word.startswith(("none", "minor")) else "same"
    return f'<span class="v {cls}">{esc(word.lower())}</span>'


bleed, effs = read("Double Label - Bleed-through.csv"), read("Double Label - Efficiency.csv")
day = lambda name: name.split("_")[0]
days = sorted({day(r["Sample"]) for r in effs})
shown = lambda v, spec="{:.1f}": "–" if num(v) is None else spec.format(num(v))

# ---- bleed-through, one row per sample: 561 into 637 and 637 into 561 are AB2 into AB1 and AB1 into AB2
rows_bleed = []
for e in effs:
    cells = [e["Sample"], e["AB1"], e["AB2"]]
    for direction in ("AB2 into AB1", "AB1 into AB2"):
        r = next((b for b in bleed if b["Sample"] == e["Sample"] and b["Direction"] == direction), None)
        if r is None or num(r.get("Bleed-Through At Most (%)")) is None:
            cells += ['<span class="v same">not testable</span>', "–", "–", r["Source Spots"] if r else "–"]
        else:
            cells += [tag(r["Verdict"]), shown(r["Bleed-Through At Most (%)"]), shown(r["Target-Free Source Spots (%)"], "{:.0f}"), r["Source Spots"]]
    rows_bleed.append(cells)
t_bleed = table(["Sample", "AB1 (longest laser)", "AB2", "AB2 into AB1", "At most (% of source)", "AB2 spots with no AB1 (%)", "Spots",
                 "AB1 into AB2", "At most (% of source)", "AB1 spots with no AB2 (%)", "Spots"], rows_bleed, "small")

# ---- by day
by_day = []
for d in days:
    cells = [f"{d[4:6]}/{d[2:4]}/20{d[:2]}", sum(day(e["Sample"]) == d for e in effs)]
    for direction in ("AB2 into AB1", "AB1 into AB2"):
        got = [b for b in bleed if day(b["Sample"]) == d and b["Direction"] == direction and num(b.get("Bleed-Through At Most (%)")) is not None]
        words = {}
        for b in got:
            w = b["Verdict"].split(":")[0].lower()
            words[w] = words.get(w, 0) + 1
        cells.append("; ".join(f"{n} {w}" for w, n in sorted(words.items(), key=lambda kv: -kv[1])) or "not testable")
    by_day.append(cells)
t_day = table(["Imaged (day/month/year)", "Samples", "AB2 into AB1 (561 into 637)", "AB1 into AB2 (637 into 561)"], by_day)

# ---- efficiency
measured = [e for e in effs if num(e.get("Pan Synapses")) and num(e.get("AB1 At AB2+ Synapses (%)")) is not None]
span = lambda lo, hi: "–" if num(lo) is None else f"{num(lo):.0f}–{num(hi):.0f}"
t_eff = table(["Sample", "Synapses in the pan stain", "AB1+ (%)", "AB2+ (%)", "Both (%)", "Both by chance (%)",
               "AB1 at AB2+ synapses (%)", "95% interval", "n", "AB2 at AB1+ synapses (%)", "95% interval", "n"],
              [[e["Sample"], e["Pan Synapses"], e["AB1+ (%)"], e["AB2+ (%)"], e["Both (%)"], e["Both By Chance (%)"],
                f'<strong>{shown(e["AB1 At AB2+ Synapses (%)"], "{:.0f}")}</strong>', span(e["AB1 Low"], e["AB1 High"]), e["AB2+ Synapses"],
                f'<strong>{shown(e["AB2 At AB1+ Synapses (%)"], "{:.0f}")}</strong>', span(e["AB2 Low"], e["AB2 High"]), e["AB1+ Synapses"]]
               for e in measured], "small")
june = [e for e in measured if day(e["Sample"]) == "260616"]
med = lambda key, rows: statistics.median(num(e[key]) for e in rows)
low_hi = lambda key, rows: f"{min(num(e[key]) for e in rows):.0f}–{max(num(e[key]) for e in rows):.0f}"
sept = [e for e in measured if day(e["Sample"]) == "260910"]
unmeasured = [e["Sample"] for e in effs if e not in measured]
count = lambda direction, word: sum(b["Direction"] == direction and b["Verdict"].startswith(word) for b in bleed)

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Double Label Report</title>
<style>
:root {{ --bg:#ffffff; --text:#1b1f24; --dim:#5b6470; --line:#d9dee5; --panel:#f5f7fa; --down:#b3261e; --up:#1a6f3c; --same:#5b6470; --accent:#0b5cad; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111418; --text:#e6e9ee; --dim:#9aa4b2; --line:#2c333d; --panel:#1b2027; --down:#ff8a80; --up:#7ddc9c; --same:#9aa4b2; --accent:#6db3ff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.55 Arial, "Segoe UI", sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:28px 18px 60px; }}
h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ font-size:20px; margin:38px 0 8px; padding-top:14px; border-top:1px solid var(--line); }}
h3 {{ font-size:16px; margin:22px 0 6px; }}
.sub {{ color:var(--dim); margin:0 0 18px; }}
.box {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin:14px 0; }}
.box li {{ margin:5px 0; }}
table {{ border-collapse:collapse; width:100%; margin:10px 0 6px; font-size:14px; }}
th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font-weight:600; background:var(--panel); }}
table.small {{ font-size:12.5px; }} table.small td, table.small th {{ padding:4px 8px; }}
.wrap {{ overflow-x:auto; }}
.v {{ font-weight:700; }} .down {{ color:var(--down); }} .up {{ color:var(--up); }} .same {{ color:var(--same); font-weight:400; }}
figure {{ margin:16px 0; }} figure img {{ max-width:100%; border:1px solid var(--line); border-radius:6px; display:block; }}
figcaption {{ color:var(--dim); font-size:13px; margin-top:6px; }}
.note {{ color:var(--dim); font-size:13px; }}
@media print {{ h2 {{ break-before:page; }} }}
</style></head><body><main>
<h1>Double Label Report: Bleed-Through and Antibody Efficiency</h1>
<p class="sub">Kuan Lab pan-ExM · written {date.today():%d %B %Y} · {len(effs)} samples with two antibody channels (561 and 637 nm), imaged between May and September 2026</p>

<div class="box"><strong>In short</strong>
<ul>
<li><strong>561 into 637: no bleed-through.</strong> In the May, June and July sets the 637 channel picks up at most 1–5% of the 561 signal wherever there were enough spots to test (most samples: under 2%). About half of the bright 561 spots have no 637 signal at all.</li>
<li><strong>637 into 561 on 16 June: cannot be settled from these images.</strong> About 90% of bright 637 spots also carry 561 signal, and the weakest 561 signal seen rises with the 637 signal. That is what bleed-through looks like, but it is also what two antibodies on the same synapses look like, and what a secondary antibody that binds both primaries looks like.</li>
<li><strong>The 10 September set (ABtesting2) is the one to worry about.</strong> Of its 640-positive synapses, 99–100% are also 561-positive, and the 640 brightness follows the 561 brightness closely. Either part of the 640 signal is 561 signal, or both labels are on the same target.</li>
<li><strong>Efficiency on 16 June:</strong> the 637 antibody is on {med("AB1 At AB2+ Synapses (%)", june):.0f}% of the synapses the 561 antibody confirms (range {low_hi("AB1 At AB2+ Synapses (%)", june)}%), and the 561 antibody on {med("AB2 At AB1+ Synapses (%)", june):.0f}% of those the 637 antibody confirms (range {low_hi("AB2 At AB1+ Synapses (%)", june)}%).</li>
<li><strong>What would settle it:</strong> a single-label control, one antibody with both channels imaged at the usual settings. None of these sets has one.</li>
<li>Nothing here has been reviewed by a person or checked against hand measurements.</li>
</ul></div>

<h2>1. What was tested</h2>
<p>Every sample on the server with two or more antibody channels: every channel that is not the pan stain or a nuclear stain. The test works on channels, not antibody names, because for most of these samples it is not yet written down which of NR1 and Homer1 is on 561 and which on 637. "AB1" is the channel on the longest laser (637 or 640 nm, drawn magenta) and "AB2" the next one (561 nm, drawn green), the same numbering as in the Synapse Log and on the synapse images. PFF and bead samples are left out.</p>

<h2>2. Bleed-through</h2>
<h3>How it is measured</h3>
<p>Bleed-through means a fixed share of one channel's light arrives in the other. If so, <em>every</em> bright spot in the source channel must show at least that share in the target channel. Two antibodies on the same synapses also overlap, but nothing forces the target up at every source spot. So the test looks at the source spots with the <em>least</em> target signal:</p>
<ul>
<li>Bright spots are found in the source channel (10 noise-widths above the background). At each one, source and target are averaged over a 5 × 5 pixel square, so a colour shift of a pixel does not matter.</li>
<li>A line is fitted under the cloud of spots (the 10th-percentile quantile regression of target on source). Its slope is how fast the least target seen rises with the source. Bleed-through cannot be larger than that slope; "at most" is its upper 97.5% bound from resampling the spots.</li>
<li>"Spots with no target" is the share of bright source spots where the target is under 2 noise-widths: direct evidence against bleed-through.</li>
</ul>
<p>Verdicts: <span class="v up">none to speak of</span> under 3% of the source; <span class="v up">none detected</span> or <span class="v up">minor</span> under 10%; <span class="v same">inconclusive</span> when there are too few or too dim spots to say; <span class="v down">not ruled out</span> when the least target seen clearly rises with the source. "Not ruled out" is not proof of bleed-through.</p>

<h3>By imaging day</h3>
{t_day}

{picture("Double Label - Summary.png", "Left: the most bleed-through each sample could have, each way (log scale; open markers are 'not ruled out'; the dotted line is 3%). Green squares, 561 into 637, sit at or under 5% except in the September set and one May sample with only 31 spots. Right: efficiency of each antibody at synapses confirmed by the other, with 95% intervals.")}

<h3>Three examples</h3>
{picture("260520_NR1_Homer1_Sample6_10percentoverlap_FusionStitcher_double-label.png", "20 May, Sample 6: clean both ways. Thousands of bright spots in each channel, most with nothing in the other. This shows the 637 and 561 channels can be free of bleed-through with these lasers.")}
{picture("260616_NR1_homer1_12_double-label.png", "16 June, sample 12. Middle: 561 into 637 is flat, no bleed-through. Left: the least 561 seen rises with 637 (white line), so 637 into 561 is not ruled out. Right: how the synapses found in the pan stain divide.")}
{picture("260910_ABtesting2_double-label.png", "10 September, ABtesting2. Both clouds are tight and rising, and only 1 synapse of 230 has the 640 label without the 561 label.")}

<h3>Every sample</h3>
<p class="note">"At most" is the largest share of the source's signal that could be arriving in the other channel. "Not testable" means fewer than 30 bright source spots.</p>
{t_bleed}

<h3>How to read the 16 June result</h3>
<ul>
<li>561 into 637 is excluded: at most 5% in all twelve samples, and under 2% in nine of them.</li>
<li>637 into 561 is "not ruled out" in {sum(b["Direction"] == "AB1 into AB2" and day(b["Sample"]) == "260616" and b["Verdict"].startswith("NOT") for b in bleed)} of twelve samples, inconclusive or not testable in the rest.</li>
<li>Two things argue against optical bleed-through. On 20 May, Sample 6, with the same two lasers, 82% of 2,417 bright 637 spots had no 561 signal. And on 16 June, at the level of synapses, 14–33% of 637-positive synapses have no 561 label, which bleed-through would not allow.</li>
<li>What remains possible: the two antibodies are simply on the same synapses with the 561 one labelling more of them, or the 561 secondary also binds the other primary.</li>
</ul>

<h3>How to read the 20 May result</h3>
<ul>
<li>Samples 2 and 6 are clean both ways, with thousands of spots.</li>
<li>Samples 3 and 5 read "not ruled out" for 637 into 561, but their 637 channel is nearly empty: the few bright 637 spots there are about as bright in 561. Objects that shine in both channels (debris, for instance) would look like this; it is not good evidence of bleed-through, and Sample 6 from the same day shows none.</li>
</ul>

<h2>3. Antibody efficiency</h2>
<h3>How it is measured</h3>
<p>Synapses are found in the pan stain by the expansion-factor tool, without using the antibodies. A synapse is positive for an antibody at 6 noise-widths, the rule used everywhere else. Two readings:</p>
<ul>
<li><strong>Of all synapses found in the pan stain:</strong> the share positive for each antibody. The pan double peaks include some membrane pairs that are not synapses, so these shares are lower bounds.</li>
<li><strong>At synapses confirmed by the other antibody:</strong> of the synapses positive for one antibody, the share also positive for the other. Where both antibodies are meant to be at every excitatory synapse, as NR1 and Homer1 are, this is each antibody's labelling efficiency. Bleed-through would inflate it, which is why section 2 matters.</li>
</ul>
{t_eff}
<ul>
<li><strong>16 June ({len(june)} samples):</strong> AB1 (637) at AB2-positive synapses, median {med("AB1 At AB2+ Synapses (%)", june):.0f}%; AB2 (561) at AB1-positive synapses, median {med("AB2 At AB1+ Synapses (%)", june):.0f}%. The two low AB1 values belong to samples whose 637 channel is sparse overall (260616_NR1_homer1_1 and _8).</li>
<li><strong>10 September:</strong> AB2 at AB1-positive synapses is {" and ".join(shown(e["AB2 At AB1+ Synapses (%)"], "{:.0f}") for e in sept)}%. That is the same observation as the bleed-through flag, so it should not be read as an efficiency.</li>
<li>"Both" is above "both by chance" in every sample: the two labels do go to the same synapses more often than independent labels would.</li>
<li><strong>Not measured ({len(unmeasured)} samples):</strong> no pan stain was found, or too few synapses, in {esc(", ".join(unmeasured))}.</li>
</ul>

<h2>4. Limits, and what to do next</h2>
<ul>
<li><strong>Single-label control.</strong> One antibody only, both channels imaged at the usual laser powers and exposures, on the same day as a double label. This measures bleed-through directly and would settle 16 June and 10 September.</li>
<li><strong>Secondary-only control</strong> for the 561 secondary on a sample carrying only the other primary: this separates a cross-reacting secondary from optical bleed-through.</li>
<li><strong>Which antibody is on which channel.</strong> The channel table (_antibody_channels.csv in the zarr folder) still says "TO DO" for most of these samples. Once filled in, the efficiencies read per antibody rather than per channel.</li>
<li>Laser power and exposure are not in the image files, so differences between days cannot be put down to the labelling alone.</li>
<li>Signals are dim: a typical spot is 8–10 noise-widths. Samples with few bright spots give wide intervals.</li>
<li>A third antibody (the 22 July triple label) is not included in the efficiency.</li>
</ul>

<h2>5. Where everything is</h2>
<ul>
<li>Numbers: "Double Label - Bleed-through.csv" and "Double Label - Efficiency.csv" in {esc(str(base))}</li>
<li>One figure per sample in the same folder: &lt;sample&gt;_double-label.png</li>
<li>To repeat it: Double_Label.bat in the ZarrBatchConverter folder (run Measure_ExF.bat first), then double_label_report.py for this report. The method is described at the top of double_label.py.</li>
</ul>
</main></body></html>"""

out = target / "Double Label Report.html"
out.write_text(page, encoding="utf-8")
print(f"{out}  ({out.stat().st_size / 1e6:.1f} MB)")
