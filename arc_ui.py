#!/usr/bin/env python3
"""
ArcSlicer UI v2 — Arc Overhang G-code Post-Processor
Smart parameter tuning + arc preview window.
Designed for Qidi Q1 Pro + OrcaSlicer workflow.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import json, os, re, shutil, subprocess, sys, threading
from pathlib import Path

# ── Try importing matplotlib (preview feature) ──────────────────────────────
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.cm as cm
    import matplotlib.patches as mpatches
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ─────────────────────────────────────────────────────────────────────────────
#  Paths
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent.resolve()
ARC_SCRIPT  = SCRIPT_DIR / "prusa_slicer_post_processing_script.py"
CONFIG_FILE = SCRIPT_DIR / "config.json"
INPUT_DIR   = SCRIPT_DIR / "input"
OUTPUT_DIR  = SCRIPT_DIR / "output"
INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
#  Material presets
# ─────────────────────────────────────────────────────────────────────────────
MATERIAL_PRESETS = {
    "PLA": {
        "ArcPrintSpeed": 90.0,  "ArcMinPrintSpeed": 30.0,
        "ArcFanSpeed": 255,     "aboveArcsFanSpeed": 25,
        "ArcExtrusionMultiplier": 1.35,
        "note": "Best results. Max fan, low temp (190–200°C).",
    },
    "PLA+": {
        "ArcPrintSpeed": 90.0,  "ArcMinPrintSpeed": 30.0,
        "ArcFanSpeed": 255,     "aboveArcsFanSpeed": 25,
        "ArcExtrusionMultiplier": 1.35,
        "note": "Same as PLA. Print at 200–210°C for best arc quality.",
    },
    "PETG": {
        "ArcPrintSpeed": 60.0,  "ArcMinPrintSpeed": 20.0,
        "ArcFanSpeed": 180,     "aboveArcsFanSpeed": 50,
        "ArcExtrusionMultiplier": 1.40,
        "note": "Slower + less cooling. Stringing is common — use min fan.",
    },
    "ABS": {
        "ArcPrintSpeed": 60.0,  "ArcMinPrintSpeed": 20.0,
        "ArcFanSpeed": 80,      "aboveArcsFanSpeed": 20,
        "ArcExtrusionMultiplier": 1.30,
        "note": "Minimal cooling. Enclosure strongly recommended.",
    },
    "TPU": {
        "ArcPrintSpeed": 30.0,  "ArcMinPrintSpeed": 15.0,
        "ArcFanSpeed": 200,     "aboveArcsFanSpeed": 60,
        "ArcExtrusionMultiplier": 1.50,
        "note": "Very slow. Flexible filament needs high extrusion mult.",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
#  Parameter definitions  name -> (label, default, unit, tooltip, min, max, type, auto_derived)
# ─────────────────────────────────────────────────────────────────────────────
PARAMETERS = {
    "ArcPrintSpeed": (
        "Arc Print Speed", 90.0, "mm/min",
        "Speed while printing arcs. Lower = better quality.\n"
        "~90 mm/min (1.5 mm/s) is the recommended start.\n"
        "Auto-suggested based on selected material.",
        10, 500, float, False),
    "ArcMinPrintSpeed": (
        "Min Arc Speed", 30.0, "mm/min",
        "Very short arcs are slowed to this speed automatically\n"
        "so they have more time to cool. 30 mm/min is safe.",
        5, 200, float, False),
    "ArcSlowDownBelowThisDuration": (
        "Slow-Down Threshold", 3.0, "sec",
        "Arcs shorter than this duration (seconds) get slowed\n"
        "down to ArcMinPrintSpeed.",
        0.5, 20.0, float, False),
    "ArcExtrusionMultiplier": (
        "Extrusion Multiplier", 1.35, "×",
        "Extra filament for mid-air arc moves.\n"
        "Increase to ~1.5 if gaps appear between arcs.\n"
        "Auto-suggested based on material.",
        0.5, 3.0, float, False),
    "ArcFanSpeed": (
        "Fan Speed (during arcs)", 255, "/255",
        "Cooling fan during arcs. Keep at max (255) for PLA/PLA+.\n"
        "Auto-suggested based on material.",
        0, 255, int, False),
    "aboveArcsFanSpeed": (
        "Fan Speed (above arcs)", 25, "/255",
        "Fan speed for layers above the arc zone.\n"
        "Kept low to avoid warping.",
        0, 255, int, False),
    "aboveArcsPerimeterFanSpeed": (
        "Fan Speed (perimeters above)", 25, "/255",
        "Fan speed for perimeters in cooling zone above arcs.",
        0, 255, int, False),
    "specialCoolingZdist": (
        "Cooling Zone Height", 3.0, "mm",
        "Height above arcs to apply special cooling/speed regime.",
        0.5, 20.0, float, False),
    "aboveArcsInfillPrintSpeed": (
        "Infill Speed (above arcs)", 600.0, "mm/min",
        "Infill speed for layers above arcs.\n"
        "Slower reduces stress on the fresh arc layer.\n"
        "300–600 mm/min recommended.",
        100, 3000, float, False),
    "aboveArcsPerimeterPrintSpeed": (
        "Perimeter Speed (above arcs)", 180.0, "mm/min",
        "Perimeter speed in the cooling zone above arcs.",
        100, 3000, float, False),
    "RMax": (
        "Max Arc Radius", 110.0, "mm",
        "Maximum arc radius the algorithm generates.\n"
        "110mm covers most prints. Only increase for very large models.",
        10, 500, float, False),
    "ArcCenterOffset": (
        "Arc Center Offset", 2.0, "mm",
        "Prevents micro-arcs at edges by offsetting centers inward.\n"
        "Higher = cleaner edges but may miss narrow passages.\n"
        "2mm is optimal for most models.",
        0.1, 20.0, float, False),
    "MinArea": (
        "Min Overhang Area", 50.0, "mm²",
        "Minimum overhang area to trigger arc processing.\n"
        "Lower to process smaller overhangs.",
        1, 1000, float, False),
    "MinBridgeLength": (
        "Min Bridge Length", 5.0, "mm",
        "Minimum overhang edge length to start arc generation.",
        0.5, 50, float, False),
    "ExtendArcDist": (
        "Arc Extension Distance", 1.0, "mm",
        "Extends arcs tangentially for better bonding between arcs.",
        0, 10, float, False),
    "MaxDistanceFromPerimeter": (
        "Max Distance from Perimeter", 0.0, "mm (0=auto)",
        "Max distance arc centres can stray from perimeter.\n"
        "0 = auto (2× perimeter width). Auto-derived when G-code loaded.",
        0, 20, float, True),
    "HilbertFillingPercentage": (
        "Hilbert Fill %", 100, "%",
        "Coverage of Hilbert-curve solid infill above arcs.\n"
        "100% gives best surface quality.",
        10, 100, int, False),
    "HilbertInfillExtrusionMultiplier": (
        "Hilbert Extrusion Multiplier", 1.05, "×",
        "Slight over-extrusion for Hilbert infill to close micro-gaps.",
        0.5, 2.0, float, False),
}

PARAM_GROUPS = {
    "⚡  Print Speed & Extrusion": [
        "ArcPrintSpeed","ArcMinPrintSpeed",
        "ArcSlowDownBelowThisDuration","ArcExtrusionMultiplier"],
    "❄️  Fan & Cooling": [
        "ArcFanSpeed","aboveArcsFanSpeed","aboveArcsPerimeterFanSpeed",
        "specialCoolingZdist","aboveArcsInfillPrintSpeed","aboveArcsPerimeterPrintSpeed"],
    "🔵  Arc Geometry": [
        "RMax","ArcCenterOffset","MinArea",
        "MinBridgeLength","ExtendArcDist","MaxDistanceFromPerimeter"],
    "🧱  Hilbert Infill (layer above arcs)": [
        "HilbertFillingPercentage","HilbertInfillExtrusionMultiplier"],
}

# Colour palette
BG=="#1e1e2e"; BG2="#2a2a3e"; BG3="#313145"; ACCENT="#7c6af7"; ACCENT2="#a89cf7"
GREEN="#3dd68c"; RED="#f77c6a"; YELLOW="#f7c86a"; FG="#e0e0f0"; FG2="#a0a0c0"
ENTRY_BG="#252538"; BORDER="#3a3a5c"; CYAN="#6af7e0"; ORANGE="#f7a06a"

# ─────────────────────────────────────────────────────────────────────────────
#  G-code metadata reader
# ─────────────────────────────────────────────────────────────────────────────
class GCodeMeta:
    """Reads key settings embedded in OrcaSlicer/PrusaSlicer G-code."""
    def __init__(self):
        self.nozzle_diameter      = None
        self.layer_height         = None
        self.perimeter_width      = None
        self.solid_infill_width   = None
        self.filament_type        = None
        self.bridge_speed         = None
        self.detected_material    = "PLA+"

    def load(self, filepath: Path):
        self.__init__()
        try:
            lines = []
            # Read header (first 200 lines) + footer (last 600 lines)
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
            lines = all_lines[:200] + all_lines[-600:]

            kmap = {
                "nozzle_diameter":              "nozzle_diameter",
                "layer_height":                 "layer_height",
                "perimeter_extrusion_width":    "perimeter_width",
                "perimeters_extrusion_width":   "perimeter_width",
                "solid_infill_extrusion_width": "solid_infill_width",
                "solid_infill_extrusion_width": "solid_infill_width",
                "filament_type":                "filament_type",
                "bridge_speed":                 "bridge_speed",
            }
            for line in lines:
                if not line.startswith(";"):
                    continue
                stripped = line.lstrip(";").strip()
                if "= " not in stripped:
                    continue
                parts = stripped.split("= ", 1)
                if len(parts) != 2:
                    continue
                raw_key = parts[0].strip().lower().replace(" ", "_")
                raw_val = parts[1].strip()
                if raw_val.endswith("mm"):
                    raw_val = raw_val[:-2]
                attr = kmap.get(raw_key)
                if attr and getattr(self, attr) is None:
                    try:
                        from ast import literal_eval
                        setattr(self, attr, literal_eval(raw_val))
                    except Exception:
                        setattr(self, attr, raw_val)

            # Also check header-style lines like: "; perimeters extrusion width = 0.45mm"
            for line in lines:
                if not line.startswith(";"):
                    continue
                m = re.match(r';\s*perimeters extrusion width\s*=\s*([\d.]+)', line)
                if m and self.perimeter_width is None:
                    self.perimeter_width = float(m.group(1))
                m = re.match(r';\s*solid infill extrusion width\s*=\s*([\d.]+)', line)
                if m and self.solid_infill_width is None:
                    self.solid_infill_width = float(m.group(1))

            # Detect material
            ft = str(self.filament_type or "").upper()
            if "PETG" in ft:   self.detected_material = "PETG"
            elif "ABS"  in ft: self.detected_material = "ABS"
            elif "TPU"  in ft: self.detected_material = "TPU"
            elif "PLA"  in ft: self.detected_material = "PLA+"
            else:              self.detected_material = "PLA+"

        except Exception:
            pass

    def derive_params(self) -> dict:
        """Return a dict of parameter values auto-derived from the G-code."""
        derived = {}
        nd = self.nozzle_diameter or 0.4
        pw = self.perimeter_width or (nd * 1.1)
        if pw and pw > 0:
            derived["MaxDistanceFromPerimeter"] = round(2 * pw, 3)
        return derived

    def summary(self) -> str:
        parts = []
        if self.nozzle_diameter: parts.append(f"Nozzle: {self.nozzle_diameter}mm")
        if self.layer_height:    parts.append(f"Layer: {self.layer_height}mm")
        if self.perimeter_width: parts.append(f"Perimeter: {self.perimeter_width}mm")
        mat = self.detected_material or "unknown"
        parts.append(f"Material: {mat}")
        return "   |   ".join(parts) if parts else "No metadata found"


# ─────────────────────────────────────────────────────────────────────────────
#  G-code preview parser
# ─────────────────────────────────────────────────────────────────────────────
def parse_preview_data(gcode_path: Path):
    """
    Parse processed G-code for the arc preview.
    Returns: (arcs, outer_wall, inner_wall, overhang_wall, layer_z)
    Each is a list of (x,y) point lists.
    """
    arcs, outer_wall, inner_wall, overhang_wall = [], [], [], []
    current_arc, current_outer, current_inner, current_overhang = [], [], [], []
    layer_z = None
    cx, cy = 0.0, 0.0
    current_type = None
    in_arc_section = False

    try:
        with open(gcode_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        # Find the arc infill layer
        arc_layer_start = None
        for i, line in enumerate(lines):
            if ";TYPE:Arc infill" in line:
                arc_layer_start = i
                break
        if arc_layer_start is None:
            return [], [], [], [], None

        # Find layer Z
        for i in range(arc_layer_start, max(0, arc_layer_start - 50), -1):
            m = re.match(r';Z:([\d.]+)', lines[i])
            if m:
                layer_z = float(m.group(1))
                break

        # Find the layer start (LAYER_CHANGE before arc section)
        layer_start = 0
        for i in range(arc_layer_start, max(0, arc_layer_start - 500), -1):
            if ";LAYER_CHANGE" in lines[i]:
                layer_start = i
                break

        def get_xy(line):
            nonlocal cx, cy
            xm = re.search(r'X([-\d.]+)', line)
            ym = re.search(r'Y([-\d.]+)', line)
            if xm: cx = float(xm.group(1))
            if ym: cy = float(ym.group(1))
            return cx, cy

        # Scan from layer start to end of special cooling zone
        scan_end = min(len(lines), arc_layer_start + 8000)
        current_type = None
        current_arc = []

        for i in range(layer_start, scan_end):
            line = lines[i]

            if ";TYPE:" in line:
                # Save previous arc segment
                if current_type == "arc" and current_arc:
                    arcs.append(current_arc)
                    current_arc = []
                elif current_type == "outer" and current_outer:
                    outer_wall.append(current_outer)
                    current_outer = []
                elif current_type == "inner" and current_inner:
                    inner_wall.append(current_inner)
                    current_inner = []
                elif current_type == "overhang" and current_overhang:
                    overhang_wall.append(current_overhang)
                    current_overhang = []

                if   "Arc infill"   in line: current_type = "arc"
                elif "Outer wall"   in line or "External" in line: current_type = "outer"
                elif "Inner wall"   in line: current_type = "inner"
                elif "Overhang"     in line: current_type = "overhang"
                else: current_type = "other"
                continue

            if ";Arc " in line and current_type == "arc":
                if current_arc:
                    arcs.append(current_arc)
                    current_arc = []
                continue

            if not line.startswith("G1"):
                continue

            em = re.search(r'E([-\d.]+)', line)
            x, y = get_xy(line)
            has_extrusion = em and float(em.group(1)) > 0

            if current_type == "arc" and has_extrusion:
                current_arc.append((x, y))
            elif current_type == "outer" and has_extrusion:
                current_outer.append((x, y))
            elif current_type == "inner" and has_extrusion:
                current_inner.append((x, y))
            elif current_type == "overhang" and has_extrusion:
                current_overhang.append((x, y))

        # Flush remaining
        if current_arc:    arcs.append(current_arc)
        if current_outer:  outer_wall.append(current_outer)
        if current_inner:  inner_wall.append(current_inner)
        if current_overhang: overhang_wall.append(current_overhang)

    except Exception as e:
        print(f"Preview parse error: {e}")

    return arcs, outer_wall, inner_wall, overhang_wall, layer_z


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────
def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f: return json.load(f)
        except Exception: pass
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f: json.dump(data, f, indent=2)

def patch_script(params: dict, output_path: str) -> Path:
    with open(ARC_SCRIPT, "r", encoding="utf-8") as f:
        content = f.read()

    def replace_var(text, name, value):
        pattern = rf'^(\s*{re.escape(name)}\s*=\s*)([^#\n]*)(\s*#?.*)?$'
        new_val = repr(value) if isinstance(value, str) else str(value)
        def repl(m):
            comment = m.group(3) if m.group(3) else ""
            return f"{m.group(1)}{new_val}{comment}"
        return re.sub(pattern, repl, text, flags=re.MULTILINE)

    for name, value in params.items():
        content = replace_var(content, name, value)

    safe_out = str(output_path).replace("\\", "/")
    content = replace_var(content, "Path2Output", safe_out)

    temp = SCRIPT_DIR / "_arc_patched_temp.py"
    with open(temp, "w", encoding="utf-8") as f:
        f.write(content)
    return temp


# ─────────────────────────────────────────────────────────────────────────────
#  Preview Window
# ─────────────────────────────────────────────────────────────────────────────
class PreviewWindow(tk.Toplevel):
    def __init__(self, parent, gcode_path: Path, coverage_pct=None):
        super().__init__(parent)
        self.title("ArcSlicer — Arc Preview")
        self.configure(bg=BG)
        w, h = 700, 620
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.resizable(True, True)

        # Header
        hdr = tk.Frame(self, bg=BG2, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  🔵  Arc Layer Preview",
                 font=("Segoe UI", 13, "bold"), bg=BG2, fg=ACCENT2).pack(side="left", padx=12)

        if coverage_pct is not None:
            col = GREEN if coverage_pct >= 90 else YELLOW if coverage_pct >= 70 else RED
            tk.Label(hdr, text=f"Coverage: {coverage_pct:.0f}%",
                     font=("Segoe UI", 10, "bold"), bg=BG2, fg=col).pack(side="right", padx=12)

        if not HAS_MPL:
            tk.Label(self,
                text="matplotlib not available.\nRun: pip install matplotlib",
                bg=BG, fg=RED, font=("Segoe UI", 11)).pack(expand=True)
            return

        # Parse data
        status_var = tk.StringVar(value="Parsing G-code…")
        tk.Label(self, textvariable=status_var, bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(pady=4)

        self.after(100, lambda: self._load(gcode_path, status_var))

    def _load(self, gcode_path, status_var):
        try:
            arcs, outer, inner, overhang, layer_z = parse_preview_data(gcode_path)

            if not arcs:
                status_var.set("No arc data found in this G-code.")
                return

            z_str = f"  (Z = {layer_z:.2f} mm)" if layer_z else ""
            status_var.set(f"{len(arcs)} arcs detected{z_str}")

            fig = Figure(figsize=(6.5, 5.5), facecolor="#0d0d1a")
            ax  = fig.add_subplot(111, facecolor="#0d0d1a")

            # Draw inner walls (faint)
            for seg in inner:
                if seg:
                    xs, ys = zip(*seg)
                    ax.plot(xs, ys, color="#444466", linewidth=0.5, alpha=0.6)

            # Draw outer walls
            for seg in outer:
                if seg:
                    xs, ys = zip(*seg)
                    ax.plot(xs, ys, color="#8888bb", linewidth=1.2, alpha=0.9)

            # Draw overhang perimeters
            for seg in overhang:
                if seg:
                    xs, ys = zip(*seg)
                    ax.plot(xs, ys, color=CYAN, linewidth=1.5, alpha=0.9)

            # Draw arcs (rainbow by arc index)
            cmap = cm.get_cmap("plasma", max(len(arcs), 1))
            for i, arc in enumerate(arcs):
                if len(arc) < 2:
                    continue
                xs, ys = zip(*arc)
                color  = cmap(i / max(len(arcs) - 1, 1))
                ax.plot(xs, ys, color=color, linewidth=1.0, alpha=0.85)

            # Dot at first arc start
            if arcs and arcs[0]:
                ax.plot(*arcs[0][0], "o", color=GREEN, markersize=5, zorder=5)

            # Styling
            ax.set_aspect("equal")
            ax.tick_params(colors=FG2, labelsize=7)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)
            ax.set_title(f"Arc infill layer — {len(arcs)} arcs",
                         color=ACCENT2, fontsize=10, pad=8)
            ax.set_xlabel("X (mm)", color=FG2, fontsize=8)
            ax.set_ylabel("Y (mm)", color=FG2, fontsize=8)
            fig.tight_layout(pad=1.5)

            # Legend
            legend_items = [
                mpatches.Patch(color="#8888bb",  label="Outer wall"),
                mpatches.Patch(color=CYAN,        label="Overhang wall"),
                mpatches.Patch(color="#c060ff",   label="Arcs (inner→outer)"),
                mpatches.Patch(color=GREEN,        label="Arc start point"),
            ]
            ax.legend(handles=legend_items, loc="upper right",
                      facecolor=BG2, edgecolor=BORDER,
                      labelcolor=FG2, fontsize=7)

            canvas = FigureCanvasTkAgg(fig, master=self)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=(0, 8))

        except Exception as e:
            status_var.set(f"Preview error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main Application
# ─────────────────────────────────────────────────────────────────────────────
class ArcSlicerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ArcSlicer v2 — Arc Overhang Post-Processor")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(740, 650)
        w, h = 820, 900
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self.input_file    = tk.StringVar()
        self.output_file   = tk.StringVar()
        self.material_var  = tk.StringVar(value="PLA+")
        self.param_vars    = {}
        self.auto_vars     = {}   # param_name -> BooleanVar (is auto-derived)
        self.processing    = False
        self.meta          = GCodeMeta()
        self.last_output   = None
        self.coverage_pct  = None

        self._apply_styles()
        self._build_ui()
        self._load_config_into_ui()
        self._check_script_present()

    # ── Styles ────────────────────────────────────────────────────────────────
    def _apply_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG,
            fieldbackground=ENTRY_BG, bordercolor=BORDER,
            troughcolor=BG2, insertcolor=FG)
        s.configure("TFrame",     background=BG)
        s.configure("TLabel",     background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("Dim.TLabel", background=BG, foreground=FG2, font=("Segoe UI", 9))
        s.configure("TLabelframe", background=BG2, foreground=ACCENT2,
            relief="flat", bordercolor=BORDER, borderwidth=1)
        s.configure("TLabelframe.Label", background=BG2, foreground=ACCENT2,
            font=("Segoe UI", 10, "bold"))
        s.configure("TCombobox", fieldbackground=ENTRY_BG, foreground=FG,
            selectbackground=ACCENT, selectforeground="#fff")
        s.map("TCombobox", fieldbackground=[("readonly", ENTRY_BG)],
              foreground=[("readonly", FG)])
        s.configure("Process.TButton", background=ACCENT, foreground="#fff",
            font=("Segoe UI", 12, "bold"), padding=(20, 10), relief="flat")
        s.map("Process.TButton",
            background=[("active", ACCENT2), ("disabled", BG3)],
            foreground=[("disabled", FG2)])
        s.configure("Preview.TButton", background=BG3, foreground=CYAN,
            font=("Segoe UI", 10, "bold"), padding=(12, 8), relief="flat")
        s.map("Preview.TButton",
            background=[("active", BORDER), ("disabled", BG3)],
            foreground=[("active", CYAN), ("disabled", FG2)])
        s.configure("Small.TButton", background=BG3, foreground=FG2,
            font=("Segoe UI", 9), padding=(6, 4), relief="flat")
        s.map("Small.TButton",
            background=[("active", BORDER)], foreground=[("active", FG)])
        s.configure("TScrollbar", background=BG3, troughcolor=BG2,
            arrowcolor=FG2, bordercolor=BG)

    # ── UI Layout ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg=BG2, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="  🔵  ArcSlicer  v2",
                 font=("Segoe UI", 15, "bold"), bg=BG2, fg=ACCENT2).pack(side="left", padx=14)
        tk.Label(header, text="Arc Overhang Post-Processor  |  Qidi Q1 Pro",
                 font=("Segoe UI", 9), bg=BG2, fg=FG2).pack(side="left")
        self.status_label = tk.Label(header, text="● Ready",
            font=("Segoe UI", 9, "bold"), bg=BG2, fg=GREEN)
        self.status_label.pack(side="right", padx=14)

        # Warning banner
        self.warn_frame = tk.Frame(self, bg=YELLOW, pady=5)
        self.warn_label = tk.Label(self.warn_frame,
            text="⚠  Arc script not found — run setup.bat first, then restart ArcSlicer.",
            bg=YELLOW, fg="#1a1a00", font=("Segoe UI", 9, "bold"))
        self.warn_label.pack()

        # ── Notebook tabs ──────────────────────────────────────────────────────
        nb_frame = tk.Frame(self, bg=BG)
        nb_frame.pack(fill="both", expand=True, padx=12, pady=6)

        self.notebook = ttk.Notebook(nb_frame)
        self.notebook.pack(fill="both", expand=True)

        style = ttk.Style()
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab",
            background=BG3, foreground=FG2,
            font=("Segoe UI", 10), padding=(14, 6))
        style.map("TNotebook.Tab",
            background=[("selected", BG2)],
            foreground=[("selected", ACCENT2)])

        # Tab 1: Quick Setup
        self.tab_quick  = tk.Frame(self.notebook, bg=BG)
        # Tab 2: Parameters
        self.tab_params = tk.Frame(self.notebook, bg=BG)

        self.notebook.add(self.tab_quick,  text="  ⚡  Quick Setup  ")
        self.notebook.add(self.tab_params, text="  🔧  All Parameters  ")

        self._build_quick_tab(self.tab_quick)
        self._build_params_tab(self.tab_params)

        # Bottom bar
        bottom = tk.Frame(self, bg=BG2, pady=10)
        bottom.pack(fill="x", side="bottom")

        ttk.Button(bottom, text="↺  Reset Defaults",
            style="Small.TButton", command=self._reset_defaults).pack(side="left", padx=(14,4))
        ttk.Button(bottom, text="💾  Save Settings",
            style="Small.TButton", command=self._save_settings).pack(side="left", padx=4)

        self.preview_btn = ttk.Button(bottom, text="👁  Preview Arcs",
            style="Preview.TButton", command=self._open_preview, state="disabled")
        self.preview_btn.pack(side="right", padx=(4, 14))

        self.process_btn = ttk.Button(bottom, text="▶  PROCESS G-CODE",
            style="Process.TButton", command=self._start_processing)
        self.process_btn.pack(side="right", padx=4)

        # Console log
        log_frame = ttk.LabelFrame(self, text=" 🖥  Console Output ", padding=6)
        log_frame.pack(fill="x", padx=12, pady=(0, 4), side="bottom")
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=7, bg="#0d0d1a", fg="#c0ffc0",
            insertbackground=FG, font=("Consolas", 9),
            relief="flat", state="disabled")
        self.log_text.pack(fill="x")
        self.log_text.tag_config("error",   foreground=RED)
        self.log_text.tag_config("success", foreground=GREEN)
        self.log_text.tag_config("warn",    foreground=YELLOW)
        self.log_text.tag_config("info",    foreground=ACCENT2)

    # ── Quick Setup Tab ────────────────────────────────────────────────────────
    def _build_quick_tab(self, parent):
        # File I/O
        io = ttk.LabelFrame(parent, text=" 📂  Files ", padding=12)
        io.pack(fill="x", padx=8, pady=(10,4))
        self._file_row(io, "Input .gcode:", self.input_file,  self._browse_input,  0)
        self._file_row(io, "Output file:",  self.output_file, self._browse_output, 1)
        ttk.Label(io,
            text="💡 Drop .gcode into the input\\ folder, then Browse to select it quickly.",
            style="Dim.TLabel", wraplength=680).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6,0))

        # Smart panel
        smart = ttk.LabelFrame(parent, text=" 🧠  Smart Settings ", padding=12)
        smart.pack(fill="x", padx=8, pady=4)

        # Material selector
        mat_row = tk.Frame(smart, bg=BG2)
        mat_row.pack(fill="x", pady=4)
        tk.Label(mat_row, text="Material:", bg=BG2, fg=FG,
                 font=("Segoe UI", 10, "bold"), width=14, anchor="e").pack(side="left", padx=(4,8))
        mat_cb = ttk.Combobox(mat_row, textvariable=self.material_var,
                              values=list(MATERIAL_PRESETS.keys()),
                              state="readonly", width=12)
        mat_cb.pack(side="left")
        mat_cb.bind("<<ComboboxSelected>>", self._on_material_change)

        self.mat_note = tk.Label(mat_row, text="", bg=BG2, fg=FG2,
                                 font=("Segoe UI", 9), anchor="w")
        self.mat_note.pack(side="left", padx=12)

        ttk.Button(mat_row, text="Apply Preset →",
                   style="Small.TButton",
                   command=self._apply_material_preset).pack(side="left", padx=8)

        # Detected metadata
        self.meta_label = tk.Label(smart,
            text="Load a G-code file to see printer settings",
            bg=BG2, fg=FG2, font=("Segoe UI", 9))
        self.meta_label.pack(anchor="w", padx=4, pady=(4,2))

        # Auto-derived params display
        self.derived_frame = tk.Frame(smart, bg=BG2)
        self.derived_frame.pack(fill="x", pady=(2,4))
        self.derived_labels = {}

        # Key quick-access params (most important ones at top)
        quick_params = ["ArcPrintSpeed", "ArcExtrusionMultiplier",
                        "ArcFanSpeed", "ArcCenterOffset"]
        quick_frame = tk.Frame(smart, bg=BG2)
        quick_frame.pack(fill="x")
        tk.Label(quick_frame, text="Key overrides:", bg=BG2, fg=ACCENT2,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=4, pady=(4,2))

        for key in quick_params:
            if key not in PARAMETERS:
                continue
            label, default, unit, tooltip, mn, mx, typ, auto = PARAMETERS[key]
            row = tk.Frame(quick_frame, bg=BG2)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, width=26, anchor="e",
                     bg=BG2, fg=FG, font=("Segoe UI", 9)).pack(side="left", padx=(4,8))
            # Re-use same StringVar as params tab
            if key not in self.param_vars:
                self.param_vars[key] = tk.StringVar(value=str(default))
            ent = tk.Entry(row, textvariable=self.param_vars[key], width=9,
                           bg=ENTRY_BG, fg=FG, insertbackground=FG,
                           relief="flat", font=("Segoe UI", 9),
                           highlightthickness=1, highlightbackground=BORDER,
                           highlightcolor=ACCENT)
            ent.pack(side="left")
            tk.Label(row, text=unit, width=10, anchor="w",
                     bg=BG2, fg=FG2, font=("Segoe UI", 8)).pack(side="left", padx=4)
            self._bind_tooltip(row, tooltip)

        # Update material note
        self._on_material_change()

    def _file_row(self, parent, label, var, cmd, row):
        ttk.Label(parent, text=label, width=14, anchor="e").grid(
            row=row, column=0, padx=(0,8), pady=4, sticky="e")
        entry = ttk.Entry(parent, textvariable=var, width=58)
        entry.grid(row=row, column=1, padx=(0,6), pady=4, sticky="ew")
        ttk.Button(parent, text="Browse…", style="Small.TButton",
                   command=cmd).grid(row=row, column=2, pady=4)
        parent.columnconfigure(1, weight=1)

    # ── Parameters Tab ─────────────────────────────────────────────────────────
    def _build_params_tab(self, parent):
        canvas    = tk.Canvas(parent, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.pframe = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0,0), window=self.pframe, anchor="nw")
        self.pframe.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
            lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        for group_name, keys in PARAM_GROUPS.items():
            grp = ttk.LabelFrame(self.pframe, text=f"  {group_name}  ", padding=(12,8))
            grp.pack(fill="x", pady=(8,0), padx=4)
            for key in keys:
                if key not in PARAMETERS: continue
                label, default, unit, tooltip, mn, mx, typ, auto = PARAMETERS[key]
                row_frame = tk.Frame(grp, bg=BG2)
                row_frame.pack(fill="x", pady=3)
                lbl = tk.Label(row_frame, text=label, width=34, anchor="w",
                               bg=BG2, fg=FG, font=("Segoe UI", 9))
                lbl.pack(side="left", padx=(4,8))
                if key not in self.param_vars:
                    self.param_vars[key] = tk.StringVar(value=str(default))
                auto_bv = tk.BooleanVar(value=auto)
                self.auto_vars[key] = auto_bv
                ent = tk.Entry(row_frame, textvariable=self.param_vars[key], width=10,
                               bg=ENTRY_BG, fg=FG, insertbackground=FG,
                               relief="flat", font=("Segoe UI", 9),
                               highlightthickness=1, highlightbackground=BORDER,
                               highlightcolor=ACCENT)
                ent.pack(side="left")
                tk.Label(row_frame, text=unit, width=14, anchor="w",
                         bg=BG2, fg=FG2, font=("Segoe UI", 8)).pack(side="left", padx=(4,0))
                if auto:
                    auto_lbl = tk.Label(row_frame, text="AUTO",
                                        bg=BORDER, fg=CYAN,
                                        font=("Segoe UI", 7, "bold"),
                                        padx=4, relief="flat")
                    auto_lbl.pack(side="left", padx=4)
                tip = tk.Label(row_frame, text="?",
                               bg=BORDER, fg=ACCENT2,
                               font=("Segoe UI", 8, "bold"),
                               width=2, cursor="question_arrow",
                               relief="flat", padx=3)
                tip.pack(side="left", padx=4)
                self._bind_tooltip(tip, tooltip)
                tk.Label(row_frame, text=f"[{mn}–{mx}]",
                         bg=BG2, fg=FG2, font=("Segoe UI", 8)).pack(side="left", padx=2)

    # ── Tooltip ───────────────────────────────────────────────────────────────
    def _bind_tooltip(self, widget, text):
        tip_win = []
        def show(e):
            x = widget.winfo_rootx() + 24
            y = widget.winfo_rooty() + 16
            tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(tw, text=text, justify="left",
                     bg="#2a2a1e", fg="#f0f0c0",
                     font=("Segoe UI", 9),
                     relief="solid", borderwidth=1, padx=8, pady=6).pack()
            tip_win.append(tw)
        def hide(e):
            for tw in tip_win: tw.destroy()
            tip_win.clear()
        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # ── File browsing ──────────────────────────────────────────────────────────
    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Select input G-code",
            initialdir=str(INPUT_DIR),
            filetypes=[("G-code files","*.gcode *.g *.gco"),("All files","*.*")])
        if path:
            self.input_file.set(path)
            p = Path(path)
            out = OUTPUT_DIR / (p.stem + "_arc_processed" + p.suffix)
            self.output_file.set(str(out))
            self._read_metadata(Path(path))

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save processed G-code as…",
            initialdir=str(OUTPUT_DIR),
            defaultextension=".gcode",
            filetypes=[("G-code files","*.gcode"),("All files","*.*")])
        if path:
            self.output_file.set(path)

    def _read_metadata(self, filepath: Path):
        """Read G-code metadata and update smart UI."""
        self.meta.load(filepath)
        self.meta_label.configure(text=f"📄  {self.meta.summary()}")

        # Auto-set material if detected
        if self.meta.detected_material in MATERIAL_PRESETS:
            self.material_var.set(self.meta.detected_material)
            self._on_material_change()

        # Apply derived parameters
        derived = self.meta.derive_params()
        for key, val in derived.items():
            if key in self.param_vars and self.auto_vars.get(key, tk.BooleanVar()).get():
                self.param_vars[key].set(str(val))
                self._log(f"Auto-derived {key} = {val} (from G-code metadata)\n", "info")

        # Update derived labels
        for w in self.derived_frame.winfo_children():
            w.destroy()
        if self.meta.nozzle_diameter:
            items = [
                ("Nozzle", f"{self.meta.nozzle_diameter}mm"),
                ("Layer",  f"{self.meta.layer_height or '?'}mm"),
                ("Perimeter width", f"{self.meta.perimeter_width or '?'}mm"),
                ("ArcWidth (auto)", f"{self.meta.nozzle_diameter * 0.95:.3f}mm"),
            ]
            if self.meta.perimeter_width:
                pw = self.meta.perimeter_width
                items.append(("MaxDist (auto)", f"{2*pw:.3f}mm"))
                items.append(("ExtendInto (auto)", f"{1.5*pw:.3f}mm"))
            row = tk.Frame(self.derived_frame, bg=BG2)
            row.pack(fill="x", padx=4)
            for name, val in items:
                tk.Label(row, text=f"{name}: ", bg=BG2, fg=FG2,
                         font=("Segoe UI", 8)).pack(side="left")
                tk.Label(row, text=val + "   ", bg=BG2, fg=CYAN,
                         font=("Segoe UI", 8, "bold")).pack(side="left")

    # ── Material ──────────────────────────────────────────────────────────────
    def _on_material_change(self, event=None):
        mat = self.material_var.get()
        preset = MATERIAL_PRESETS.get(mat, {})
        note = preset.get("note", "")
        self.mat_note.configure(text=note)

    def _apply_material_preset(self):
        mat = self.material_var.get()
        preset = MATERIAL_PRESETS.get(mat, {})
        changed = []
        for key, val in preset.items():
            if key == "note": continue
            if key in self.param_vars:
                self.param_vars[key].set(str(val))
                changed.append(key)
        if changed:
            self._log(f"Applied {mat} preset: {', '.join(changed)}\n", "info")

    # ── Config ────────────────────────────────────────────────────────────────
    def _load_config_into_ui(self):
        cfg = load_config()
        for key, var in self.param_vars.items():
            if key in cfg:
                var.set(str(cfg[key]))
        if "material" in cfg:
            self.material_var.set(cfg["material"])

    def _save_settings(self):
        cfg = self._collect_params()
        cfg["material"] = self.material_var.get()
        save_config(cfg)
        self._log("Settings saved.\n", "success")

    def _reset_defaults(self):
        for key, var in self.param_vars.items():
            _, default, *_ = PARAMETERS[key]
            var.set(str(default))
        self._log("Parameters reset to defaults.\n", "info")

    def _collect_params(self) -> dict:
        params = {}
        for key, var in self.param_vars.items():
            _, default, _, _, mn, mx, typ, _ = PARAMETERS[key]
            raw = var.get().strip()
            try:
                val = typ(raw)
                if key != "MaxDistanceFromPerimeter":
                    val = max(typ(mn), min(typ(mx), val))
            except ValueError:
                val = default
            params[key] = val
        return params

    # ── Script check ──────────────────────────────────────────────────────────
    def _check_script_present(self):
        if not ARC_SCRIPT.exists():
            self.warn_frame.pack(fill="x")
            self.process_btn.state(["disabled"])
            self._set_status("⚠ Script missing — run setup.bat", YELLOW)
        else:
            self._log(f"Arc script found: {ARC_SCRIPT.name}\n", "success")
            self._log(f"Python: {sys.executable}\n", "info")
            if HAS_MPL:
                self._log("Matplotlib available — arc preview enabled.\n", "info")
            else:
                self._log("Matplotlib not found — preview disabled. Run: pip install matplotlib\n", "warn")

    # ── Processing ────────────────────────────────────────────────────────────
    def _start_processing(self):
        if self.processing: return
        input_path  = self.input_file.get().strip()
        output_path = self.output_file.get().strip()

        if not input_path:
            messagebox.showwarning("No Input File", "Please select a .gcode file.", parent=self)
            return
        if not Path(input_path).exists():
            messagebox.showerror("File Not Found", f"Not found:\n{input_path}", parent=self)
            return
        if not output_path:
            p = Path(input_path)
            output_path = str(OUTPUT_DIR / (p.stem + "_arc_processed" + p.suffix))
            self.output_file.set(output_path)

        # Re-read metadata if not loaded yet
        if self.meta.nozzle_diameter is None:
            self._read_metadata(Path(input_path))

        params = self._collect_params()
        self.processing  = True
        self.last_output = None
        self.coverage_pct = None
        self.preview_btn.state(["disabled"])
        self.process_btn.state(["disabled"])
        self._set_status("⟳ Processing…", YELLOW)
        self._log("\n" + "─"*50 + "\n", "info")
        self._log(f"Input:  {input_path}\n", "info")
        self._log(f"Output: {output_path}\n", "info")
        self._log("Patching arc script with your settings…\n")

        threading.Thread(
            target=self._run_processing,
            args=(input_path, output_path, params),
            daemon=True).start()

    def _run_processing(self, input_path, output_path, params):
        temp_script = temp_gcode = None
        try:
            temp_gcode = SCRIPT_DIR / "_arc_input_temp.gcode"
            shutil.copy2(input_path, temp_gcode)
            temp_script = patch_script(params, output_path)
            self._log("Running arc overhang algorithm…\n")

            proc = subprocess.Popen(
                [sys.executable, str(temp_script), str(temp_gcode)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=str(SCRIPT_DIR))

            for line in proc.stdout:
                tag = "error"   if ("error" in line.lower() or "exception" in line.lower()) \
                     else "warn" if "warn"  in line.lower() \
                     else None
                self._log(line, tag)
                # Detect coverage from script output
                m = re.search(r'(\d+(?:\.\d+)?)\s*%.*fill', line, re.IGNORECASE)
                if m:
                    self.coverage_pct = float(m.group(1))

            proc.wait()
            out = Path(output_path)

            # If script didn't write to output, copy from temp
            if not out.exists() and temp_gcode.exists():
                shutil.copy2(temp_gcode, out)

            if out.exists():
                size_kb = out.stat().st_size // 1024
                self._log(f"\n✅ Done!  →  {out.name}  ({size_kb} KB)\n", "success")
                self.last_output = out
                self.after(0, self._on_processing_done, True)
            else:
                self._log("\n❌ Output file was not created. Check log above.\n", "error")
                self.after(0, self._on_processing_done, False)

        except Exception as exc:
            self._log(f"\n❌ Error: {exc}\n", "error")
            self.after(0, self._on_processing_done, False)
        finally:
            for f in [temp_script, temp_gcode]:
                if f and Path(str(f)).exists():
                    try: Path(str(f)).unlink()
                    except: pass
            self.processing = False
            self.after(0, lambda: self.process_btn.state(["!disabled"]))

    def _on_processing_done(self, success: bool):
        if success:
            self._set_status("✅ Done!", GREEN)
            if HAS_MPL and self.last_output:
                self.preview_btn.state(["!disabled"])
                self._log("Click  👁 Preview Arcs  to visualize the arc layer.\n", "info")
            # Coverage warning
            if self.coverage_pct is not None and self.coverage_pct < 90:
                self._log(
                    f"⚠ Coverage {self.coverage_pct:.0f}% < 90% — "
                    "try lowering ArcCenterOffset or MinArea.\n", "warn")
        else:
            self._set_status("❌ Failed", RED)

    # ── Preview ───────────────────────────────────────────────────────────────
    def _open_preview(self):
        if self.last_output and self.last_output.exists():
            PreviewWindow(self, self.last_output, self.coverage_pct)

    # ── Utilities ─────────────────────────────────────────────────────────────
    def _log(self, text, tag=None):
        def _append():
            self.log_text.configure(state="normal")
            if tag: self.log_text.insert("end", text, tag)
            else:   self.log_text.insert("end", text)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _append)

    def _set_status(self, text, color=FG):
        self.status_label.configure(text=text, fg=color)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ArcSlicerApp()
    app.mainloop()
