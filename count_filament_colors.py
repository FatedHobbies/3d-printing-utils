#!/usr/bin/env python3
"""
count_filament_colors.py
Reads a sliced .gcode.3mf file (Bambu Studio / OrcaSlicer) and reports how many
filaments / colors the print uses.

Usage (Windows 11):
    python count_filament_colors.py "C:\\path\\to\\model.gcode.3mf"
    - or drag a .gcode.3mf file onto this script
    - or double-click the script and pick a file in the dialog

Only uses the Python standard library - nothing to install.
"""

import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


# ---------------------------------------------------------------- helpers
def read_text(zf, name):
    try:
        return zf.read(name).decode("utf-8", errors="replace")
    except KeyError:
        return None


def find_member(zf, pattern):
    """Return archive member names matching a regex (case-insensitive)."""
    rx = re.compile(pattern, re.IGNORECASE)
    return sorted(n for n in zf.namelist() if rx.search(n))


def normalize_color(c):
    c = (c or "").strip().upper()
    if c and not c.startswith("#"):
        c = "#" + c
    # Bambu sometimes stores #RRGGBBAA - drop alpha for comparison
    if len(c) == 9:
        c = c[:7]
    return c or "?"


# --------------------------------------------- method 1: slice_info.config
def from_slice_info(zf):
    """Bambu/Orca write the filaments each plate actually uses here."""
    text = read_text(zf, "Metadata/slice_info.config")
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    plates = []
    for plate in root.iter("plate"):
        index = "?"
        for md in plate.findall("metadata"):
            if md.get("key") == "index":
                index = md.get("value")
        filaments = []
        for f in plate.findall("filament"):
            filaments.append({
                "slot": int(f.get("id", "0")),
                "type": f.get("type", "?"),
                "color": normalize_color(f.get("color")),
                "used_m": float(f.get("used_m") or 0),
                "used_g": float(f.get("used_g") or 0),
            })
        if filaments:
            plates.append({"plate": index, "filaments": filaments})
    return plates or None


# --------------------------------------------- method 2: scan the G-code
def project_colors(zf):
    """Slot colors/types from project_settings.config (JSON)."""
    text = read_text(zf, "Metadata/project_settings.config")
    if not text:
        return [], []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], []
    return data.get("filament_colour", []) or [], data.get("filament_type", []) or []


def from_gcode(zf):
    gcode_files = find_member(zf, r"\.gcode$")
    if not gcode_files:
        return None

    proj_colors, proj_types = project_colors(zf)
    plates = []
    tool_rx = re.compile(r"^\s*T(\d+)\b")
    m620_rx = re.compile(r"^\s*M620\s+S(\d+)A", re.IGNORECASE)

    for name in gcode_files:
        text = read_text(zf, name) or ""
        colors = list(proj_colors)
        types = list(proj_types)
        grams = []
        used_slots = set()

        for line in text.splitlines():
            s = line.strip()
            if s.startswith(";"):
                low = s.lower()
                if low.startswith("; filament_colour =") and not colors:
                    colors = [c for c in s.split("=", 1)[1].strip().split(";") if c]
                elif low.startswith("; filament_type =") and not types:
                    types = [t for t in s.split("=", 1)[1].strip().split(";") if t]
                elif low.startswith("; filament used [g] ="):
                    grams = [float(x) for x in re.findall(r"[\d.]+", s.split("=", 1)[1])]
                continue
            m = tool_rx.match(s) or m620_rx.match(s)
            if m:
                n = int(m.group(1))
                if n < 64:  # ignore special codes like T255 / T1000 / M620 S255
                    used_slots.add(n)

        # The per-filament usage line is the most reliable signal if present
        if grams and any(g > 0 for g in grams):
            used_slots = {i for i, g in enumerate(grams) if g > 0}
        if not used_slots:
            used_slots = {0}

        filaments = []
        for i in sorted(used_slots):
            filaments.append({
                "slot": i + 1,
                "type": types[i] if i < len(types) else "?",
                "color": normalize_color(colors[i]) if i < len(colors) else "?",
                "used_m": 0.0,
                "used_g": grams[i] if i < len(grams) else 0.0,
            })
        plate_no = re.search(r"plate_(\d+)", name)
        plates.append({"plate": plate_no.group(1) if plate_no else name,
                       "filaments": filaments})
    return plates


# ------------------------------------------------------------------ report
def analyze(path):
    with zipfile.ZipFile(path) as zf:
        plates = from_slice_info(zf)
        source = "slice_info.config"
        if not plates:
            plates = from_gcode(zf)
            source = "G-code scan"
    if not plates:
        raise ValueError("No sliced G-code or slice info found. Is this a "
                         "sliced .gcode.3mf (not a plain project .3mf)?")
    return plates, source


def print_report(path, plates, source):
    print(f"\nFile: {path}")
    print(f"(read from {source})\n")
    all_colors = set()
    all_slots = set()
    for p in plates:
        fils = p["filaments"]
        colors = {f["color"] for f in fils}
        all_colors |= colors
        all_slots |= {f["slot"] for f in fils}
        print(f"Plate {p['plate']}: {len(fils)} filament(s), {len(colors)} distinct color(s)")
        for f in fils:
            usage = []
            if f["used_m"]:
                usage.append(f"{f['used_m']:.2f} m")
            if f["used_g"]:
                usage.append(f"{f['used_g']:.2f} g")
            usage_txt = f"  ({', '.join(usage)})" if usage else ""
            print(f"   Slot {f['slot']:>2}: {f['type']:<8} {f['color']}{usage_txt}")
        print()
    if len(plates) > 1:
        print(f"All plates combined: {len(all_slots)} filament slot(s), "
              f"{len(all_colors)} distinct color(s)")
    else:
        n = len(all_colors)
        print(f"==> This print uses {n} color{'s' if n != 1 else ''} of filament.")


def pick_file():
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return input("Path to .gcode.3mf file: ").strip().strip('"')
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="Choose a sliced .gcode.3mf file",
        filetypes=[("Sliced 3MF", "*.gcode.3mf"), ("3MF files", "*.3mf"), ("All files", "*.*")],
    )
    root.destroy()
    return path


def main():
    interactive = len(sys.argv) < 2  # double-clicked / no argument
    paths = sys.argv[1:] or [pick_file()]
    exit_code = 0
    for p in paths:
        if not p:
            print("No file selected.")
            exit_code = 1
            continue
        path = Path(p)
        try:
            plates, source = analyze(path)
            print_report(path, plates, source)
        except FileNotFoundError:
            print(f"File not found: {path}")
            exit_code = 1
        except zipfile.BadZipFile:
            print(f"Not a valid 3MF (zip) file: {path}")
            exit_code = 1
        except ValueError as e:
            print(f"{path}: {e}")
            exit_code = 1
    if interactive or own_console_window():
        input("\nPress Enter to close...")
    sys.exit(exit_code)


def own_console_window():
    """True on Windows when the script got its own console window
    (double-click or drag-and-drop), so we keep it open for reading."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        buf = (ctypes.c_uint * 8)()
        count = ctypes.windll.kernel32.GetConsoleProcessList(buf, 8)
        return count <= 2  # python (+ py.exe launcher) only, no cmd/PowerShell
    except Exception:
        return False


if __name__ == "__main__":
    main()
