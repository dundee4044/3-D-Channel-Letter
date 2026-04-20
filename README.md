# 3-D Channel Letter

A CLI tool to generate channel-letter fabrication outputs from either:
- an SVG line/polygon/path file, or
- text rendered from a font.

## What it does

- Generates a **face DXF** (`LWPOLYLINE`) for the letter profile.
- Generates a **3D STL** mesh for the channel body.
- Always includes a **lip** feature (controlled by options).
- Supports **presets** to save and reuse settings.

## Quick start

```bash
python3 channel_letter.py build \
  --svg letter.svg \
  --out-dxf face.dxf \
  --out-stl body.stl \
  --depth 75 \
  --lip-height 12 \
  --lip-inset-scale 0.94 \
  --save-preset shop_default
```

Build from text/font:

```bash
python3 channel_letter.py build \
  --text "A" \
  --font /path/to/font.ttf \
  --out-dxf face.dxf \
  --out-stl body.stl
```

## Presets

```bash
python3 channel_letter.py preset save default --depth 75 --lip-height 12 --lip-inset-scale 0.94
python3 channel_letter.py preset list
python3 channel_letter.py preset delete default
```

Preset file defaults to:
- Linux: `$XDG_CONFIG_HOME/channel-letter/presets.json`
- Fallback: `~/.config/channel-letter/presets.json`

## Notes

- SVG parsing is intentionally focused on line/polygon workflows and supports path commands `M/L/H/V/Z` (and lowercase relatives).
- Font mode requires `matplotlib`.
