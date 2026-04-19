═══════════════════════════════════════════════════════════════
 ArcSlicer — Arc Overhang Post-Processor
 For Qidi Q1 Pro + OrcaSlicer
═══════════════════════════════════════════════════════════════

WHAT THIS DOES
──────────────
Arc Overhang is a technique that lets you print true 90° flat
overhangs WITHOUT any support material, by replacing the normal
bridge/infill moves with concentric arcs that cool mid-air and
bond to each other.

ArcSlicer takes your normally-sliced .gcode, rewrites the
overhang layers with arc moves, and saves a new .gcode file
ready to send to your printer.


FIRST-TIME SETUP (do this once)
─────────────────────────────────
1. Install Python 3.10+ from https://www.python.org/downloads/
   ⚠ IMPORTANT: tick "Add Python to PATH" during installation!

2. Double-click setup.bat
   This installs all required packages and downloads the arc
   algorithm script from GitHub automatically.


EVERY-TIME WORKFLOW
────────────────────
Step 1 — Prepare your slicer (OrcaSlicer / QIDIStudio)
   • Printer Settings → Machine G-code:
       Make sure "Use relative E distances" (M83) is active.
   • Print Settings → Quality:
       Enable "Detect overhangs" / bridge detection.
   • Slice your model as normal.
   • Export G-code: File → Export → Export G-code…
     Save it into the  input\  folder inside ArcSlicer.

Step 2 — Run ArcSlicer
   • Double-click  ArcSlicer.bat
   • Click Browse and select your .gcode from the input\ folder.
   • The output path auto-fills (you can change it if you want).
   • Adjust parameters if needed (see section below).
   • Click  ▶ PROCESS G-CODE
   • Watch the console for progress.

Step 3 — Send to printer
   • Your processed file appears in the  output\  folder.
   • Upload it to your Qidi Q1 Pro via the web interface,
     USB, or however you normally send files.


KEY PARAMETERS EXPLAINED
──────────────────────────
Most of the time you only need to tweak these:

  Arc Print Speed (default 90 mm/min ≈ 1.5 mm/s)
    How fast the nozzle moves during arc printing.
    Slower = better cooling = better overhang quality.
    Once you're happy with results, try 120–150 mm/min.

  Extrusion Multiplier (default 1.35)
    Extra filament for the mid-air arc moves.
    If you see gaps between arcs → increase to 1.4–1.5.
    If you see blobs → decrease to 1.2–1.3.

  Fan Speed during arcs (default 255 = 100%)
    KEEP THIS AT MAX. The Q1 Pro has excellent cooling.
    Strong airflow is what makes arcs solidify mid-air.

  Arc Center Offset (default 2 mm)
    Prevents micro-arcs at the edges.
    Increase if you get messy edges; decrease if narrow
    sections of your model aren't being filled.

  Max Arc Radius (default 110 mm)
    The largest arc the algorithm will make.
    Only increase this for very large flat overhangs
    (bigger than ~half your bed width).

  Hilbert Fill % (default 100%)
    Solid infill density for the layer printed on top of
    the arcs. Keep at 100% for best surface quality.


TROUBLESHOOTING
────────────────
Problem: "Arc script not found"
  → Run setup.bat first. If auto-download failed, see the
    manual download instructions shown by setup.bat.

Problem: Arcs drooping / sagging
  → Decrease Arc Print Speed (try 60 mm/min)
  → Make sure fan is at max (255)
  → Print as cold as safely possible (190°C for PLA)

Problem: Gaps between arcs
  → Increase Extrusion Multiplier (try 1.45)
  → Decrease Arc Print Speed

Problem: Warping on layers above arcs
  → Decrease Hilbert Fill % slightly (try 85%)
  → Decrease aboveArcsInfillPrintSpeed (try 300 mm/min)

Problem: Small overhangs not being processed
  → Decrease Min Overhang Area (try 20 mm²)
  → Decrease Min Bridge Length (try 2 mm)

Problem: Script runs but nothing changes in the G-code
  → Make sure "Detect overhangs" is enabled in OrcaSlicer
  → Re-export the G-code (don't use a saved/old file)
  → Check that you have actual 90° overhangs (not just steep
    angles) — arc processing only targets true bridges.


RECOMMENDED PRINT SETTINGS (Qidi Q1 Pro, PLA)
───────────────────────────────────────────────
  Nozzle temp:    190–195°C  (cooler = better arc quality)
  Bed temp:       60°C
  Part cooling:   100% during arc layers (automatic)
  Layer height:   0.2 mm
  Perimeters:     3+ (gives better anchor for arcs)


FILE STRUCTURE
──────────────
  ArcSlicer\
  ├── ArcSlicer.bat          ← Double-click to launch
  ├── setup.bat              ← First-time setup
  ├── arc_ui.py              ← The UI application
  ├── prusa_slicer_post_processing_script.py  ← Arc algorithm
  ├── config.json            ← Your saved settings (auto-created)
  ├── input\                 ← Drop your .gcode files here
  └── output\                ← Processed files appear here


CREDITS
────────
Arc overhang algorithm: Steven McCulloch
PrusaSlicer integration: nicolai-wachenschwan
This fork: Wasupmacuz
UI wrapper: ArcSlicer (built for Qidi Q1 Pro)
GitHub: https://github.com/Wasupmacuz/arc-overhang-prusaslicer-integration

═══════════════════════════════════════════════════════════════
