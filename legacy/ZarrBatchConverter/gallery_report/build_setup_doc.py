"""Build 'pan-ExM Analysis Setup.pdf': what the toolchain does, how it measures, how to run it."""
import sys
from datetime import date
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).parent
FIG = HERE / "fig"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\admin\OneDrive\Desktop\pan-ExM Analysis Setup.pdf")

pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", r"C:\Windows\Fonts\arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Italic", r"C:\Windows\Fonts\ariali.ttf"))
pdfmetrics.registerFont(TTFont("Mono", r"C:\Windows\Fonts\consola.ttf"))
registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-Bold")

INK, DIM, LINE, PANEL = colors.HexColor("#1b1f24"), colors.HexColor("#5b6470"), colors.HexColor("#d9dee5"), colors.HexColor("#f3f5f8")
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
    "code": ParagraphStyle("code", fontName="Mono", fontSize=8, leading=10.5, textColor=INK, backColor=PANEL, leftIndent=8, borderPadding=(4, 6, 4, 6), spaceBefore=3, spaceAfter=8),
    "box": ParagraphStyle("box", fontName="Arial", fontSize=9.4, leading=13, textColor=INK, leftIndent=10, spaceAfter=3),
}
P = lambda t, s="body": Paragraph(t, S[s])
B = lambda t: Paragraph(t, S["bullet"], bulletText="•")
CODE = lambda t: Paragraph(t.replace("\n", "<br/>").replace(" ", "&nbsp;"), S["code"])


def figure(name, caption, width=TEXT_W, max_h=7.0 * inch):
    path = FIG / name
    im = PILImage.open(path); w, h = im.size
    scale = min(width / w, max_h / h)
    return KeepTogether([Image(str(path), width=w * scale, height=h * scale), Paragraph(caption, S["cap"])])


def table(heads, rows, widths=None, size=7.6, bold_first=False):
    cs = ParagraphStyle("c", parent=S["cell"], fontSize=size, leading=size + 2)
    cb = ParagraphStyle("cb", parent=S["cellb"], fontSize=size, leading=size + 2)
    data = [[Paragraph(str(h), cb) for h in heads]]
    for r in rows:
        data.append([Paragraph("" if c is None else str(c), cb if (bold_first and i == 0) else cs) for i, c in enumerate(r)])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [("BACKGROUND", (0, 0), (-1, 0), PANEL), ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE), ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
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


story = []
# ---------------------------------------------------------------- title
story += [Spacer(1, 0.5 * inch), P("pan-ExM analysis setup: from raw images to expansion factor and synaptic quantification", "title"),
          P(f"What the Kuan Lab toolchain in <b>Desktop\\ZarrBatchConverter</b> does, how each measurement is made, what it writes where, and how to run it. "
            f"Kuan Lab, Yale · written {date.today():%d %B %Y} · covers the tools as they stand on this date (about 10,400 lines of Python in 20 scripts, 9 launchers).", "sub")]
story.append(box("What the setup can do", [
    "<b>Convert</b> any Bio-Formats-readable microscope file (Imaris, Nikon, Leica, Zeiss, TIFF) to OME-Zarr on the lab web server, with a one-click Neuroglancer link and a shared, auto-rebuilt Excel image list.",
    "<b>Document</b> every sample without opening it: standard views (planes, projections, 3D volume, zooms) with scale bars, a screenshot log with ratings and notes, and a browser gallery with filters, colour chips and the measured numbers.",
    "<b>Check channels</b> before trusting numbers: which channel is the pan stain, which antibody is on which laser, dark or sparse channels, a bleed-through screen, and software registration of a channel recorded on a second camera.",
    "<b>Measure the expansion factor</b> from synapses in the pan stain (DP–PSD distance ÷ 81.9 nm, after M'Saad, Cairns et al. 2025), with a reviewable log, 300-dpi pictures, a second ruler from DP–DP spacing, an anisotropy test and a nuclei cross-check.",
    "<b>Quantify synapse structure:</b> PSD length and thickness, dense-projection spacing, cleft position, provisional classes (type I macular / perforated, type II), per synapse and per sample.",
    "<b>Quantify antibodies at synapses:</b> share of synapses labelled (beyond chance), signal, enrichment over chance, penetration with depth, resolution from the smallest puncta; double labels get bleed-through limits and co-labelling; whole-image colocalisation (PCC, Manders, coverage) is also available.",
    "<b>Compare</b> sets of samples: antibody against antibody, imaging session against session, and genotype against genotype at the animal level with permutation tests.",
    "<b>pan-ASLM:</b> judge NRStitcher output (doubling, seams), find synapses in a 1.9-mm stitched light-sheet volume, and cut a 10,000-patch training set for segmentation.",
    "<b>Report:</b> HTML and PDF reports built from the result files, so every number in a report can be traced to a CSV.",
]))
story.append(P("Everything runs on a lab PC against the server share X:\\nginx_share; nothing is loaded whole (images are read as Dask arrays over the zarr chunks, plane by plane, tile by tile). The .bat launchers find Python (Anaconda) themselves and install the few packages they need on first use."))
story.append(PageBreak())

# ---------------------------------------------------------------- 1 pipeline
story += [P("1. How the pieces fit together", "h1"),
          P("The data flow has four layers: image data on the server, tools that read it, result folders they write, and viewing or reporting built from those results. Every tool reads the same <i>settings.ini</i> and the same channel decisions from <i>convert_to_zarr.py</i>, so the image list, the Neuroglancer views, the screenshots and the measurements agree on which channel is the pan stain and what colour each channel has.")]
story.append(figure("pipeline.png", "Figure 1. The toolchain. Solid grey arrows: a tool reads from the box above it. Light arrows: downstream analyses read the Synapse Log rather than the images. Green: what feeds the gallery and the reports."))
story.append(P("1.1 Folders and files", "h2"))
story.append(table(["Where", "What", "Who writes it"], [
    ["X:\\nginx_share\\exm\\2026\\zarr\\", "One <image>.zarr per image (OME-Zarr, multiscale group at /0/); _ng_states\\ (the JSON files the Neuroglancer links point to, including per-synapse views); _channel_registration.json; _antibody_channels.csv; _conversion_log.txt", "convert_to_zarr.py, channel_registration.py, antibody_channels.py"],
    ["X:\\nginx_share\\exm\\2026\\Zarr Image List.xlsx", "Shared, read-only metadata list, rebuilt on every conversion run (Images, Stitched and Legend tabs)", "convert_to_zarr.py"],
    ["X:\\nginx_share\\exm\\2026\\Stitched Precomputed NG\\", "pan-ASLM volumes in Neuroglancer precomputed format (listed and linked, never converted)", "the stitching pipeline on the HPC (Misha)"],
    ["OneDrive …\\Images\\2026\\Zarr Screenshots\\", "One folder of PNG views per sample; Zarr Screenshot Log.xlsx; 2026 AC Gallery.html and _gallery_thumbs\\", "roi_log.py, make_gallery.py"],
    ["OneDrive …\\Images\\2026\\Zarr Synapse ExF\\", "Synapse Log.xlsx; per sample <sample>_synapses.csv, synapse cards, contact sheet, structure figure, nuclei pictures; Fit options; Nucleus Sizes.csv; pan-ASLM\\ (its log, training set and review workbook)", "synapse_exf.py, nucleus_size.py, synapse_fit_options.py, aslm_*.py"],
    ["OneDrive …\\Images\\2026\\Zarr Comparisons\\", "Channel Check, AB Metrics, SZ vs AC, WT vs KO, Double Label, Single Label, Multi Label, Stitching, Antibody Report.html", "channel_check.py, ab_metrics.py, compare_ab.py, compare_morphology.py, double_label.py, single_label.py, stitch_check.py, antibody_report.py"],
    ["X:\\nginx_share\\exm\\2026\\gallery\\", "A full mirror of the three folders above (robocopy /MIR), served at neuroglancer.kuanlab.yale.edu/data/exm/2026/gallery/", "make_gallery.py --mirror"],
    ["Your notes workbook (settings.ini: notes_file)", "The one workbook a person types in: ms ID, ROI, AB labels, ExF, notes; rows added per image, never overwritten", "you; convert_to_zarr.py adds rows and refreshes four columns"],
    ["%LOCALAPPDATA%\\ZarrBatchConverter\\, …\\ZarrABEfficiency\\masks", "Local caches that do not belong in OneDrive: pan-ASLM candidate cache (1.3 GB), structure masks (zarr)", "aslm_training.py, ab_efficiency.py"],
], widths=[2.0 * inch, 3.2 * inch, TEXT_W - 5.2 * inch]))
story.append(P("1.2 settings.ini", "h2"))
story.append(P("Written by First_Time_Setup.bat and read by every tool through <i>convert_to_zarr.load_settings()</i>. The [paths] section names the zarr folder, image list, notes workbook, stitched folder, screenshot folder (the two other output folders sit beside it as 'Zarr Synapse ExF' and 'Zarr Comparisons'), scratch folder and NGFF-Converter install; the [server] section names the served folder, its URL and the Neuroglancer viewer. Moving a folder means editing this file, not the code."))
story.append(CODE("[paths]\nzarr_folder = X:\\nginx_share\\exm\\2026\\zarr\nimage_list = X:\\nginx_share\\exm\\2026\\Zarr Image List.xlsx\nnotes_file = C:\\Users\\admin\\OneDrive - Yale University\\01_Projects\\pan-ASLM\\Zarr Image List - Notes.xlsx\nstitched_folder = X:\\nginx_share\\exm\\2026\\Stitched Precomputed NG\nscreenshot_folder = C:\\Users\\admin\\OneDrive - Yale University\\09_Data & Imaging\\Images\\2026\\Zarr Screenshots\n[server]\nserved_folder = nginx_share\nserved_url = https://neuroglancer.kuanlab.yale.edu/data/\nneuroglancer = https://neuroglancer-demo.appspot.com/"))
story.append(P("1.3 Conventions every tool shares", "h2"))
for t in [
    "<b>Sample names are the key.</b> The zarr, the link, every folder and every row take the file's name; rename before converting. Names start with the date (YYMMDD), carry the animal (WT4, KO1b), the antibody and host (GluA3ms, GluA1Rb), the objective and the kind (3x3 = 2D overview montage, s2s = surface-to-surface stack, _F0 = a single tile, FusionStitcher = a stitched montage).",
    "<b>Which channel is the pan stain:</b> from the name if it says so (pan488), otherwise the one channel where most pixels carry signal rather than scattered spots (PAN_MIN_LIFT 20 counts, PAN_MIN_RATIO 2, PAN_MIN_FILL 25%), with a fallback for faint pan stains (<i>structured_channel</i>). It is drawn white and marked '(pan)' in the image list.",
    "<b>Antibody channels</b> are every channel that is not the pan stain or a 405 nuclear stain, coloured magenta, green, cyan, yellow in order of laser, longest first. In two-antibody samples <b>AB1 = the longest laser (magenta), AB2 = the next (green)</b>, everywhere.",
    "<b>Which antibody is on which laser</b> comes from <i>_antibody_channels.csv</i> in the zarr folder (wins), else dye names in the file name (NR1AF546_Homer1AF647), else a single named antibody. Unknown channels are reported by wavelength ('637 Cy5').",
    "<b>Left out of all testing:</b> names containing SZ (no pan stain), control, pff, tetraspec, and repeats named number_number (Sample1_2) unless the number ends an antibody name (Homer1_2) or a grid (3x3_1). Single tiles (_F0) are left out of comparisons.",
    "<b>Units:</b> 'as imaged' = in the expanded gel; 'corrected' or 'nm of tissue' = divided by the sample's ExF. Pixel sizes: 150.7 nm (40×), 100.5 nm (60×), 203 nm (pan-ASLM).",
    "<b>Statistics:</b> the field of view (one camera frame: a plane of a stack or a tile of a montage) is the unit for the ExF; the animal is the unit for genotype comparisons; intervals are bootstraps with a fixed seed; nothing is corrected for multiple comparisons unless stated.",
    "<b>Figures:</b> Arial, labels above images, a scale bar on every image with its length in the title ('2 µm expanded, 91 nm non-expanded'), 300 dpi, dark background for cards and sheets, light for charts in documents.",
]: story.append(B(t))

# ---------------------------------------------------------------- 2 data layer
story += [PageBreak(), P("2. Getting images onto the server", "h1"), P("2.1 Convert_To_Zarr (convert_to_zarr.py, 2,261 lines)", "h2"),
          P("Drag a folder onto the launcher. For each image it copies the file to local scratch, runs <i>bioformats2raw</i> (the engine inside Glencoe's NGFF-Converter, with its bundled Java) to make a multiscale OME-Zarr, uploads it to the zarr folder, reads the OME metadata (channels, wavelengths, date, objective, voxel size), samples the smallest pyramid level for a brightness range per channel, decides the pan channel and colours, and builds a Neuroglancer state (one layer per channel, pan dimmed to 0.5, antibodies starting above the background haze, a tilted 3D MIP panel for stacks). "
            "The state is saved as JSON under <i>_ng_states</i> and the link points at it. The shared image list is rewritten from the zarrs on every run; your notes workbook gets a row per new image and keeps what you typed. Finished zarrs are skipped, so re-running is safe. A 28 GB Nikon file takes about 20 minutes, mostly the two transfers.")]
story.append(P("Per-format gotchas are recorded in the tool: Nikon files carry no acquisition date (read from the name); a '~' before a channel means the laser was inferred from the dye; plain TIFFs with missing metadata can come out with Z and C swapped (re-export as OME-TIFF)."))
story.append(P("2.2 Views and screenshots (roi_log.py, 922 lines)", "h2"))
story.append(P("<b>Save_NG_View.bat</b> takes the Neuroglancer URL from the clipboard and makes it your default link for that image and/or logs it as an ROI with a crop, rating and notes. <b>Log_Screenshots.bat</b> draws the standard views straight from the data with the view's colours and brightness (not browser grabs): for a stack, XY/XZ/YZ projections, a tilted volume and planes at 5/25/50/75/95% of the way through the gel (planes outside the tissue are dropped first); for a montage, overviews and a seam crop; for a flat image, overview and five crops; plus a 200% close-up at each depth. "
               "Views are at least 1,600 px on the long side with a scale bar; a 'montage' is anything wider than 2,400 px and a 'stack' has 5 or more planes. One row per picture goes in the Zarr Screenshot Log, which is where ratings and notes live."))
story.append(P("2.3 The gallery (make_gallery.py, 629 lines)", "h2"))
story.append(P("<b>Make_Gallery.bat</b> builds <i>2026 AC Gallery.html</i> from what is on disk: one card per sample with its volume or overview, colour chips per channel, tabs All / Pan only / Single / Double / Triple (counted on channels that show signal), objective and genotype filters, search, and a detail page with every picture, the metadata, the Synapse Log numbers (ExF, resolution, labelling), the contact sheet and each synapse card with its file name for copy-paste. "
               "<i>--server</i> writes a one-file copy with embedded thumbnails to the zarr folder (web address under /data/exm/2026/zarr/); <i>--mirror</i> robocopies the three result folders to /gallery/ on the server. PFF and bead samples are hidden."))

# ---------------------------------------------------------------- 3 ExF
story += [PageBreak(), P("3. The expansion factor (synapse_exf.py, 1,282 lines; Measure_ExF.bat)", "h1"),
          P("<b>Principle.</b> In pan-ExM the pan stain shows every synapse as two parallel densities: the postsynaptic density (PSD) and, across the cleft, the row of presynaptic dense projections (DPs). M'Saad, Cairns et al. (Nat. Biotechnol. 2025) measured their centre-to-centre distance as <b>81.9 nm</b> before expansion (82 ± 26 nm between synapses), so the distance measured in an expanded gel, divided by 81.9 nm, is the linear expansion factor. "
            "The paper drew 2-pixel-thick line profiles by hand in FIJI and reported the mean ± s.d. across fields of view. Here the program finds the synapses and places the lines; the reading is the same; a person accepts or rejects synapses in the log.")]
story.append(figure("exf_schematic.png", "Figure 2. The measurement in three steps: the profile across a candidate PSD and the peak-to-peak reading (left), the two-Gaussian fit that decides whether the profile is a side-on synapse (middle), and the hierarchy of evidence behind the one number quoted per sample (right)."))
story.append(P("3.1 Step by step", "h2"))
story.append(table(["Step", "What happens", "Settings (synapse_exf.py)"], [
    ["Planes", "The pan channel is read at full resolution one plane at a time, in 2,048-px tiles with a margin. Planes outside the gel are dropped; planes closer than 4 µm are skipped so no synapse is counted twice; at most --max-planes (default 12) are used, evenly spread.", "PLANE_GAP_UM 4; TILE_PX 2048; gel_planes() from the coarse pan profile"],
    ["Smoothing", "Both channels get a 1-pixel Gaussian (the paper smoothed 0.4–1.5 px).", "SMOOTH_PX 1.0"],
    ["Candidate bars", "A ridge filter (most negative Hessian eigenvalue at the scale of a PSD's half-thickness) marks thin bright lines; pixels in the strongest 3% of ridge response that are also brighter than 80% of the plane are kept; connected bars 120–900 nm long (at a nominal 20×) and at least 1.7× longer than wide are candidates.", "PSD_SIGMA_NM 15; RIDGE_PERCENTILE 97; BRIGHT_PERCENTILE 80; PSD_LENGTH_NM 120–900; EXF_NOMINAL 20 (sets filter sizes only)"],
    ["Profile", "Across each bar, along its normal, 260 nm either side (at 20×), averaged along the bar over up to ±90 nm. The bar's own peak must sit at the centre; a second peak is searched for at 81.9 nm × 12–30 (deliberately wider than the expected 16–24, so the answer is not built in).", "PROFILE_HALF_NM 260; AVERAGE_HALF_NM 90; EXF_SEARCH 12–30; EXF_EXPECTED 16–24"],
    ["Fit and gates", "Two Gaussians on a flat baseline are fitted to the averaged profile. Kept when R² ≥ 0.90, the dip between the peaks is ≥ 0.20 of the smaller peak, the peaks are within a factor 4 in height, and the brighter peak outshines 99.5% of the plane (set from GluA2-positive synapses: 95% ranked above 99.6%; other double peaks had a median rank of 97.8%; without it membrane pairs piled up at 26–30×).", "FIT_R2 0.90; MIN_DIP 0.20; PEAK_RATIO 0.25–4; PSD_RANK 0.995"],
    ["The reading", "On a line 2 pixels thick through the middle of the bar, each peak's top is its brightest sample, refined to sub-pixel by a parabola through it and its two neighbours; top-to-top distance ÷ 81.9 nm = ExF. The distance between the fitted Gaussian centres ÷ 81.9 nm is kept as 'ExF (Gaussian fit)'; it reads about 10% higher.", "LINE_PX 2; REF_NM 81.9"],
    ["Antibody", "Over the same footprint (both densities and a quarter of their spacing beyond), the brightest smoothed antibody pixel is compared with the plane's median in noise-widths (1.4826 × MAD). ≥ 6 is antibody-positive; the same for a second antibody (AB2). Where the antibody peaks across the synapse and how much of the bar it covers are recorded.", "AB_SIGMAS 6"],
    ["De-duplication, ordering", "A synapse found from its PSD and again from its DPs, or in two tiles, is kept once (the better fit). Antibody-positive synapses come first, ordered by an example score (fit, dip, PSD length, antibody strength and band-like coverage); the rest by brightness. The score only orders the log.", "unique(); example_score()"],
    ["Per field, per sample", "A field of view is one plane of a stack or one tile of a montage (grid estimated from the image size) and needs 3 synapses to count. The quoted ExF is the mean ± s.d. across fields, with a 2,000-resample bootstrap CI that resamples fields and then synapses within fields.", "MIN_PER_FOV 3; BOOTSTRAPS 2000"],
    ["Which ExF is quoted", "Accepted synapses if ≥ 5 were marked Y; else antibody-positive synapses if the antibody is synaptic and there are ≥ 10; else all double peaks (pan only, reads high, marked). 'ExF Basis' says which; 'ExF at Window Edge' flags clipping by the 12–30 window.", "MIN_ACCEPTED 5; MIN_FOR_EXF 10"],
    ["Output", "The 60 best synapses per sample are logged with a 300-dpi card and a Neuroglancer link that opens on the synapse; every synapse that passed is in <sample>_synapses.csv (28 columns); a contact sheet and a structure figure per sample; Summary and Method sheets.", "TOP_N 60"],
], widths=[0.95 * inch, 3.85 * inch, TEXT_W - 4.8 * inch], size=7.2))
story.append(P("3.2 Reviewing and re-running", "h2"))
story.append(P("Open the contact sheet beside the log, type Y or N in the Accept column (a dropdown; Notes is free text), save, close Excel, and run Measure_ExF.bat again. Decisions are matched by Synapse ID (slice and position), so they survive renumbering and re-measurement; the Summary then reports 'ExF, accepted'. Which number to use: accepted once reviewed, otherwise 'ExF, AB+ only'; never 'all found' on its own. The log is rewritten after every sample, so a stopped run keeps what it finished and <i>--new-only</i> resumes it."))
story.append(CODE("Measure_ExF.bat                       all samples, asks which antibodies are synaptic\nMeasure_ExF.bat GluA2 --max-planes 50 one antibody, every plane of a stack\nsynapse_exf.py --synaptic auto --no-links   unattended: ANTIBODIES table decides, no server views\nsynapse_exf.py --links                 only add Neuroglancer views for synapses already logged"))
story.append(P("3.3 How the measurement was validated", "h2"))
for t in [
    "<b>Reading methods</b> (synapse_fit_options.py): nine ways of reading the distance were ranked by split-half repeatability on 88 GluA2 synapses (each bar cut in two along its length, both halves measured). Peak-to-peak with parabolic refinement was best (7.7% of the distance) and matches the paper's Supplementary Methods; it is the quoted number. Two-Gaussian fits (8.9%) read ~10% higher and are kept as a second column.",
    "<b>The PSD brightness gate</b> was set on antibody-confirmed GluA2 synapses, not on the pan-only candidates it is meant to clean.",
    "<b>A second ruler:</b> the DP–DP spacing ÷ 67 nm (the paper's Fig. 1o) gives an ExF within ~5% of the DP–PSD one across samples (19.4 vs 20.5 median), from a different structure read along, not across, the synapse.",
    "<b>Anisotropy:</b> ExF against synapse orientation (a + b cos 2θ + c sin 2θ, permutation p) is flat in every sample (median amplitude 3%, max 7%), as isotropic expansion predicts.",
    "<b>A third ruler, prepared:</b> nucleus_size.py measures complete nuclei in the pan stain (volume by Cavalieri, projected area); with pre-expansion nuclei entered in PRE_EXPANSION it gives the paper's nuclear ExF. Until then it reports the implied pre-expansion diameter (5–7 µm so far).",
    "<b>Known limits:</b> only side-on synapses are found, tilted ones read short; the 81.9 nm reference itself scatters 30% between synapses, so the mean over many carries the ExF; pan-only candidates include membrane pairs; the pixel at 40× is 7–9 nm of tissue, so sub-pixel refinement matters.",
]: story.append(B(t))
story.append(figure("card_0616_12.jpg", "Figure 3. A synapse card as the log writes it: pan, antibody 1, antibody 2, overlay with the measured axis (dotted), and the profile with the 2-pixel line (white), the averaged profile (grey), the two-Gaussian fit (blue), the PSD and DP peaks, and both antibodies rescaled (dashed). The title gives the reading, the fit, the antibody signal in noise-widths, the class and the scale bar before and after expansion."))

# ---------------------------------------------------------------- 4 structure
story += [PageBreak(), P("4. Synaptic structure (synapse_structure.py, 346 lines)", "h1"),
          P("Measured on every synapse the finder returns, from the same profiles, and summarised per sample on the synapses the ExF rests on. Nothing here feeds back into the expansion factor.")]
story.append(table(["Measure", "How", "Per-sample summary"], [
    ["PSD length", "Along the PSD (a 2-px line through its top, ±0.75 bar lengths), the unbroken stretch above half of its height; left out when it runs off the sampled stretch.", "mean ± s.d., nm of tissue"],
    ["PSD thickness", "FWHM across the synapse from the fitted Gaussian (2.355 σ). Includes the optical blur (~430 nm as imaged): an upper bound.", "mean ± s.d., nm"],
    ["DP–DP spacing", "Peaks along the DP row (prominence 10%), neighbouring distances within 67 nm × the search window.", "mean ± s.d., n; ExF from DP–DP = mean ÷ 67"],
    ["Cleft", "The darkest point between the two peaks on the 2-px line, sub-pixel; PSD-to-cleft and DP-to-cleft distances; where each antibody peaks relative to it (+ = presynaptic side).", "means, nm"],
    ["Antibody offset", "Where the antibody peaks across the synapse, from the PSD (the brighter density) towards the DPs, and its distance to the DP peak; AB1–AB2 distance across the synapse on doubly positive synapses.", "mean ± s.d., nm, n"],
    ["Which density is the PSD", "By the antibody's known side when positive; else by texture (a PSD is smooth along its length, a DP row bumpy: ROUGHER_BY 1.2); else the brighter one. Texture agreed with the antibody in 90% of GluA2 synapses.", "'PSD Named By' per synapse"],
    ["Class", "Type I macular; Type I perforated (PSD falls below 0.5 of its height over 20–250 nm with ≥ 40 nm of PSD on both sides); Type II putative (texture names the DP row and it is 1.3× brighter than the PSD). After the paper's Fig. 2jj–ll; provisional.", "counts per class, perforated %"],
    ["Anisotropy, evenness", "ExF vs orientation fit with 1,000-shuffle p (needs ≥ 20 synapses); CV of the fields' mean ExF.", "amplitude %, p, field CV %"],
    ["Labelling by depth", "Share of synapses carrying antibody in the planes within 0.2 of the gel thickness of either face, and in the middle 0.2 (penetration).", "near faces %, at centre %"],
    ["Confirmed-synapse density", "Antibody-positive synapses per 100 µm² of tissue (area ÷ ExF²); a lower bound, side-on synapses only.", "per 100 µm²"],
], widths=[1.3 * inch, 3.9 * inch, TEXT_W - 5.2 * inch], size=7.2))
story.append(figure("struct_WT4.jpg", "Figure 4. The per-sample structure figure: ExF against orientation, ExF per field, DP–DP spacing against the paper's 67 nm, antibody position across the synapse, PSD length, and (for stacks with an antibody) labelling by depth."))

# ---------------------------------------------------------------- 5 antibody quantification
story += [PageBreak(), P("5. Antibody quantification", "h1"),
          P("Six tools look at antibodies from different angles. They share one rule for 'a punctum' and 'antibody-positive': 3 or more connected pixels of the 1-px-smoothed channel at least 6 noise-widths (1.4826 × MAD) above the frame's median. Laser power and exposure are not in the files, so comparisons across imaging sessions always carry that caveat.")]
story.append(table(["Tool", "Question", "How", "Output"], [
    ["synapse_exf.py (antibody_numbers)", "Does the antibody sit on the synapses the pan stain finds?", "Share of pan-found synapses that are antibody-positive (lower bound); median signal at positive synapses; share of all puncta within 2 DP–PSD distances of a synapse, divided by the share expected by chance = enrichment; resolution = FWHM of the 10th-percentile smallest isolated puncta from a 2D Gaussian fit on the unsmoothed image.", "Summary sheet: AB Labelled Synapses %, AB Signal, AB Puncta on Synapses %, AB Enrichment ×, Resolution as imaged and corrected"],
    ["single_label.py", "Labelling efficiency of one antibody, with no second label to confirm synapses", "The same footprint as the log, moved ~400 nm of tissue away in four diagonal directions, gives the share that reads as labelled by chance; efficiency = (labelled − chance) ÷ (1 − chance). Also on the clearest third of profiles. Bootstrap over fields of view. --multi applies it to each channel of double labels.", "Zarr Comparisons\\Single Label\\ and Multi Label\\: Per Sample, Per Antibody, Efficiency.png"],
    ["double_label.py", "Does one channel bleed into the other, and how often are both labels on the same synapse?", "At strong source spots (≥ 10 σ), source and target are averaged over 5 × 5 px; a 10th-percentile quantile regression of target on source gives the floor's slope = the most bleed-through there could be (bootstrap bound); 'not ruled out' when the floor clearly rises. Co-labelling from the log: AB1 at AB2-positive synapses and the reverse, Wilson intervals, both-by-chance.", "Zarr Comparisons\\Double Label\\: Bleed-through.csv, Efficiency.csv, a scatter figure per sample, Summary.png"],
    ["channel_check.py", "Is there signal in each channel, and does a channel show up in another?", "Spots per 1,000 µm² and typical spot height per channel (dark < 0.1, sparse < 1); per channel pair, at strong source spots: target present %, target/source ratio and its CV, correlation. A screen, not proof.", "Channel Check - Channels.csv, - Pairs.csv"],
    ["ab_metrics.py", "Puncta in biological units", "Puncta per 100 µm² of tissue, nearest-neighbour distance and Clark–Evans R (clustered < 1), labelled sites against the number of synapse profiles a section should hold (Abercrombie), synaptic enrichment, background level and unevenness, vessel-like structures, AB1–AB2 pairing within 150 nm.", "AB Metrics\\: AB Metrics.csv, AB Pairs.csv, AB Distributions (nm).csv"],
    ["ab_efficiency.py (Measure_AB_Efficiency.bat)", "Whole-image colocalisation with the pan stain", "Rolling-ball background (8 µm), Otsu or Li masks, Pearson's r in the structure box and inside the structure, Manders M1/M2, coverage, SBR/SNR, SBR CV on a 3 × 3 grid, penetration profile plane by plane (NPI, Z½), montage seam step and vignetting, a fidelity map. Three lazy Dask passes; masks kept locally as zarr.", "AB Efficiency Summary.csv, per-sample plots. On these sparse synaptic antibodies SBR ≈ 1, so coverage numbers are not informative; the synapse-centred tools are."],
    ["compare_ab.py (Compare_AB.bat)", "Set A against set B from the antibody channel alone", "Eight planes through the middle 80% of each stack, one field per camera frame: puncta density, punctum signal, labelled area, background, noise; ratio B/A with a field bootstrap and Mann–Whitney; 'decreased' only when the whole interval is below 1. Pairs samples by antibody and host from the names; lists the imaging settings the files record.", "&lt;A&gt; vs &lt;B&gt;\\: Comparison.csv, Per Sample.csv, Imaging Settings.txt, Examples.png, Fields.png"],
], widths=[1.15 * inch, 1.3 * inch, 2.85 * inch, TEXT_W - 5.3 * inch], size=7.0))
story.append(P("5.1 Channel bookkeeping", "h2"))
for t in [
    "<b>antibody_channels.py</b> keeps <i>_antibody_channels.csv</i> in the zarr folder: one row per sample (or name prefix) and laser, with the antibody and a Status; TO DO rows are created for sets where the mapping is unknown. Naming new files as date_animal_pan488_NR1-AF546_Homer1-AF647_objective_kind makes the table unnecessary.",
    "<b>channel_registration.py</b> handles a channel recorded on a second camera (16 June 2026: the 488 pan on 'Sona02', mirrored, rotated ~1–4° and shifted ~2,100 px). Beads in the two cameras' channels are matched (pairs of beads the same distance apart, with and without mirroring, RANSAC affine at 1 px); each sample of that day is tried with each bead transform and keeps the one that makes the pan brightest at the antibody puncta. The transform is recorded in <i>_channel_registration.json</i> and applied by <i>ab_efficiency.open_level</i>, the screenshots and the Neuroglancer layer's source.transform; raw zarrs are untouched. The 22 July samples also name camera 2 but are in register and are left alone.",
]: story.append(B(t))
story.append(figure("dl_0616_12.jpg", "Figure 5. The double-label test for one sample: target against source at strong spots in each direction, with the floor line whose slope bounds the bleed-through, and the division of pan-found synapses between the two labels."))

# ---------------------------------------------------------------- 6 comparisons
story += [PageBreak(), P("6. Comparing sets of samples", "h1")]
story.append(table(["Tool", "Unit and test", "Used for"], [
    ["compare_ab.py", "Field of view; bootstrap ratio and Mann–Whitney (optimistic p: fields of one sample are not independent)", "SZ vs AC (same gels, nine weeks apart, before and after pan-staining); WT vs KO antibody channel"],
    ["compare_morphology.py", "Animal: per-sample median over synapses, per-animal mean, exact permutation test across animals, leave-one-animal-out range. Lengths on each sample's own DP–PSD scale.", "WT vs Shank2 KO synapse morphology (5 + 4 animals; detects ~15–20% changes)"],
    ["single_label.py / double_label.py", "Field of view bootstrap; Wilson intervals for shares", "Antibody against antibody; channel against channel"],
    ["antibody_report.py, double_label_report.py, gallery_report\\", "Read the CSVs and the Synapse Log only; embed the figures", "Antibody Report.html, Double Label Report.html, the gallery and setup PDFs"],
], widths=[1.6 * inch, 3.0 * inch, TEXT_W - 4.6 * inch]))
story.append(P("Sets are named by a token in the sample name ('WT', 'KO', 'SZ', 'AC'), matched alone or with a number and letter (WT4, KO1b); an animal is that token. Samples at another pixel size than the majority are left out of morphology comparisons, as are single tiles."))

# ---------------------------------------------------------------- 7 pan-ASLM
story += [P("7. pan-ASLM volumes", "h1"),
          P("The light-sheet data arrive as 4 × 4 tile grids of 2,048 × 2,048 × 9,520 voxels at 203 × 203 × 200 nm, stitched by NRStitcher (pi2) on the HPC into Neuroglancer precomputed volumes (11,363 × 11,362 × 9,521 voxels for the June volume). Three tools work on them directly; the chunk files are read 16 at a time, so a 2,048² field takes about 10 s.")]
for t in [
    "<b>stitch_check.py &lt;folder&gt; 4 4 15 [depths]</b>: windows of 512 px along every overlap band, at every four-tile corner and in tile interiors; a doubling index from the autocorrelation (a second peak = the two tiles blended a few pixels apart, with its offset), a sharpness ratio, and brightness relative to interiors; whole-plane mean profiles along x and y for seams. Writes Stitching Check.png, Bands/Windows/Depths CSVs; the critique is in NR Stitching Critique.md.",
    "<b>aslm_synapses.py &lt;folder&gt; 4 4 15 6</b>: the synapse_exf finder, unchanged, on the middle 2,048 px of every tile at six depths; synapses ranked by brightness within their own field (tiles differ in brightness); the same log layout in pan-ASLM Synapse Log.xlsx; finished fields cached in _fields.pkl so a stopped run resumes.",
    "<b>aslm_training.py plan | collect | export</b>: plans 1,400 fields weighted by expected synapse yield (depth × tile effects from the pilot, with a floor so every depth and tile is sampled), collects candidates with loosened gates (PSD_RANK 0.98, R² 0.85, dip 0.15) and a 96-px raw patch each, then exports the 8,000 best that pass the normal gates and 2,000 questionable ones drawn evenly across the score range (noise below 4 σ excluded) as 64 × 64 and 9 × 64 × 64 npz arrays with PSD/DP points, overview sheets, a card per questionable synapse and a review workbook. Candidate cache: %LOCALAPPDATA%\\ZarrBatchConverter\\aslm_training\\.",
]: story.append(B(t))

# ---------------------------------------------------------------- 8 tool reference
story += [PageBreak(), P("8. Tool reference", "h1")]
story.append(table(["Script", "Launcher", "Reads", "Writes", "Typical time"], [
    ["convert_to_zarr.py", "Convert_To_Zarr.bat, First_Time_Setup.bat", "microscope files, settings.ini", "zarrs, _ng_states, Zarr Image List.xlsx, notes workbook rows", "~20 min per 28 GB file"],
    ["roi_log.py", "Log_Screenshots.bat, Save_NG_View.bat", "zarrs, NG states, clipboard URL", "Zarr Screenshots\\<sample>\\*.png, Zarr Screenshot Log.xlsx", "~1 min per sample"],
    ["make_gallery.py", "Make_Gallery.bat (--open --server --mirror)", "screenshots, logs, image list, synapse log", "2026 AC Gallery.html, _gallery_thumbs, server copy, gallery mirror", "4 min"],
    ["synapse_exf.py", "Measure_ExF.bat", "zarrs (pan + antibodies), Accept decisions", "Synapse Log.xlsx, <sample>_synapses.csv, cards, contact sheets, structure figures, per-synapse NG views", "~70 min for 51 samples (12 planes each)"],
    ["synapse_structure.py", "(imported by synapse_exf)", "profiles", "structure columns and figure", "—"],
    ["synapse_fit_options.py", "—", "zarrs (one antibody)", "Fit options (<text>).csv / .png", "minutes"],
    ["nucleus_size.py", "—", "zarrs (pan, stacks)", "Nucleus Sizes.csv, <sample>_nuclei.png / -3d.png / .gif", "minutes per stack"],
    ["channel_check.py", "—", "zarrs (middle frame, 3 planes)", "Channel Check - Channels.csv, - Pairs.csv", "~10 min"],
    ["antibody_channels.py", "—", "_antibody_channels.csv, names", "the table's TO DO rows; used by the other tools", "seconds"],
    ["channel_registration.py", "— (--views)", "bead images, same-day samples", "_channel_registration.json, updated NG states", "minutes"],
    ["ab_efficiency.py", "Measure_AB_Efficiency.bat", "zarrs (pan + antibodies)", "AB Efficiency Summary.csv, plots, local mask zarrs", "~1 min per 40-plane stack at full resolution"],
    ["ab_metrics.py", "—", "zarrs, synapse CSVs", "AB Metrics\\ CSVs", "~20 min"],
    ["compare_ab.py", "Compare_AB.bat A B", "zarrs (antibody channel)", "&lt;A&gt; vs &lt;B&gt;\\ CSVs and figures, _measured cache", "~10 min per pair"],
    ["compare_morphology.py", "—", "<sample>_synapses.csv, Synapse Log", "WT vs KO\\Morphology - *.csv, Morphology.png", "seconds"],
    ["single_label.py", "— (--multi)", "zarrs (antibody), <sample>_synapses.csv", "Single Label\\ or Multi Label\\ CSVs and Efficiency.png", "~25 min / ~20 min"],
    ["double_label.py", "Double_Label.bat", "zarrs (antibody channels), <sample>_synapses.csv", "Double Label\\ CSVs, per-sample figures, Summary.png", "~4 min"],
    ["antibody_report.py, double_label_report.py", "—", "comparison CSVs, Synapse Log", "Antibody Report.html; Desktop\\Double Label Report.html", "seconds"],
    ["stitch_check.py", "—", "precomputed volume", "Stitching\\<volume>\\ CSVs, Stitching Check.png", "~15 min for the full volume"],
    ["aslm_synapses.py", "—", "precomputed volume", "pan-ASLM Synapse Log.xlsx, fields/synapses CSVs, cards, sheets", "~20 min for 96 fields"],
    ["aslm_training.py", "—", "precomputed volume, pilot results", "Training Set\\ (npz, CSV, sheets, cards), Training Review.xlsx, local cache", "collect: hours; export: 15 min"],
    ["gallery_report\\ (charts.py, build_report.py, build_setup_doc.py)", "—", "Synapse Log summary, image list, comparison CSVs, figures", "Desktop PDFs", "seconds"],
], widths=[1.35 * inch, 1.2 * inch, 1.3 * inch, 2.0 * inch, TEXT_W - 5.85 * inch], size=6.9))
story.append(P("Long runs are launched detached (Start-Process) with a driver script such as redo_figures.py, because background tasks in the editor are stopped after two hours. Close the Excel logs before a run: a workbook open in Excel cannot be rewritten, and the tools say so and keep the sample for the next save.", "small"))

# ---------------------------------------------------------------- 9 requirements and recipe
story += [P("9. Requirements and an end-to-end recipe", "h1"), P("9.1 Requirements", "h2")]
for t in [
    "Windows PC with the X: share mapped; Python 3.9+ (Anaconda at C:\\Users\\admin\\anaconda3 here); NGFF-Converter (Glencoe) installed for its bioformats2raw and Java.",
    "Python packages, installed by the launchers on first use: openpyxl, numpy ≥ 2, zarr ≥ 3, dask[array], scikit-image, scipy, matplotlib, pillow, contourpy ≥ 1.3. <b>Always pin numpy ≥ 2 and contourpy ≥ 1.3 in the same pip call</b>: an unpinned install once pulled numpy 1.26 into the user site and broke zarr 3. The report builders add reportlab, pypdf and pymupdf. napari and poppler are not installed.",
    "Fonts: Arial (Windows) for all figures and PDFs; Consolas for code in this document.",
]: story.append(B(t))
story.append(P("9.2 Recipe for a new imaging day", "h2"))
story.append(table(["#", "Do", "Then check"], [
    ["1", "Name files date_animal_antibody+host_objective_kind; drag the folder onto Convert_To_Zarr.bat.", "Image list: Status, Link Status, channel colours, '(pan)' on the right channel, no '~' surprises."],
    ["2", "If two antibodies: add rows to _antibody_channels.csv (or encode dyes in the names). If a channel was on a second camera: run channel_registration.py, then --views.", "channel_check.py: no dark or sparse channel you did not expect; pair verdicts."],
    ["3", "Log_Screenshots.bat (Enter = all). Rate and note in the Zarr Screenshot Log while looking.", "Views exist per sample; XZ projection shows the gel faces; seams on montages."],
    ["4", "Measure_ExF.bat; answer which antibodies are synaptic (or --synaptic auto).", "Summary: ExF Basis, AB+ counts, 'ExF at Window Edge' near 0, anisotropy flat, DP–DP ExF close to DP–PSD."],
    ["5", "Open each contact sheet, mark Accept Y/N, run Measure_ExF.bat again.", "'ExF, accepted' appears; use it from now on."],
    ["6", "single_label.py (and --multi), double_label.py for double labels, compare_ab.py / compare_morphology.py for sets, ab_metrics.py if spacing or density is wanted.", "Bleed-through verdicts before quoting any co-labelling; efficiency intervals per field."],
    ["7", "Make_Gallery.bat (server copy + mirror); regenerate the HTML/PDF reports.", "Gallery tabs and numbers; the server mirror holds the new result folders."],
], widths=[0.3 * inch, 3.6 * inch, TEXT_W - 3.9 * inch]))

# ---------------------------------------------------------------- 10 decisions
story += [PageBreak(), P("10. Decisions behind the setup", "h1"), P("Choices made during the build, so they are not re-litigated by accident:")]
for t in [
    "<b>81.9 nm, peak to peak, on a 2-pixel line, mean ± s.d. across fields of view</b> (7 October): this follows the paper's Supplementary Methods. The two-Gaussian value stays as a second column; the AB profile stays on the cards, dashed; the fit is drawn blue.",
    "<b>Expected ExF is 16–24</b> (user, 7 October; typed 16–20 first and corrected the same day). Values outside are marked, not dropped; the search window is wider (12–30) on purpose.",
    "<b>Synapse classes</b> follow the paper's Fig. 2 (type I macular / perforated, type II) and are labelled provisional; names are written in full on figures ('Type I, macular', never abbreviated).",
    "<b>Sample exclusions:</b> SZ (same gels as AC, imaged before the pan stain; its 405 channel is DAPI), control, number_number repeats, PFF (user: 'ignore all PFF for future testing'; the five PFF zarrs were deleted from the server on 7 October, raw originals kept), tetraspec beads (hidden from the gallery, used only for registration).",
    "<b>KO = Shank2 KO.</b> The 31 August KO animals lack Shank2; none of their antibodies targets it, so KO-vs-WT is a genotype comparison, not a specificity test, and one animal per genotype per antibody is the design's limit.",
    "<b>SZ and AC are the same gels</b> (SZ imaged 24 February before pan-staining, AC 29 April after), so AC's ExF applies to SZ and AC's dark 637 channel is either lost label or under-excitation.",
    "<b>The 22 July samples name camera 2 but are not misregistered</b>: leave them alone, do not caveat them.",
    "<b>Figures:</b> Arial, 'ExF = 20' never '20×', labels above images, scale bar on every image with 'expanded / non-expanded' lengths in the title, image panels touching, 300 dpi.",
    "<b>Gallery:</b> tabs counted on channels that show signal; colour chips per channel; 'Images' not 'pictures'; dropdown entries acceptable instead of tabs.",
    "<b>Code hygiene:</b> several sessions edit synapse_exf.py and make_gallery.py at once, so patches are small exact-string edits after re-reading; the .py files use LF line endings; long re-runs go through detached driver scripts.",
]: story.append(B(t))

# ---------------------------------------------------------------- 11 limits and next
story += [P("11. Limits, and what is planned", "h1")]
for t in [
    "<b>2D, side-on synapses only.</b> The finder sees synapses cut side-on in a single plane; tilted ones read short or are missed; density numbers are lower bounds; PSD length is a half-height stretch on one profile (~118 nm against ~300 nm in EM).",
    "<b>Rank-based gates</b> (brightest 0.5% of the plane) normalise away global brightness differences between samples, which protects the ExF but can hide a genotype effect on PSD brightness or number.",
    "<b>Antibody efficiencies are lower bounds</b> (membrane pairs in the denominator) and depend on imaging settings the files do not record.",
    "<b>Bleed-through</b> can be bounded but not measured without a single-label control imaged in both channels.",
    "<b>Human review is the missing step:</b> the Accept column, screenshot ratings and the training review are the designed checkpoints and are still empty.",
    "<b>Planned (decided 7–13 October):</b> pixel-level PSD ('post') and DP ('pre') segmentation, self-supervised pre-training then train/test on the pan-ASLM training set, labels corrected in WebKnossos, 2D first then 3D, run on the HPC in a separate package repository; a pairing tool giving a DP→PSD vector per synapse for size and distribution; nuclei from pan-ASLM; the F31 panel Shank2 / NR1 / Homer1 / mGluR5 + pan in ACC at P60, male and female, via a manifest.",
    "<b>Validation rule for any new segmentation:</b> show 30+ random full-resolution instances before quoting a count (the MAPS 'mitochondria' trial counted synapses and nuclei).",
]: story.append(B(t))
story.append(P("Reference: M'Saad O., Cairns J., et al. pan-ExM-t. Nat. Biotechnol. 2025, and its Supplementary Methods (OneDrive: Nature Biotech MS v1\\v1.4\\250922_panExmt_supplementary_v1.4.pdf).", "small"))


def on_page(canvas, doc):
    canvas.saveState(); canvas.setFont("Arial", 7.5); canvas.setFillColor(DIM)
    canvas.drawString(MARGIN, 0.5 * inch, "pan-ExM analysis setup  ·  Kuan Lab  ·  Desktop\\ZarrBatchConverter")
    canvas.drawRightString(W - MARGIN, 0.5 * inch, f"{doc.page}")
    canvas.restoreState()


doc = SimpleDocTemplate(str(OUT), pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=0.75 * inch, bottomMargin=0.8 * inch,
                        title="pan-ExM analysis setup", author="Kuan Lab", subject="How the ExF and synaptic quantification toolchain works")
doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print(OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")
