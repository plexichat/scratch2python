# scratch2python

Convert Scratch `.sb3` projects into native, runnable Python modules with 28 LLVM-style intermediate representation (IR) optimisation passes.

## Overview

`decompile.py` is a single-file (8,330 lines) transpiler that parses a Scratch project's block structure into a structured IR, applies a suite of provably-correct optimisations, and emits executable Python code per sprite — with a Tkinter-based runtime, game loop, costume/sound asset support, and keyboard/mouse input handling.

Pass it an `.sb3` file directly — the decompiler handles ZIP extraction internally, writing organised assets under `data/` and generating everything needed to run the project.

## Dependencies

### For the decompiler (this tool)

```
Pillow>=10.0.0
psutil>=5.9.0
resvg-py>=0.1.0
```

Install with:
```bash
pip install -r requirements.txt
```

### For decompiled output projects

Generated projects require:
```
numpy>=1.24.0
sounddevice>=0.4.6
```

The decompiler emits a `requirements.txt` alongside the generated modules.

## Usage

```bash
# Decompile an .sb3 file (default: parse + emit)
python3 decompile.py project.sb3

# Extract human-readable pseudo-code per sprite (debugging aid)
python3 decompile.py project.sb3 extract -o pseudo/

# Parse only — save IR to JSON for inspection
python3 decompile.py project.sb3 parse -o ir.json

# Emit Python from existing IR
python3 decompile.py ir.json emit -o output/

# With options
python3 decompile.py project.sb3 --target-fps 30 --scale 1 --debug --force
```

## Subcommands

| Subcommand | Description |
|---|---|
| `all` (default) | Full pipeline: extract .sb3 → parse IR → optimise → emit Python |
| `extract` | Write human-readable pseudo-code per sprite |
| `parse` | Build IR JSON from a project (without emitting code) |
| `emit` | Generate Python modules from an existing IR JSON file |

## Output structure

```
output/
├── main.py                 # Tkinter-based game window entry point
├── _engine.py              # Shared runtime engine
├── debug_panel.py          # Debug control/inspection window (optional)
├── _asset_data.json        # Costume/sound metadata (fast reload)
├── requirements.txt        # Runtime dependencies
├── data/                   # Extracted assets organised by target
│   ├── Stage/
│   │   ├── backdrop1.png
│   │   └── ...
│   └── Sprite1/
│       ├── costume1.svg
│       └── ...
├── 00_Stage.py
├── 01_Sprite1.py
└── ...
```

## Pipeline

1. **Extract** — SB3 (ZIP) opened in-memory; `project.json` parsed; assets written to `data/<target>/` with human-readable names
2. **Parse** — Block graph converted to structured IR via `IRParser`
3. **Optimise** — 28 passes including constant folding, CSE, LICM, procedure inlining, dead code elimination
4. **Emit** — Python modules generated per target, with a Tkinter game loop, rendering, and I/O

## License

MIT
