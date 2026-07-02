# Claude working agreement (applies on Windows and WSL)

## Do not execute anything

Do NOT run pyaedt, drive Jupyter kernels, or execute Windows exes. Write and
fix code purely from API references; the user runs every script and pastes
the output back. Make scripts print rich diagnostics — pasted stdout is the
only feedback channel.

## API ground truth (in priority order)

1. AEDT 2026 R1 scripting guides (authoritative when AEDT silently ignores a
   call): `D:\Program Files\ANSYS Inc\v261\AnsysEM\Help\<product>\...ScriptingGuide.pdf`
   (WSL: `/mnt/d/Program Files/ANSYS Inc/v261/AnsysEM/Help/...`)
2. Installed pyaedt 1.1.0 source (readable as plain files):
   `C:\Users\taz5297\AppData\Roaming\.pyaedt_env\3_10\lib\site-packages\ansys\aedt\core\`
   (WSL: `/mnt/c/Users/taz5297/AppData/Roaming/.pyaedt_env/3_10/...`)
3. Online pyaedt docs: https://aedt.docs.pyansys.com

## AEDT 2026 R1 + pyaedt 1.1.0 gotchas (verified the hard way)

- 2026 R1 tends to SILENTLY NO-OP invalid legacy scripting calls instead of
  raising. When a native call "does nothing", check the 2026 R1 scripting
  guide before suspecting licensing or session state.
- `InsertDesign` for circuit designs changed (Circuit guide p. 6-45): type
  must be `"NexximCircuit"`/`"Circuit"` (legacy `"Circuit Design"` silently
  ignored) and the 3rd arg is a technology-file path (`""` = none), not a
  solution name. pyaedt 1.1.0 (and main, as of 2026-07) still sends the
  legacy form → "Failed to create design", app with `modeler=None`.
- pyaedt `design_list` parses `GetTopDesignList()` fragilely. Use
  `oproject.GetDesigns()` → `GetName()` for existence checks, and verify
  after `DeleteDesign`.
- Attaching to a GUI session: pass `aedt_process_id` as an **int** — the
  auto-generated PyAEDT-console notebook passes a string and trips pyaedt's
  "started a new session" check.
- Delete boundaries in batch via `hfss.oboundary.DeleteBoundaries(names)`;
  per-object `bd.delete()` revalidates the design each call and looks hung
  with 60+ ports.

## Project context

See `UpdateToAnsys2026Scripts/HANDOFF.md` for the task state, environment
facts, and acceptance checks of the birdcage co-sim scripts
(`make_ports.py`, `make_circuit.py`). Working project: `Prisma3T` at
`D:\Tayeb\Simulations\Ansys2026Testing\` (a copy — scripts save on exit).
User preferences: concise plan before actions, idempotent scripts,
deterministic names, everything parameterized (no hardcoded port counts).
