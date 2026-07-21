# scratch2python

Convert Scratch `.sb3` project files into native, runnable Python modules with 28 LLVM-style intermediate representation (IR) optimisation passes.

## Overview

`decompile.py` is a single-file (8,330 lines) transpiler that parses a Scratch project's block structure into a structured IR, applies a suite of provably-correct optimisations, and emits executable Python code per sprite — complete with a Tkinter-based runtime, game loop, costume/sound asset support, and keyboard/mouse input handling.

### Subcommands

| Command | Description |
|---|---|
| `decompile project.json` | Full pipeline: parse + emit (default) |
| `decompile project.json extract -o txt/` | Emit human-readable pseudo-code per sprite (debugging aid) |
| `decompile project.json parse -o ir.json` | Build intermediate-representation JSON |
| `decompile project.json emit -o decompiled/` | Generate Python modules from existing IR |

### IR Optimisation Passes (28 total)

1. Constant folding
2. Algebraic identity simplification
3. Strength reduction
4. Boolean literal normalisation
5. Comparison canonicalisation
6. Commutativity-aware hashing (CSE)
7. Common subexpression elimination (CSE)
8. Constant propagation
9. Dead expression elimination
10. No-op statement removal
11. Empty-block elimination
12. Dead-code after terminators
13. Redundant broadcast collapse
14. Duplicate statement collapse
15. Unreachable script removal
16. Loop-invariant code motion (LICM)
17. Loop strength reduction
18. Infinite-loop simplification
19. Empty-loop deletion
20. Procedure inlining
21. Dead procedure elimination
22. Tail-call / redundant call elimination
23. Argument default flattening
24. Unused variable/list elimination
25. Broadcast deduplication
26. Asset/constant hoisting (LICM)
27. CFG simplification
28. Nested repeat collapse

## Requirements

- **Python 3.10+**
- No third-party packages — uses only the Python standard library

Install:
```bash
pip install -r requirements.txt  # (no external deps — just a version pin)
```

## Usage

```bash
# Full pipeline: parse a Scratch project and emit Python modules
python3 decompile.py path/to/project.json

# Extract human-readable pseudo-code
python3 decompile.py path/to/project.json extract -o pseudo/

# Just parse to IR (for inspection)
python3 decompile.py path/to/project.json parse -o ir.json

# Emit Python from existing IR
python3 decompile.py ir.json emit -o output/

# Options
python3 decompile.py project.json --target-fps 30 --scale 1 --debug --force
```

## Output

The emitter produces a complete executable project:
- `main.py` — Tkinter-based game window with game loop, rendering, and I/O
- `00_Stage.py`, `01_SpriteName.py`, ... — per-target sprite modules
- `_engine.py` — shared runtime engine
- `_asset_data.json` — costume/sound metadata (separate from code for fast reloads)
- `data/` — extracted costume and sound assets

## License

MIT
