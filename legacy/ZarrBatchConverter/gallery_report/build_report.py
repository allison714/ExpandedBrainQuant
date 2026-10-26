"""Build the pan-ExM 2026 gallery report as a PDF."""
import csv, sys, statistics as st
from datetime import date
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

HERE = Path(__file__).parent
FIG = HERE / "fig"
ROOT = Path(r"C:\Users\admin\OneDrive - Yale University\09_Data & Imaging\Images\2026")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\admin\OneDrive\Desktop\pan-ExM 2026 Gallery Report.pdf")

pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", r"C:\Windows\Fonts\arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Italic", r"C:\Windows\Fonts\ariali.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-Bold")

INK, DIM, LINE, PANEL, ACCENT, DOWN, UP = colors.HexColor("#1b1f24"), colors.HexColor("#5b6470"), colors.HexColor("#d9dee5"), colors.HexColor("#f3f5f8"), colors.HexColor("#0b5cad"), colors.HexColor("#b3261e"), colors.HexColor("#1a6f3c")
W, H = letter
MARGIN = 0.8 * inch
TEXT_W = W - 2 * MARGIN

S = {
    "title": ParagraphStyle("title", fontName="Arial-Bold", fontSize=22, leading=27, textColor=INK, spaceAfter=6),
    "sub": ParagraphStyle("sub", fontName="Arial", fontSize=10.5, leading=14, textColor=DIM, spaceAfter=14),
    "h1": ParagraphStyle("h1", fontName="Arial-Bold", fontSize=15, leading=19, textColor=INK, spaceBefore=14, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName="Arial-Bold", fontSize=11.5, leading=15, textColor=INK, spaceBefore=10, spaceAfter=4),
    "body": ParagraphStyle("body", fontName="Arial", fontSize=9.6, leading=13.4, textColor=INK, spaceAfter=5),
    "bullet": ParagraphStyle("bullet", fontName="Arial", fontSize=9.6, leading=13.4, textColor=INK, leftIndent=12, bulletIndent=2, spaceAfter=2.5),
    "small": ParagraphStyle("small", fontName="Arial", fontSize=8, leading=10.5, textColor=DIM, spaceAfter=4),
    "cap": ParagraphStyle("cap", fontName="Arial", fontSize=8.2, leading=10.8, textColor=DIM, spaceBefore=3, spaceAfter=10),
    "cell": ParagraphStyle("cell", fontName="Arial", fontSize=7.6, leading=9.6, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Arial-Bold", fontSize=7.6, leading=9.6, textColor=INK),
    "box": ParagraphStyle("box", fontName="Arial", fontSize=9.4, leading=13, textColor=INK, leftIndent=10, bulletIndent=0, spaceAfter=3),
}
P = lambda t, s="body": Paragraph(t, S[s])
B = lambda t: Paragraph(t, S["bullet"], bulletText="•")


def figure(name, caption, width=TEXT_W, max_h=7.2 * inch):
    path = FIG / name
    im = PILImage.open(path); w, h = im.size
    scale = min(width / w, max_h / h)
    return KeepTogether([Image(str(path), width=w * scale, height=h * scale), Paragraph(caption, S["cap"])])


def table(heads, rows, widths=None, bold_first=False, zebra=True, size=7.6):
    cs = ParagraphStyle("c", parent=S["cell"], fontSize=size, leading=size + 2)
    cb = ParagraphStyle("cb", parent=S["cellb"], fontSize=size, leading=size + 2)
    data = [[Paragraph(str(h), cb) for h in heads]]
    for r in rows:
        data.append([Paragraph("" if c is None else str(c), cb if (bold_first and i == 0) else cs) for i, c in enumerate(r)])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [("BACKGROUND", (0, 0), (-1, 0), PANEL), ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE), ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if zebra:
        for i in range(2, len(data), 2):
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafbfc")))
    t.setStyle(TableStyle(style))
    return t


def box(title, items):
    inner = [Paragraph(f"<b>{title}</b>", S["body"])] + [Paragraph(f"• {i}", S["box"]) for i in items]
    t = Table([[inner]], colWidths=[TEXT_W], hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


f = lambda v: None if v in (None, "", "None") else float(v)
num = lambda v, d=1: "–" if f(v) is None else f"{f(v):.{d}f}"

# ------------------------------------------------------------------ data
summary = list(csv.DictReader(open(HERE / "synapse_summary.csv", encoding="utf-8")))
inventory = [d for d in csv.DictReader(open(HERE / "image_list_images.csv", encoding="utf-8")) if not any(k in d["Image Name"].lower() for k in ("pff", "tetraspec"))]
single = list(csv.DictReader(open(ROOT / "Zarr Comparisons/Single Label/Single Label - Per Antibody.csv", encoding="utf-8-sig")))
multi = list(csv.DictReader(open(ROOT / "Zarr Comparisons/Multi Label/Multi Label - Per Antibody.csv", encoding="utf-8-sig")))
morph = list(csv.DictReader(open(ROOT / "Zarr Comparisons/WT vs KO/Morphology - Comparison.csv", encoding="utf-8-sig")))
szac = list(csv.DictReader(open(ROOT / "Zarr Comparisons/SZ vs AC/Comparison.csv", encoding="utf-8-sig")))
wtko = list(csv.DictReader(open(ROOT / "Zarr Comparisons/WT vs KO/Comparison.csv", encoding="utf-8-sig")))
bleed = list(csv.DictReader(open(ROOT / "Zarr Comparisons/Double Label/Double Label - Bleed-through.csv", encoding="utf-8-sig")))
dleff = list(csv.DictReader(open(ROOT / "Zarr Comparisons/Double Label/Double Label - Efficiency.csv", encoding="utf-8-sig")))
chan = list(csv.DictReader(open(ROOT / "Zarr Comparisons/Channel Check - Channels.csv", encoding="utf-8-sig")))
found_total = sum(int(r["Synapses found"] or 0) for r in summary)
abpos_total = sum(int(r["AB+"] or 0) for r in summary)
n_vox = sum(float(d["Voxels"] or 0) for d in inventory) / 1e9
n_vol = sum(float(d["Volume (µm³)"] or 0) for d in inventory) / 1e9
n_pan = sum("(pan)" in str(d["Channels (laser)"]) for d in inventory)
dark = [(r["Sample"], r["Channel"]) for r in chan if r["Role"] == "antibody" and r["Status"].startswith("DARK")]
sparse = [(r["Sample"], r["Channel"]) for r in chan if r["Role"] == "antibody" and r["Status"] == "sparse"]
weak_pan = [r["Sample"] for r in chan if r["Role"] == "pan" and r["Status"] == "weak pan stain"]

story = []
# ------------------------------------------------------------------ title
story += [Spacer(1, 0.6 * inch), P("pan-ExM 2026: what the gallery holds", "title"),
          P(f"A summary of every image, measurement and comparison in <b>X:\\nginx_share\\exm\\2026\\gallery</b> (Zarr Screenshots, Zarr Synapse ExF, Zarr Comparisons) "
            f"and the analysis folders it mirrors. Kuan Lab, Yale. Written {date.today():%d %B %Y}. All numbers are automatic and unreviewed unless a section says otherwise.", "sub")]
story.append(box("In short", [
    f"<b>{len(inventory)} images</b> on the server (February to September 2026, {n_vox:.0f} gigavoxels, {n_vol:.1f} mm³ as imaged), plus two stitched pan-ASLM volumes. "
    f"{n_pan} of the images carry a pan (total-protein) stain; the rest are antibody-only screens.",
    f"<b>Expansion factor:</b> {found_total:,} synapse profiles were found in the pan stain across 51 samples, {abpos_total:,} of them confirmed by an antibody. "
    "The expansion factor (DP–PSD distance ÷ 81.9 nm) reads <b>16.3 to 23.2 depending on the gel</b>: about 20 for the 31 August gels, 22 for 16 June, 23 for 10 September, and 21.7 ± 1.6 for the pan-ASLM volume. "
    "Within a day's gels the spread is small (s.d. across fields 0.5 to 1.8), so the ruler is steady; between gels it is not.",
    "<b>Synapse ultrastructure</b> is consistent across all 45 measured samples: PSD length 118 nm (76–160), PSD thickness 50 nm with blur, DP–DP spacing 63 nm (paper: 67), "
    "PSD-to-cleft 46 nm and DP-to-cleft 36 nm, 1.3% perforated. The DP–DP spacing gives an independent expansion factor of 19.4, within 5% of the DP–PSD one. No expansion anisotropy was found (median 3%, all p > 0.05).",
    "<b>Antibody efficiency</b> (share of pan-found synapses carrying the antibody, beyond chance): GluA1 rabbit 52–56%, the 16 June NR1/Homer1 pair 43–48%, the 10 September pair 53% and 82%, "
    "GluA3 rabbit 42%, the mouse antibodies 6–14%. Cx43 is not synaptic (1.4 × chance). mGluR5 mouse showed no signal.",
    "<b>Two imaging sets failed in the antibody channel:</b> the AC re-imaging of the SZ gels (29 April; 637 channel reads as a dark frame in all five samples) and the 22 July triple-label set (weak pan, almost no synapses found).",
    "<b>Double labels:</b> 561 → 637 bleed-through is excluded (≤ 1–5%). 637 → 561 cannot be ruled out from the images alone on 16 June and 10 September; no single-label control exists to settle it.",
    "<b>Shank2 KO against WT (31 August, 5 + 4 animals):</b> no difference in any pan-stain morphology measure (KO/WT 0.96–1.02, all p ≥ 0.37; detectable effect ≈ 15–20%). "
    "In the antibody channel Cx43 was 0.19 × WT in the one KO animal; GluA1 and GluA3 were not reduced.",
    "<b>pan-ASLM:</b> the 4 × 4 × 9520 stitched volume (1.9 mm deep) shows no doubled structures at any depth, but visible brightness seams and very low signal; synapses are found throughout, fewest at the shallow and deep ends. "
    "A training set of 8,000 best + 2,000 questionable synapse patches was cut from it and awaits review.",
    "<b>Not yet done:</b> no synapse has been accepted or rejected by a person in the main log (0 of 2,716), no screenshot has a rating or note (0 of 1,050), and the channel table still says TO DO for which of NR1 and Homer1 sits on 561 and 637.",
]))
story.append(PageBreak())

# ------------------------------------------------------------------ 1 inventory
story += [P("1. What is in the gallery", "h1"),
          P("The gallery is a full copy, kept with <i>make_gallery.py --mirror</i>, of three analysis folders. Everything is produced from the OME-Zarr images on <b>X:\\nginx_share\\exm\\2026\\zarr</b> by the tools in <i>Desktop\\ZarrBatchConverter</i>.")]
story.append(table(["Folder", "Files", "Size", "What it holds"], [
    ["Zarr Screenshots", "8,339", "4.0 GB", "One folder per image with 10–12 standard Neuroglancer views (overview, planes at 5/25/50/75/95% depth, 200% zooms, XY/XZ/YZ projections, 3D volume), the Zarr Screenshot Log, and the one-file 2026 AC Gallery (HTML)."],
    ["Zarr Synapse ExF", "4,951", "1.9–2.0 GB", "Synapse Log.xlsx (2,716 logged synapses, 51-sample Summary, Method sheet); per sample a CSV of every synapse found, a 300-dpi card per logged synapse, a contact sheet and a structure figure; nuclei measurements; the pan-ASLM log and training set."],
    ["Zarr Comparisons", "24 on the server, 120 locally", "5–18 MB", "Channel Check, AB Metrics, SZ vs AC, WT vs KO (antibody and morphology), Double Label, Single Label, Multi Label, Stitching critique, Antibody Report, and a NOT VALIDATED mitochondria trial."],
], widths=[1.15 * inch, 0.9 * inch, 0.8 * inch, TEXT_W - 2.85 * inch]))
story.append(P("The server copy of Zarr Comparisons dates from 8 October; the Single Label, Multi Label, Morphology and Mitochondria results added since then exist only in the OneDrive folder until the mirror is run again.", "small"))
story.append(figure("chart_inventory.png", "Figure 1. Images on the server by acquisition day. Grey: antibody-only screens without a pan stain (SZ, February; most of May). Blue and green: images with a pan stain, which is what every synapse measurement rests on."))
story.append(P("1.1 The imaging sets", "h2"))
story.append(table(["Set (day)", "Images", "Microscope, objective", "Channels", "What it is", "Usable for"], [
    ["SZ screen (24 Feb)", "36", "Dragonfly, 40×/1.15, 151 nm px", "637 only (+ DAPI in stacks)", "Single-label antibody screen: Homer1 chicken and rabbit, NR1, Shank2, mGluR5, Bassoon; three no-primary controls. 3×3 montages (one plane) and surface-to-surface stacks.", "Puncta density and brightness per antibody; the control floor. No pan stain, so no synapses."],
    ["AC re-imaging (29 Apr)", "6", "Dragonfly, 40×/1.15", "637 CF25 + 488 CF40 (pan)", "The same gels re-imaged nine weeks later after pan-staining, with a different channel preset.", "Pan-only synapse finding (pan is good). Antibody channel is a dark frame."],
    ["May NR1/Homer1 (20–26 May)", "11", "Dragonfly, 60×/1.2, 100 nm px", "637 + 561 (+ 488 on 26 May)", "Double-label trials, 0.3 µm z steps, large montages (up to 1,003 planes).", "Bleed-through test (sample 6 is clean both ways). Pan weak or absent, so no synapses."],
    ["June NR1/Homer1 (16 Jun)", "12", "Dragonfly, 60×/1.2", "561 + 637 + 488 pan", "Twelve single-field stacks, 0.5–1 µm steps; the 488 pan was on a second camera (mirrored, rotated) and is registered in software.", "ExF, structure, both antibodies' efficiency, bleed-through."],
    ["July (22 Jul)", "6", "Dragonfly, 60×/1.2", "488 pan + 561/637 (+ 790)", "NR1/Homer1 double labels and one triple label with mGluR5-AF790 (790 not in the file).", "Little: weak pan, 0–18 synapses per sample."],
    ["WT vs Shank2 KO (31 Aug)", "26", "Dragonfly, 40×/1.15 (two at 60×)", "488 pan + 637", "Six WT and four KO animals, one antibody each: Cx43, GluA1 rabbit, GluA2 mouse, GluA3 mouse and rabbit, mGluR5 mouse. 3×3 overviews and surface-to-surface stacks.", "ExF, structure, single-label efficiency, genotype comparison."],
    ["AB testing 2 (10 Sep)", "2", "Nikon, 40× WI", "~488 pan + ~561 + ~640", "Two double-label stacks on a different microscope.", "ExF, efficiency; bleed-through suspected."],
    ["pan-ASLM (Apr–Jun)", "3 volumes", "pan-ASLM light sheet, 203 nm px", "pan only", "Stitched 4×4 tile volumes: a 276-plane toy, the full 4×4×9520 (11,363 × 11,362 × 9,521 voxels, 1.9 mm deep), and a 5×5 ROI with no stitched output.", "Stitching quality, deep-tissue synapses, training data."],
], widths=[1.05 * inch, 0.5 * inch, 1.05 * inch, 1.0 * inch, 2.1 * inch, TEXT_W - 5.7 * inch], size=7.2))
story.append(P("Five PFF samples and two bead (tetraspec) images on the server are left out of every analysis and of the gallery by standing instruction.", "small"))

# ------------------------------------------------------------------ 2 the images
story += [PageBreak(), P("2. The images themselves", "h1"),
          P("Every image has a fixed set of Neuroglancer views saved as screenshots, so a sample can be judged without opening the 100-gigapixel data. "
            "<b>The qualitative record is incomplete:</b> the Zarr Screenshot Log has a Rating and a Notes column for each of its 1,050 screenshots and none has been filled in. What follows is what the automatic channel check and a look at the views say.")]
story.append(P("2.1 Channel health check", "h2"))
story.append(P(f"<i>channel_check.py</i> looked at every channel of 98 samples (187 channels). Antibody channels: 92 with signal, {len(sparse)} sparse, {len(dark)} dark (nothing detected). Pan channels: 44 with signal, {len(weak_pan)} weak. "
               "Of 218 channel pairs tested for one channel showing up in the other, none met the 'bleed-through likely' rule; 9 were 'possible bleed-through, or the two label the same places' (all 637 → 561 on 16 June or 10 September), and 55 had too few spots to test."))
story.append(table(["Dark antibody channels", "Weak pan stain"], [
    [", ".join(sorted({s.replace("_FusionStitcher", "") for s, _ in dark})), ", ".join(sorted(s.replace("_FusionStitcher", "") for s in weak_pan))]], widths=[TEXT_W * 0.55, TEXT_W * 0.45], size=7))
story.append(P("The dark channels fall into two groups: the five AC samples (the whole 637 channel), and the 561 or 637 channel of several May and July double labels where only one of the two antibodies worked.", "small"))
story.append(figure("shot_WT4_overview.jpg", "Figure 2. A 31 August 3×3 overview (260831_WT4_GluA2ms, pan in grey, GluA2 in magenta; 100 µm bar as imaged, about 6 µm of tissue at ExF 16.3). "
                    "Cell bodies are the dark round gaps, with the neuropil as a dense texture of membranes between them; blood vessels appear as bright-walled tubes. The nine tiles are visible as faint brightness steps, as in all FusionStitcher montages.", width=4.2 * inch))
story.append(figure("shot_0616_12_zoom.jpg", "Figure 3. A 200% zoom from 16 June (260616_NR1_homer1_12, mid-stack; 10 µm bar as imaged ≈ 0.46 µm of tissue). Pan grey, 637 magenta, 561 green. "
                    "Dendrites run as parallel bright-walled tubes; the brightest short bars inside the neuropil are postsynaptic densities, several of them carrying both antibody colours. Both antibody channels are dim and speckled, a typical spot being 8–10 noise-widths.", width=4.2 * inch))
story.append(figure("shot_0616_12_xz.jpg", "Figure 4. The same stack seen from the side (XZ projection, 20 µm bar). The gel face is at the top; labelling and pan signal fade with depth, which is why the Summary reports antibody labelling near the faces and at the centre separately.", width=4.6 * inch))

# ------------------------------------------------------------------ 3 ExF
story += [PageBreak(), P("3. Expansion factor", "h1"),
          P("<b>Method, in brief.</b> Following M'Saad, Cairns et al. (Nat. Biotechnol. 2025), the expansion factor is the centre-to-centre distance between the presynaptic dense projections (DP) and the postsynaptic density (PSD), read peak to peak on a 2-pixel line across the synapse, divided by 81.9 nm. "
            "The program finds candidate PSDs in the pan channel with a ridge filter, fits two Gaussians to the profile across each to decide whether it is a side-on synapse, requires the brighter density to outshine 99.5% of the plane (this removes pairs of membranes, which otherwise pile up at 26–30×), and then reads the distance. "
            "The quoted ExF is the mean ± s.d. across fields of view, as in the paper, with a bootstrap 95% interval. Antibody-positive synapses (≥ 6 noise-widths) are trusted; pan-only double peaks read high and are marked as such. The full method is on the Method sheet of the Synapse Log.")]
story.append(figure("chart_exf.png", "Figure 5. Expansion factor of every sample with at least 30 synapses. Circles rest on antibody-positive synapses; squares are pan-only double peaks, which read high. "
                    "Gels differ: the GluA2 gel (WT4) expanded 16.3–16.7×, the GluA1 gels about 20.5×, the 16 June gels 21–23×, the AC gels 22× and the 10 September gels 23×."))
story.append(P("3.1 What the numbers say", "h2"))
for t in [
    "<b>Expansion is set per gel, not per animal or antibody.</b> On 31 August the WT4 gel reads 16.3 and 16.7 in its two images, while the WT2 gel reads 20.1, 20.8 and 20.3. The spread within a gel's own fields of view is 0.5–1.8, three to ten times smaller than between gels.",
    "<b>Two rulers agree.</b> The spacing between neighbouring dense projections, divided by the paper's 67 nm, gives a second ExF of 19.4 (median over samples) against 20.5 from DP–PSD: the two independent structures scale together.",
    "<b>No anisotropy.</b> Fitting ExF against the orientation of each synapse in the image gives an amplitude of 3% (median; largest 7%), never significant: the gels expanded the same in x and y.",
    "<b>Reading methods.</b> Nine ways of reading the distance were compared on 88 GluA2 synapses by split-half repeatability (Fit options (GluA2).csv). Peak-to-peak with parabolic refinement was the most repeatable (7.7% of the distance) and gives 16.6; two-Gaussian fits give 18.2, about 10% higher, and are kept as a second column.",
    "<b>A third ruler from nuclei</b> was tried on three stacks (Nucleus Sizes.csv): one complete nucleus each in the WT4 and WT6 stacks implies a pre-expansion diameter of 7.3 and 5.3–5.6 µm with the synapse ExF. Without nuclei measured before expansion this is a plausibility check only.",
    "<b>Window edges.</b> The second peak is searched for at 12–30×. 'Found outside 16–24' is 21–50% per sample because the unreviewed pan-only peaks include membrane pairs; among antibody-positive synapses the ExF at the window edge is 0–3%.",
]: story.append(B(t))
story.append(P("3.2 Per sample", "h2"))
rows = []
for r in sorted(summary, key=lambda r: r["Sample"]):
    if int(r["Synapses found"] or 0) < 30 or r["Sample"].endswith("_F0"): continue
    basis = "AB+" if r["ExF Basis"].startswith("antibody") else "accepted" if r["ExF Basis"].startswith("accepted") else "pan only"
    rows.append([r["Sample"].replace("_FusionStitcher", "").replace("260831_", "0831_").replace("260616_", "0616_"), r["Antibody"], r["Planes"], r["Synapses found"], r["AB+"],
                 f"{num(r['ExF'])} ± {num(r['ExF s.d.'])}", r["ExF 95% CI"], basis, num(r["ExF (Gaussian fit)"]), r["DP–DP (nm, corrected)"], num(r["ExF from DP–DP (÷67 nm)"]), num(r["ExF Anisotropy (%)"], 0)])
story.append(table(["Sample", "Antibody", "Pl.", "Found", "AB+", "ExF ± s.d. (fields)", "95% CI", "Basis", "Gauss. fit", "DP–DP (nm)", "ExF from DP–DP", "Aniso. %"], rows,
                   widths=[1.5 * inch, 0.6 * inch, 0.38 * inch, 0.4 * inch, 0.35 * inch, 0.72 * inch, 0.6 * inch, 0.5 * inch, 0.42 * inch, 0.55 * inch, 0.45 * inch, TEXT_W - 6.47 * inch], size=6.4))
story.append(P("Samples with fewer than 30 synapses (260526_pan_2, the four 22 July samples) and the single-tile duplicates (_F0) are left out. The AC values are pan-only and reproduce the 16 June range because the pan stain is good; their antibody channel is dark.", "small"))

# ------------------------------------------------------------------ 4 structure
story += [PageBreak(), P("4. Synapse ultrastructure from the pan stain", "h1"),
          P("On the synapses the expansion factor rests on, the program also measures the PSD along and across its length, the spacing of the dense projections, the position of the cleft, and a provisional class after the paper's Fig. 2 (Type I macular, Type I perforated, Type II putative). "
            "Sizes are in nm of tissue, each sample on its own ExF. The numbers below are medians across the 45 samples with 30 or more synapses, with the range of sample means in brackets.")]
story.append(table(["Measure", "Across samples", "Paper (M'Saad 2025)", "Note"], [
    ["PSD length (above half height)", "118 nm (76–160)", "—", "Cut-off lengths left out; reads short of EM (~300 nm) because it is one 2D profile at half height."],
    ["PSD thickness (FWHM across)", "50 nm (42–60)", "—", "Includes the optical blur: an upper bound."],
    ["DP–DP spacing", "63 nm (55–80)", "67 ± 15 nm", "Needs the DP row told from the PSD, right about nine times in ten."],
    ["PSD peak to cleft", "46 nm (44–50)", "—", "The cleft is the darkest point between the peaks; far narrower than resolved."],
    ["DP peak to cleft", "36 nm (34–38)", "—", "PSD + DP to cleft = 82 nm, the DP–PSD distance by construction."],
    ["Perforated PSDs", "1.3% (0–5%)", "classified by eye", "A perforation shows only when the plane cuts the hole: under-counted."],
    ["Type II (putative)", "0–26% per sample", "classified by eye", "About half the calls are expected to be wrong; highest in pan-only samples."],
    ["Antibody position across the synapse", "+2 to +29 nm from the PSD peak towards the DPs (postsynaptic antibodies)", "—", "GluA2: 5 ± 20 nm; GluA1: 15–25 nm; 16 June 637 channel: 2–20 nm. All on the PSD side of the cleft."],
    ["Resolution (smallest antibody puncta, FWHM)", "426 nm as imaged (median), 16–29 nm corrected", "290 ± 60 nm as imaged", "Puncta are receptor clusters, wider than the optics: an upper bound."],
], widths=[1.7 * inch, 1.5 * inch, 1.1 * inch, TEXT_W - 4.3 * inch]))
story.append(figure("struct_WT4.jpg", "Figure 6. The structure figure of one sample (260831_WT4_GluA2ms 3×3): ExF against synapse orientation (no anisotropy), ExF per field of view, DP–DP spacing with the paper's 67 nm in blue, "
                    "the antibody's position across the synapse (GluA2 sits on the PSD peak), and the PSD length distribution. Every sample has one of these."))
story.append(figure("card_0616_12.jpg", "Figure 7. One synapse card (260616_NR1_homer1_12, synapse 1): pan, antibody 1, antibody 2, overlay with the measured axis, and the profile with the two peaks, the two-Gaussian fit (blue) and both antibodies (dashed). 2,716 such cards are in the log; 4,885 pictures in all."))
story.append(figure("sheet_WT4.jpg", "Figure 8. A contact sheet: the 60 logged synapses of one sample, antibody-positive first, with number, ExF and class above each tile and a 2 µm (as imaged) bar on each. These are the pictures meant for publication panels.", max_h=4.8 * inch))

# ------------------------------------------------------------------ 5 antibody efficiency
story += [PageBreak(), P("5. Antibody labelling efficiency", "h1"),
          P("<b>Method.</b> Synapses are found in the pan stain without the antibody. A synapse is labelled when the brightest antibody pixel in its footprint is 6 noise-widths above the frame's median. "
            "The same footprint moved about 400 nm of tissue away in four directions gives the share that would read as labelled by chance; efficiency is the share labelled beyond that chance. "
            "It is a lower bound: some pan-found double peaks are membrane pairs that no antibody should label. Intervals are bootstraps over fields of view; where an antibody was imaged in one animal they say how steady the number is across that tissue, not across animals.")]
story.append(figure("chart_efficiency.png", "Figure 9. Labelling efficiency of every antibody channel in a sample with a pan stain, pooled by antibody and day. Double-label channels are measured one at a time with the same rule."))
rows = []
for r in sorted(single + multi, key=lambda r: -f(r["Efficiency (%)"])):
    if r["Synapses"] in ("", None): continue
    rows.append([r["Antibody"], r.get("Set", ""), r["Imaged"], r["Samples"], r["Synapses"], num(r["Labelled (%)"]), num(r["By Chance (%)"]), f"{num(r['Efficiency (%)'], 0)} ({num(r['Efficiency Low'], 0)}–{num(r['Efficiency High'], 0)})",
                 num(r["Efficiency, Clearest Third (%)"], 0), r["Enrichment (× chance)"] or "–", r["Signal at Labelled Synapses (noise-widths)"] or "–"])
story.append(table(["Antibody / channel", "Set", "Imaged", "Smp.", "Syn.", "Labelled %", "By chance %", "Efficiency % (95%)", "Clearest third %", "Enrichment ×", "Signal (σ)"], rows,
                   widths=[1.3 * inch, 0.33 * inch, 0.45 * inch, 0.48 * inch, 0.52 * inch, 0.55 * inch, 0.58 * inch, 0.9 * inch, 0.6 * inch, 0.55 * inch, TEXT_W - 6.26 * inch], size=6.6))
story.append(P("5.1 Reading it", "h2"))
for t in [
    "<b>Rabbit beats mouse on the same target:</b> GluA3 rabbit labels 42% of synapses, GluA3 mouse 6–10%. GluA1 rabbit, at 52–56%, is the most efficient single-label antibody in hand.",
    "<b>The 16 June pair</b> label 48% (561) and 43% (637) of pan-found synapses, and 72–75% of the synapses the other antibody confirms (section 6), which is the better estimate of their true efficiency.",
    "<b>The 10 September 561 channel</b> reaches 82%, the highest in the dataset, but 99–100% of its 640-positive synapses are also 561-positive, the same observation as the bleed-through flag: do not quote it yet.",
    "<b>Cx43</b> is at synapses 1.4 times more often than at decoy positions (8–23 times for the synaptic antibodies): it behaves as the astrocytic, non-synaptic protein it is, and is absent in the KO animal.",
    "<b>mGluR5 mouse</b> gave no signal in the one animal imaged (KO3). <b>The AC set</b> reads 0% for all five antibodies because the channel is dark; this says nothing about the antibodies.",
    "<b>Samples of one antibody vary a lot:</b> GluA3 rabbit 31% and 59% in its two samples, GluA3 mouse KO 23% and 1%. Differences between antibodies smaller than that spread should not be trusted.",
    "<b>Not measurable this way:</b> the whole SZ screen (no pan stain), most May samples, and the 22 July set (too few synapses). For SZ the only numbers are puncta density and brightness against the no-primary controls (section 7).",
]: story.append(B(t))

# ------------------------------------------------------------------ 6 double label
story += [PageBreak(), P("6. Double labels: bleed-through and co-labelling", "h1"),
          P("Twenty-five samples carry two antibody channels (561 and 637/640). Bleed-through would be a fixed share of one channel's light arriving in the other, so at every bright source spot the target must show at least that share. "
            "The test fits a line under the cloud of (source, target) spot pairs (10th-percentile quantile regression) and reports its slope as the most bleed-through there could be. 'Not ruled out' is not proof: two antibodies on the same synapses look the same.")]
rows = []
for day, label in (("260520", "20 May"), ("260526", "26 May"), ("260616", "16 Jun"), ("260722", "22 Jul"), ("260910", "10 Sep")):
    cells = [label, sum(e["Sample"].startswith(day) for e in dleff)]
    for direction in ("AB2 into AB1", "AB1 into AB2"):
        words = {}
        for b in bleed:
            if b["Sample"].startswith(day) and b["Direction"] == direction and f(b.get("Bleed-Through At Most (%)")) is not None:
                w = b["Verdict"].split(":")[0].lower(); words[w] = words.get(w, 0) + 1
        cells.append("; ".join(f"{n} {w}" for w, n in sorted(words.items(), key=lambda kv: -kv[1])) or "not testable")
    rows.append(cells)
story.append(table(["Imaged", "Samples", "561 into 637 (AB2 into AB1)", "637 into 561 (AB1 into AB2)"], rows, widths=[0.7 * inch, 0.6 * inch, 2.6 * inch, TEXT_W - 3.9 * inch]))
for t in [
    "<b>561 into 637 is clean</b> wherever it could be tested: at most 1–5% of the source on 20 May, 16 June and 22 July, and about half of the bright 561 spots have no 637 at all.",
    "<b>637 into 561 is open on 16 June</b> (7 of 12 samples 'not ruled out', the rest inconclusive). Against optical bleed-through: on 20 May sample 6, with the same lasers, 82% of 2,417 bright 637 spots had no 561; and on 16 June 14–33% of 637-positive synapses have no 561 label, which bleed-through would not allow. What remains possible is two antibodies on the same synapses, or a secondary that binds both primaries.",
    "<b>10 September is the set to worry about:</b> both directions read 'not ruled out' (11–17% for 561 → 640) and only 1 synapse in 230 carries the 640 label without the 561 label.",
    "<b>Co-labelling on 16 June:</b> across 1,120 pan-found synapses, 'both' exceeds 'both by chance' in every sample (e.g. 38% against 27% in sample 12). At synapses confirmed by one antibody, the other is present in 72% (637 at 561-positive) and 75% (561 at 637-positive), median over 12 samples.",
    "<b>Only a single-label control</b> (one antibody, both channels imaged at the usual settings) measures bleed-through directly; none of these sets has one. A fuller write-up is in <i>Double Label Report.html</i> on the Desktop.",
]: story.append(B(t))
story.append(figure("dl_summary.jpg", "Figure 10. Left: the most bleed-through each sample could have, each way (log scale; open markers 'not ruled out'). Right: each antibody's efficiency at synapses confirmed by the other, with 95% intervals.", max_h=5.2 * inch))
story.append(figure("dl_0616_12.jpg", "Figure 11. One sample's test (16 June, sample 12). Middle: 561 → 637 is flat (none). Left: the least 561 seen rises with 637 (not ruled out). Right: how the pan-found synapses divide between the two labels."))

# ------------------------------------------------------------------ 7 SZ vs AC and WT vs KO antibody
story += [PageBreak(), P("7. Antibody comparisons in the antibody channel alone", "h1"), P("7.1 SZ against AC: the same gels, nine weeks apart", "h2"),
          P("SZ imaged the single-label screen on 24 February before pan-staining; AC re-imaged the same gels on 29 April after the pan stain was added, with a different channel preset (637_CF25 instead of 637_Cy5_CF40). "
            "Puncta (3+ connected pixels ≥ 6 noise-widths above the field median) per 1,000 µm² as imaged, mean ± s.d. over fields:")]
story.append(table(["Antibody", "SZ (24 Feb)", "AC (29 Apr)", "No-primary controls (SZ)", "Verdict"], [[r["Antibody"], r["Puncta / 1000 µm² (as imaged): SZ"], r["Puncta / 1000 µm² (as imaged): AC"], r["Control Floor (puncta / 1000 µm²)"], r["Puncta / 1000 µm² (as imaged): Verdict"].lower()] for r in szac],
                   widths=[1.2 * inch, 1.5 * inch, 1.5 * inch, 1.4 * inch, TEXT_W - 5.6 * inch]))
for t in [
    "In every AC image the 637 channel holds nothing: fewer puncta than the no-primary controls, with background 110.0 counts and noise 1.53 counts identical in all five samples. It reads as a dark frame. The 488 pan channel of the same files is normal.",
    "Either the label did not survive the pan-staining and nine weeks, or the channel was imaged with too little excitation; laser power and exposure are not in the files. To settle it: re-image one AC gel with SZ's preset; if the puncta return it was the imaging.",
    "The SZ screen itself ranks the antibodies by puncta density: Shank2 and Homer1 rabbit (~55–70 per 1,000 µm² as imaged) > Homer1 chicken ≈ NR1 (~45–60) > mGluR5 (~20) against a control floor of 0–4. Bassoon (SZ only) is densest at ~70–80. Punctum signal is 11–15 noise-widths for all.",
]: story.append(B(t))
story.append(figure("szac_examples.jpg", "Figure 12. The busiest field of each antibody, SZ above AC, background removed, the same brightness scale within each column. The AC row is noise."))
story.append(P("7.2 WT against Shank2 KO, antibody channel (31 August)", "h2"))
story.append(table(["Antibody", "WT puncta / 1000 µm²", "KO", "KO / WT (95%)", "p", "Verdict"], [[r["Antibody"], r["Puncta / 1000 µm² (as imaged): WT"], r["Puncta / 1000 µm² (as imaged): KO"], r["Puncta / 1000 µm² (as imaged): KO / WT"], r["Puncta / 1000 µm² (as imaged): p"], r["Puncta / 1000 µm² (as imaged): Verdict"].lower()] for r in wtko],
                   widths=[1.1 * inch, 1.4 * inch, 1.4 * inch, 1.1 * inch, 0.7 * inch, TEXT_W - 5.7 * inch]))
story.append(P("Cx43 is 0.19 × WT in puncta density and 0.05 × in labelled area in the knockout animal; GluA1 rabbit is 1.38 × higher in the KO and GluA3 mouse unchanged. Each antibody was imaged in one animal per genotype (Cx43: WT1 vs KO1b; GluA1: WT2 vs KO2; GluA3: WT6 vs KO6), so a gel-to-gel difference would look exactly like this; none of it is yet a genotype effect. All 31 August images share one channel preset, so imaging settings are less of a worry than for SZ vs AC."))
story.append(figure("wtko_examples.jpg", "Figure 13. Busiest field per antibody, WT above KO, same scale within a column. The Cx43 loss in the KO animal is visible by eye; GluA1 and GluA3 are not.", width=4.6 * inch))

# ------------------------------------------------------------------ 8 morphology
story += [PageBreak(), P("8. WT against Shank2 KO: synapse morphology", "h1"),
          P("The pan-stain measurements do not depend on which antibody an animal carries, so every animal counts once per genotype: 5 WT (WT1, 2, 4, 5, 6) and 4 KO (KO1b, 2, 3, 6), 5,342 synapses in 22 samples at 150 nm pixels. "
            "Per sample the median over synapses, per animal the mean of its samples, then an exact permutation test across animals. Lengths are on each sample's own DP–PSD scale, which assumes the DP–PSD distance itself is the same in both genotypes; the ExF row says it is.")]
story.append(table(["Measure", "WT (mean ± s.d. of animals)", "KO", "KO / WT", "Leaving one animal out", "p"], [[r["Measure"], r["WT (mean ± s.d. across animals)"], r["KO (mean ± s.d. across animals)"], num(r["KO / WT"], 2), r["Leaving One Animal Out"], num(r["p (permutation, animals)"], 2)] for r in morph],
                   widths=[2.2 * inch, 1.35 * inch, 1.05 * inch, 0.65 * inch, 1.1 * inch, TEXT_W - 6.35 * inch]))
for t in [
    "No measure differs: ratios 0.96–1.02 for the four length measures, all p ≥ 0.37. With 5 and 4 animals this could only have detected changes of roughly 15–20%.",
    "The one hint is at the large end: the 90th-percentile PSD length is 182 nm in WT and 167 nm in KO (0.92, p = 0.21) and the spread of PSD lengths is narrower in KO (CV 0.36 vs 0.40, p = 0.06). Not evidence yet.",
    "Why a real size difference could still be missed: the synapse finder selects PSDs by rank within each image (brightest 0.5%), so a global dimming or thinning would be normalised away; PSD length is a half-height stretch on one 2D profile (reads ~118 nm where EM gives ~300); brain region and layer were not recorded per field; everything is 2D at 40×.",
    "What would test it properly: absolute PSD brightness relative to the neuropil, synapse density with a fixed threshold rather than a per-image rank, and the planned full PSD segmentation in 3D.",
]: story.append(B(t))
story.append(figure("morph.jpg", "Figure 14. Each morphology measure per animal (large dots) and per sample (small grey), WT white and KO orange, with the mean across animals as a line.", max_h=4.6 * inch))

# ------------------------------------------------------------------ 9 pan-ASLM
story += [PageBreak(), P("9. pan-ASLM: stitching, synapses, training data", "h1"), P("9.1 Stitching quality", "h2"),
          P("<i>stitch_check.py</i> sampled the stitched volumes at full resolution along the overlap bands, at the tile corners and in tile interiors, looking for doubled structures (misregistration) and brightness steps.")]
for t in [
    "<b>260618_pan_aslm_4x4x9520 (the full volume):</b> 0 of 399 band and corner windows doubled, at seven depths from 95 µm to 1,809 µm; band doubling index 0.05–0.07 against 0.09 in tile interiors. The stitch does not degrade with depth and tiles are level in z (9,521 planes from 9,520-plane tiles).",
    "<b>Brightness seams:</b> the top-left tile is about 10 counts brighter than the rest (120 vs 110) and the overlap bands dip 2–4 counts, even though the stitcher's own normalisation was on. Bands and corners are 2–7% darker than interiors.",
    "<b>Very little signal:</b> most tissue is a few counts above a ~100-count offset; at 1,809 µm the mean falls to 103 and texture collapses. 'No doubling' there means 'nothing to double'.",
    "<b>260610 toy:</b> same picture, but 276 planes deep from 200-plane tiles, which the full volume shows is an artefact of how the toy was cut or stitched, not the microscope.",
    "<b>260409_local_nr_ROI_5x5x200:</b> no stitched output on the server, only seven shift files that are exactly affine (bin-2, rigid-preview settings); the tiles show a 0.3° camera rotation against the stage and about 0.4 µm of z drift per tile.",
    "<b>Recommended, in order:</b> keep settings, logs and goodness maps with every run; subtract the camera offset and flat-field tiles before stitching; match at full resolution for the final pass; finish the 5×5 run. Full text: <i>NR Stitching Critique.md</i>.",
]: story.append(B(t))
story.append(figure("stitch_full.jpg", "Figure 15. Stitching check of the full volume: mid-plane with overlap bands shaded and sampled windows as dots; mean brightness along x and y with the bright first tile and the band dips; the two 'most doubled' windows (both noise-dominated) and a typical interior.", max_h=4.6 * inch))
story.append(P("9.2 Synapses in the stitched volume", "h2"))
story.append(P("The same finder was run on 96 fields (16 tiles × 6 depths, 2,048-px each) of the full volume: <b>1,285 synapse profiles</b>, fewest near the gel faces (75 at 152 µm, 143 at 1,676 µm) and most in the middle (286–339 per depth). "
               "The user accepted all 60 logged synapses, giving <b>ExF = 21.7 ± 1.6</b> across 7 fields (95% CI 20.5–23.1; Gaussian fit 23.6; DP–DP 67 ± 18 nm, ExF from DP–DP 21.8). PSD length 122 ± 34 nm, thickness 63 ± 15 nm, 1.7% perforated, no anisotropy (2.1%, p = 0.79). "
               "At 203 nm pixels the corrected pixel is 9.4 nm, about twice the Dragonfly's 4.5–7.5 nm."))
story.append(figure("chart_aslm_depth.png", "Figure 16. pan-ASLM synapses by depth: counts per depth (left) and the per-field expansion factor (right; all double peaks, so it reads high)."))
story.append(figure("sheet_aslm.jpg", "Figure 17. The 60 accepted pan-ASLM synapses. Resolution is coarser than the confocal data but the two densities are clearly separated.", max_h=4.2 * inch))
story.append(P("9.3 Training set", "h2"))
story.append(P("From 1,400 sampled fields of the volume, <i>aslm_training.py</i> scored every candidate (without using the expansion factor) and exported <b>8,000 best</b> synapses that pass the normal gates and <b>2,000 questionable</b> ones drawn evenly across the score range, "
               "as 64 × 64 (2D) and 9 × 64 × 64 (3D) patches with PSD and DP points, plus a card per questionable synapse and <i>pan-ASLM Training Review.xlsx</i> for a person's Y/N. Review status: 1 of 2,000 decided. "
               "The low end of the 8,000 is of mixed quality. This is the starting material for the planned PSD/DP segmentation (2D first, then 3D, labels corrected in WebKnossos, run on the HPC)."))

# ------------------------------------------------------------------ 10 provisional / not validated
story += [P("10. Provisional, unvalidated and known problems", "h1")]
for t in [
    "<b>Nothing has been reviewed by a person</b> except the 60 pan-ASLM synapses: 0 of 2,716 logged synapses have an Accept decision, 0 of 1,050 screenshots have a rating or note, 1 of 2,000 training-review synapses is decided. Every ExF in section 3 is therefore the 'antibody-positive, unreviewed' or 'pan-only' kind.",
    "<b>Mitochondria (NOT VALIDATED):</b> the MAPS kidney model run on 260831_WT5_GluA3_large_1 reported 3,447 'mitochondria' (0.65 per µm³, median 0.039 µm³). On review (13 October) most instances were synaptic clefts or parts of nuclei. The numbers are kept in the folder with a NOT VALIDATED prefix and must not be quoted.",
    "<b>Synapse classes</b> are automatic; roughly half the Type II calls are expected to be wrong, and perforations are under-counted.",
    "<b>Which antibody is on which channel</b> is still TO DO in <i>_antibody_channels.csv</i> for every May and June NR1/Homer1 sample and for 10 September; those results are reported by channel, not by antibody.",
    "<b>Second-camera registration (16 June):</b> the 488 pan was on camera 2, mirrored and rotated; the transform was measured from the bead images (and the camera moved mid-session, so two transforms exist) and is applied in software by the tools and the Neuroglancer views. The raw zarrs are unchanged.",
    "<b>Laser power and exposure are not in any file.</b> Comparisons across imaging days (SZ vs AC, 16 June vs 31 August efficiencies) rest on settings having matched.",
    "<b>One animal per antibody per genotype</b> in the 31 August antibody comparison; the p-values there describe sampling within an animal.",
    "<b>Pan-only ExF reads high</b> (membrane pairs) and should not be quoted; it is in the tables for completeness and marked.",
    "<b>Server mirror is behind:</b> Zarr Comparisons on the server holds 24 files against 120 locally.",
]: story.append(B(t))

# ------------------------------------------------------------------ 11 next
story += [P("11. What would most improve the dataset", "h1")]
for t in [
    "<b>Review the synapses.</b> Mark Accept Y/N for the GluA1, GluA3 rabbit and 16 June samples; the quoted ExF then becomes 'accepted' and the efficiency denominators lose their membrane pairs.",
    "<b>Single-label controls imaged with both channels</b> on the 16 June and 10 September settings, to settle 637 → 561 bleed-through before any double-label result is quoted.",
    "<b>Re-image one AC gel with SZ's preset</b> to learn whether the February labels survived pan-staining.",
    "<b>Fill the channel table</b> (NR1 vs Homer1 on 561/637) so efficiencies read per antibody.",
    "<b>Record laser power, exposure, brain region and layer per field</b> from now on; the files do not carry them.",
    "<b>For the genotype question,</b> more animals (the current 5 + 4 detects ≈ 15–20% changes), fixed-threshold synapse density, absolute PSD brightness, and the 3D segmentation the training set was cut for.",
    "<b>pan-ASLM:</b> offset-subtract and flat-field tiles before stitching, keep the stitcher's logs, finish the 5×5.",
]: story.append(B(t))

# ------------------------------------------------------------------ 12 files
story += [P("12. Where everything is", "h1")]
story.append(table(["Result", "File (under the gallery / Images\\2026 folder)"], [
    ["Every synapse, ExF per sample, method", "Zarr Synapse ExF\\Synapse Log.xlsx (sheets Synapses, Summary, Method); per sample <sample>_synapses.csv, cards, contact sheet, structure figure"],
    ["Reading-method comparison; nuclei", "Zarr Synapse ExF\\Fit options (GluA2).csv / .png; Nucleus Sizes.csv"],
    ["pan-ASLM synapses and training set", "Zarr Synapse ExF\\pan-ASLM\\pan-ASLM Synapse Log.xlsx; 260618_pan_aslm_4x4x9520\\ (fields.csv, synapses.csv, Training Set\\); pan-ASLM Training Review.xlsx"],
    ["Channel health", "Zarr Comparisons\\Channel Check - Channels.csv, - Pairs.csv"],
    ["Puncta density, spacing, enrichment", "Zarr Comparisons\\AB Metrics\\AB Metrics.csv, AB Pairs.csv, AB Distributions (nm).csv"],
    ["SZ vs AC; WT vs KO (antibody)", "Zarr Comparisons\\SZ vs AC\\, WT vs KO\\ (Comparison.csv, Per Sample.csv, Imaging Settings.txt, figures); Antibody Report.html"],
    ["WT vs KO morphology", "Zarr Comparisons\\WT vs KO\\Morphology - Comparison / Per Animal / Per Sample.csv, Morphology.png"],
    ["Bleed-through and co-labelling", "Zarr Comparisons\\Double Label\\ (two CSVs, a figure per sample, Summary.png); Desktop\\Double Label Report.html"],
    ["Labelling efficiency", "Zarr Comparisons\\Single Label\\ and Multi Label\\ (Per Sample.csv, Per Antibody.csv, Efficiency.png)"],
    ["Stitching", "Zarr Comparisons\\Stitching\\NR Stitching Critique.md, per-volume Stitching Check.png and CSVs"],
    ["Screenshots and ratings", "Zarr Screenshots\\<sample>\\, Zarr Screenshot Log.xlsx, 2026 AC Gallery.html (also at neuroglancer.kuanlab.yale.edu/data/exm/2026/gallery/)"],
    ["Tools", "Desktop\\ZarrBatchConverter: synapse_exf.py, ab_efficiency.py, ab_metrics.py, channel_check.py, compare_ab.py, compare_morphology.py, double_label.py, single_label.py, aslm_synapses.py, aslm_training.py, stitch_check.py, make_gallery.py; QUICKSTART.txt"],
], widths=[1.9 * inch, TEXT_W - 1.9 * inch]))
story.append(P("Reference: M'Saad O., Cairns J., et al. pan-ExM-t, Nat. Biotechnol. 2025 (DP–PSD = 81.9 nm, DP–DP = 67 ± 15 nm, resolution 290 ± 60 nm as imaged).", "small"))


def on_page(canvas, doc):
    canvas.saveState(); canvas.setFont("Arial", 7.5); canvas.setFillColor(DIM)
    canvas.drawString(MARGIN, 0.5 * inch, "pan-ExM 2026: what the gallery holds  ·  Kuan Lab  ·  automatic measurements, unreviewed")
    canvas.drawRightString(W - MARGIN, 0.5 * inch, f"{doc.page}")
    canvas.restoreState()


doc = SimpleDocTemplate(str(OUT), pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=0.75 * inch, bottomMargin=0.8 * inch,
                        title="pan-ExM 2026: what the gallery holds", author="Kuan Lab", subject="Summary of the 2026 pan-ExM gallery")
doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print(OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")
