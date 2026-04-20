#!/usr/bin/env python3
"""Channel letter generator.

Features:
- Build geometry from SVG line art (path/polyline/polygon)
- Build geometry from font glyph outlines (requires matplotlib)
- Export face profile as DXF (LWPOLYLINE)
- Export 3D body as ASCII STL
- Always applies a lip feature unless explicitly overridden in code
- Save/load reusable presets in JSON
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tkinter as tk
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Iterable, List, Sequence, Tuple

Point = Tuple[float, float]
Triangle = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


@dataclass
class LetterOptions:
    depth: float = 75.0
    lip_height: float = 12.0
    lip_inset_scale: float = 0.94
    units: str = "mm"


class ChannelLetterError(RuntimeError):
    pass


def polygon_area(poly: Sequence[Point]) -> float:
    area = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def ensure_ccw(poly: Sequence[Point]) -> List[Point]:
    pts = list(poly)
    if polygon_area(pts) < 0:
        pts.reverse()
    return pts


def centroid(poly: Sequence[Point]) -> Point:
    x = sum(p[0] for p in poly) / len(poly)
    y = sum(p[1] for p in poly) / len(poly)
    return (x, y)


def scale_about(poly: Sequence[Point], c: Point, scale: float) -> List[Point]:
    cx, cy = c
    return [(cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in poly]


def triangulate_fan(poly: Sequence[Point], z: float, reverse: bool = False) -> List[Triangle]:
    tris: List[Triangle] = []
    p0 = poly[0]
    for i in range(1, len(poly) - 1):
        p1 = poly[i]
        p2 = poly[i + 1]
        tri = ((p0[0], p0[1], z), (p1[0], p1[1], z), (p2[0], p2[1], z))
        if reverse:
            tri = (tri[0], tri[2], tri[1])
        tris.append(tri)
    return tris


def wall_tris(poly_top: Sequence[Point], z_top: float, poly_bot: Sequence[Point], z_bot: float) -> List[Triangle]:
    tris: List[Triangle] = []
    n = len(poly_top)
    for i in range(n):
        j = (i + 1) % n
        t1 = (poly_top[i][0], poly_top[i][1], z_top)
        t2 = (poly_top[j][0], poly_top[j][1], z_top)
        b1 = (poly_bot[i][0], poly_bot[i][1], z_bot)
        b2 = (poly_bot[j][0], poly_bot[j][1], z_bot)
        tris.append((t1, b1, b2))
        tris.append((t1, b2, t2))
    return tris


def generate_channel_letter_mesh(outer_polygon: Sequence[Point], opts: LetterOptions) -> List[Triangle]:
    if len(outer_polygon) < 3:
        raise ChannelLetterError("Need at least 3 points for a valid polygon")

    outer = ensure_ccw(outer_polygon)
    c = centroid(outer)

    # Lip is always included by design.
    lip = scale_about(outer, c, opts.lip_inset_scale)

    z_face = 0.0
    z_lip = -opts.lip_height
    z_back = -opts.depth

    tris: List[Triangle] = []

    # face ring side (outer face down to lip)
    tris.extend(wall_tris(outer, z_face, outer, z_lip))
    # lip sloped wall (outer lip level down to inset back level)
    tris.extend(wall_tris(outer, z_lip, lip, z_lip))
    # channel walls (lip down to full depth)
    tris.extend(wall_tris(lip, z_lip, lip, z_back))

    # close front and back
    tris.extend(triangulate_fan(outer, z_face, reverse=False))
    tris.extend(triangulate_fan(lip, z_back, reverse=True))
    return tris


def normal(tri: Triangle) -> Tuple[float, float, float]:
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    mag = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / mag, ny / mag, nz / mag


def write_ascii_stl(path: Path, tris: Sequence[Triangle], name: str = "channel_letter") -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(f"solid {name}\n")
        for tri in tris:
            nx, ny, nz = normal(tri)
            f.write(f"  facet normal {nx:.8g} {ny:.8g} {nz:.8g}\n")
            f.write("    outer loop\n")
            for vx, vy, vz in tri:
                f.write(f"      vertex {vx:.8g} {vy:.8g} {vz:.8g}\n")
            f.write("    endloop\n")
            f.write("  endfacet\n")
        f.write(f"endsolid {name}\n")


def write_dxf_polyline(path: Path, poly: Sequence[Point]) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("0\nSECTION\n2\nENTITIES\n")
        f.write("0\nLWPOLYLINE\n8\n0\n90\n")
        f.write(f"{len(poly)}\n70\n1\n")
        for x, y in poly:
            f.write(f"10\n{x:.8g}\n20\n{y:.8g}\n")
        f.write("0\nENDSEC\n0\nEOF\n")


def parse_path_d_to_polyline(d: str) -> List[Point]:
    tokens = re.findall(r"[MLHVZmlhvz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    pts: List[Point] = []
    i = 0
    cmd = None
    x = y = 0.0
    sx = sy = 0.0

    def get_num() -> float:
        nonlocal i
        if i >= len(tokens):
            raise ChannelLetterError("Unexpected end of path data")
        val = float(tokens[i])
        i += 1
        return val

    while i < len(tokens):
        t = tokens[i]
        if re.fullmatch(r"[MLHVZmlhvz]", t):
            cmd = t
            i += 1
        elif cmd is None:
            raise ChannelLetterError("Path data missing initial command")

        if cmd in ("M", "L"):
            x, y = get_num(), get_num()
            if cmd == "M":
                sx, sy = x, y
                cmd = "L"
            pts.append((x, y))
        elif cmd in ("m", "l"):
            dx, dy = get_num(), get_num()
            x, y = x + dx, y + dy
            if cmd == "m":
                sx, sy = x, y
                cmd = "l"
            pts.append((x, y))
        elif cmd == "H":
            x = get_num()
            pts.append((x, y))
        elif cmd == "h":
            x += get_num()
            pts.append((x, y))
        elif cmd == "V":
            y = get_num()
            pts.append((x, y))
        elif cmd == "v":
            y += get_num()
            pts.append((x, y))
        elif cmd in ("Z", "z"):
            if pts and pts[0] != pts[-1]:
                pts.append((sx, sy))
            cmd = None
        else:
            raise ChannelLetterError(f"Unsupported path command: {cmd}")

    if pts and pts[0] == pts[-1]:
        pts.pop()
    return pts


def load_polygon_from_svg(svg_path: Path) -> List[Point]:
    tree = ET.parse(svg_path)
    root = tree.getroot()

    ns_trim = lambda tag: tag.split("}", 1)[-1] if "}" in tag else tag

    for elem in root.iter():
        tag = ns_trim(elem.tag)
        if tag == "polygon":
            raw = elem.attrib.get("points", "")
            nums = [float(x) for x in re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", raw)]
            pts = list(zip(nums[::2], nums[1::2]))
            if len(pts) >= 3:
                return pts
        if tag == "polyline":
            raw = elem.attrib.get("points", "")
            nums = [float(x) for x in re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", raw)]
            pts = list(zip(nums[::2], nums[1::2]))
            if len(pts) >= 3:
                return pts
        if tag == "path" and "d" in elem.attrib:
            pts = parse_path_d_to_polyline(elem.attrib["d"])
            if len(pts) >= 3:
                return pts

    raise ChannelLetterError("No usable polygon/polyline/path found in SVG")


def load_polygon_from_font(text: str, font: str | None = None) -> List[Point]:
    try:
        from matplotlib.font_manager import FontProperties
        from matplotlib.textpath import TextPath
    except Exception as exc:  # noqa: BLE001
        raise ChannelLetterError("Font mode requires matplotlib to be installed") from exc

    if not text.strip():
        raise ChannelLetterError("Text cannot be empty")

    fp = FontProperties(fname=font) if font else FontProperties()
    tp = TextPath((0, 0), text, size=100, prop=fp)
    polys = tp.to_polygons()
    if not polys:
        raise ChannelLetterError("Could not extract polygon from font/text")

    # Use largest contour by area as the face profile
    best = max(polys, key=lambda p: abs(polygon_area([(float(x), float(y)) for x, y in p])))
    return [(float(x), float(y)) for x, y in best]


def default_preset_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "channel-letter" / "presets.json"


def load_presets(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_presets(path: Path, presets: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(presets, f, indent=2)


def options_from_args(args: argparse.Namespace) -> LetterOptions:
    opts = LetterOptions()
    if args.preset:
        presets = load_presets(Path(args.presets_file))
        if args.preset not in presets:
            raise ChannelLetterError(f"Preset '{args.preset}' was not found")
        opts = LetterOptions(**presets[args.preset])

    for k in ("depth", "lip_height", "lip_inset_scale", "units"):
        val = getattr(args, k, None)
        if val is not None:
            setattr(opts, k, val)
    return opts


def cmd_build(args: argparse.Namespace) -> int:
    opts = options_from_args(args)

    if args.svg:
        poly = load_polygon_from_svg(Path(args.svg))
    else:
        poly = load_polygon_from_font(args.text, args.font)

    poly = ensure_ccw(poly)

    if args.out_dxf:
        write_dxf_polyline(Path(args.out_dxf), poly)

    if args.out_stl:
        tris = generate_channel_letter_mesh(poly, opts)
        write_ascii_stl(Path(args.out_stl), tris)

    if args.save_preset:
        presets_path = Path(args.presets_file)
        presets = load_presets(presets_path)
        presets[args.save_preset] = asdict(opts)
        save_presets(presets_path, presets)

    return 0


def cmd_preset(args: argparse.Namespace) -> int:
    pfile = Path(args.presets_file)
    presets = load_presets(pfile)

    if args.action == "list":
        for name, values in presets.items():
            print(name, json.dumps(values, sort_keys=True))
        return 0

    if args.action == "save":
        opts = LetterOptions(depth=args.depth, lip_height=args.lip_height, lip_inset_scale=args.lip_inset_scale, units=args.units)
        presets[args.name] = asdict(opts)
        save_presets(pfile, presets)
        print(f"Saved preset '{args.name}' -> {pfile}")
        return 0

    if args.action == "delete":
        presets.pop(args.name, None)
        save_presets(pfile, presets)
        print(f"Deleted preset '{args.name}'")
        return 0

    raise ChannelLetterError(f"Unknown preset action: {args.action}")


class ChannelLetterApp(tk.Tk):
    def __init__(self, presets_file: str) -> None:
        super().__init__()
        self.title("3-D Channel Letter Generator")
        self.geometry("760x560")
        self.presets_file = presets_file
        self.mode_var = tk.StringVar(value="svg")
        self.svg_var = tk.StringVar()
        self.text_var = tk.StringVar(value="A")
        self.font_var = tk.StringVar()
        self.out_dxf_var = tk.StringVar()
        self.out_stl_var = tk.StringVar()
        self.depth_var = tk.StringVar(value="75")
        self.lip_height_var = tk.StringVar(value="12")
        self.lip_scale_var = tk.StringVar(value="0.94")
        self.units_var = tk.StringVar(value="mm")
        self.preset_var = tk.StringVar()
        self.save_preset_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self._build_ui()
        self._load_presets_to_combo()

    def _build_ui(self) -> None:
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frm, text="Input mode").grid(row=row, column=0, sticky="w")
        mode = ttk.Frame(frm)
        mode.grid(row=row, column=1, sticky="w")
        ttk.Radiobutton(mode, text="SVG", variable=self.mode_var, value="svg").pack(side="left")
        ttk.Radiobutton(mode, text="Font/Text", variable=self.mode_var, value="font").pack(side="left")
        row += 1

        self._row_with_browse(frm, row, "SVG file", self.svg_var, self._pick_svg)
        row += 1

        ttk.Label(frm, text="Text").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.text_var).grid(row=row, column=1, sticky="ew", padx=(0, 6))
        row += 1

        self._row_with_browse(frm, row, "Font file", self.font_var, self._pick_font, file_types=[("Font files", "*.ttf *.otf"), ("All files", "*.*")])
        row += 1

        self._row_with_browse(frm, row, "Output DXF", self.out_dxf_var, self._pick_dxf_save)
        row += 1

        self._row_with_browse(frm, row, "Output STL", self.out_stl_var, self._pick_stl_save)
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1

        ttk.Label(frm, text="Depth").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.depth_var).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Lip height").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.lip_height_var).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Lip inset scale").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.lip_scale_var).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Units").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.units_var).grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1

        ttk.Label(frm, text="Load preset").grid(row=row, column=0, sticky="w")
        self.preset_combo = ttk.Combobox(frm, textvariable=self.preset_var, state="readonly")
        self.preset_combo.grid(row=row, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(frm, text="Apply preset", command=self._apply_preset).grid(row=row, column=2, sticky="e")
        row += 1

        ttk.Label(frm, text="Save preset as").grid(row=row, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.save_preset_var).grid(row=row, column=1, sticky="ew")
        row += 1

        actions = ttk.Frame(frm)
        actions.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Build Files", command=self._run_build).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Refresh Presets", command=self._load_presets_to_combo).grid(row=0, column=1, padx=6)
        row += 1

        ttk.Label(frm, textvariable=self.status_var).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _row_with_browse(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, on_browse, file_types=None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(parent, text="Browse", command=on_browse).grid(row=row, column=2, sticky="e")

    def _pick_svg(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("SVG files", "*.svg"), ("All files", "*.*")])
        if p:
            self.svg_var.set(p)

    def _pick_font(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("Font files", "*.ttf *.otf"), ("All files", "*.*")])
        if p:
            self.font_var.set(p)

    def _pick_dxf_save(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".dxf", filetypes=[("DXF", "*.dxf"), ("All files", "*.*")])
        if p:
            self.out_dxf_var.set(p)

    def _pick_stl_save(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".stl", filetypes=[("STL", "*.stl"), ("All files", "*.*")])
        if p:
            self.out_stl_var.set(p)

    def _load_presets_to_combo(self) -> None:
        presets = load_presets(Path(self.presets_file))
        names = sorted(presets.keys())
        self.preset_combo["values"] = names
        if names and not self.preset_var.get():
            self.preset_var.set(names[0])

    def _apply_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name:
            return
        presets = load_presets(Path(self.presets_file))
        cfg = presets.get(name)
        if not cfg:
            messagebox.showerror("Preset", f"Preset '{name}' not found")
            return
        self.depth_var.set(str(cfg.get("depth", 75.0)))
        self.lip_height_var.set(str(cfg.get("lip_height", 12.0)))
        self.lip_scale_var.set(str(cfg.get("lip_inset_scale", 0.94)))
        self.units_var.set(str(cfg.get("units", "mm")))
        self.status_var.set(f"Applied preset '{name}'")

    def _run_build(self) -> None:
        try:
            args = argparse.Namespace(
                preset=self.preset_var.get().strip() or None,
                presets_file=self.presets_file,
                depth=float(self.depth_var.get()),
                lip_height=float(self.lip_height_var.get()),
                lip_inset_scale=float(self.lip_scale_var.get()),
                units=self.units_var.get().strip() or "mm",
                out_dxf=self.out_dxf_var.get().strip() or None,
                out_stl=self.out_stl_var.get().strip() or None,
                save_preset=self.save_preset_var.get().strip() or None,
                svg=None,
                text=None,
                font=None,
            )
            if self.mode_var.get() == "svg":
                args.svg = self.svg_var.get().strip() or None
                if not args.svg:
                    raise ChannelLetterError("Please choose an SVG file")
            else:
                args.text = self.text_var.get()
                args.font = self.font_var.get().strip() or None
                if not args.text.strip():
                    raise ChannelLetterError("Please provide text for font mode")

            if not args.out_dxf and not args.out_stl:
                raise ChannelLetterError("Please choose at least one output file (DXF or STL)")

            cmd_build(args)
            if args.save_preset:
                self._load_presets_to_combo()
            self.status_var.set("Build complete")
            messagebox.showinfo("Success", "Files generated successfully.")
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Error: {exc}")
            messagebox.showerror("Build failed", str(exc))


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        app = ChannelLetterApp(args.presets_file)
    except tk.TclError as exc:
        raise ChannelLetterError(f"GUI could not start (display unavailable): {exc}") from exc
    app.mainloop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate channel letters from SVG or font outlines")
    parser.add_argument("--presets-file", default=str(default_preset_path()), help="Preset storage file")

    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="Build channel letter outputs")
    src = b.add_mutually_exclusive_group(required=True)
    src.add_argument("--svg", help="Input SVG line art file")
    src.add_argument("--text", help="Input text to generate from font")
    b.add_argument("--font", help="Path to .ttf/.otf font file for --text mode")

    b.add_argument("--preset", help="Load options preset by name")
    b.add_argument("--save-preset", help="Save final options as preset after build")
    b.add_argument("--depth", type=float, help="Total depth of channel body")
    b.add_argument("--lip-height", type=float, help="Lip height from face")
    b.add_argument("--lip-inset-scale", type=float, help="Scale factor for lip inset (0-1)")
    b.add_argument("--units", help="Units metadata, e.g. mm")

    b.add_argument("--out-dxf", help="Output DXF face file path")
    b.add_argument("--out-stl", help="Output STL file path")
    b.set_defaults(func=cmd_build)

    p = sub.add_parser("preset", help="Manage presets")
    psub = p.add_subparsers(dest="action", required=True)

    pl = psub.add_parser("list", help="List presets")
    pl.set_defaults(func=cmd_preset)

    ps = psub.add_parser("save", help="Save preset")
    ps.add_argument("name")
    ps.add_argument("--depth", type=float, required=True)
    ps.add_argument("--lip-height", type=float, required=True)
    ps.add_argument("--lip-inset-scale", type=float, required=True)
    ps.add_argument("--units", default="mm")
    ps.set_defaults(func=cmd_preset)

    pd = psub.add_parser("delete", help="Delete preset")
    pd.add_argument("name")
    pd.set_defaults(func=cmd_preset)

    g = sub.add_parser("gui", help="Launch desktop GUI")
    g.set_defaults(func=cmd_gui)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ChannelLetterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
