# Handoff: birdcage lumped ports + Nexxim co-sim scripts (AEDT 2026 R1, PyAEDT)

## Task state

The two deliverable scripts are **written and API-verified but never executed
against the live model**. Your job is to run them interactively against the
user's running AEDT 2026 R1 session, walk the acceptance checks, and fix
whatever reality disagrees with.

- `make_ports.py` — Part A: reads the hand-drawn reference sheet
  **`Rectangle1`** from the HFSS design, generates 2×32 = 64 port sheets
  (`PortSheet_U01..U32`, `PortSheet_L01..L32`) by analytic numpy rotation
  (upper ring: Z rotations; lower ring: 180° X-mirror about the bbox z-mid
  plane, then Z rotations), and assigns 50 Ω lumped ports `P_U01..P_L32` with
  explicit two-point integration lines (mid-edge to mid-edge across the gap).
  Idempotent: deletes/recreates its own ports+sheets on rerun; never touches
  imported coil geometry (only optionally sets `Rectangle1` non-model).
- `make_circuit.py` — Part B: deletes/recreates circuit design
  `BirdcageCosim`: dynamic-link subcircuit of the HFSS design, project
  variable `$C_ring` (preserved across reruns if it exists), 64 caps of value
  `$C_ring` pin→gnd in an 8×8 grid (page-port nets named after each HFSS
  port), interface ports `Drive1_0deg`/`Drive2_90deg` in parallel with the
  caps of `P_U01`/`P_U09` (auto-computed 90° apart), and an LNA setup with
  sweeps around 64 and 128 MHz.

Both scripts have a CONFIG block at the top. `PROJECT`/`DESIGN` are `None` =
attach to the active project/design of the running session. **Work on a copy
of the project — the scripts save at the end.**

## Environment facts (hard-won, don't rediscover)

- AEDT 2026 R1 install: `D:\Program Files\Ansys Inc\v261\AnsysEM`
  (`ansysedt.exe` at the root; bundled CPython 3.10 at
  `commonfiles\CPython\3_10\winx64\Release\python\python.exe`, **pyaedt NOT
  pre-installed** in it).
- The previous session ran Claude Code inside WSL2: Windows-exe interop was
  broken there (any `.exe` → `Invalid argument`) and WSL networking is NAT —
  that's why this session should be **Windows-native**.
- The scripts live in the WSL filesystem:
  `\\wsl.localhost\<distro>\home\taz5297\Development\rfsourceval\UpdateToAnsys2026Scripts\`
  (also copy them somewhere on `C:`/`D:` if `\\wsl.localhost` is slow).
- To run: any Windows CPython 3.10+ with `pip install pyaedt`. Attach to the
  running GUI session works if it exposes gRPC; PyAEDT discovers sessions by
  parsing `-grpcsrv` from the process command line, with a psutil TCP-listener
  fallback. If attach fails or PyAEDT starts spawning a *new* desktop,
  relaunch AEDT as: `ansysedt.exe -grpcsrv 50051` and reopen the project.
  Do **not** use AEDT's legacy `Tools > Run Script` (IronPython, no pyaedt);
  a plain terminal or the PyAEDT "Run Script" Automation-tab extension is fine.

## API verification already done (pyaedt 1.1.0 source, don't re-verify)

- `Hfss(project, design, ..., new_desktop=False)`;
  `lumped_port(assignment, integration_line=[[x0,y0,z0],[x1,y1,z1]],
  impedance, name, renormalize)` — 2-point list confirmed supported.
- `create_polyline(points, cover_surface=True, close_surface=True, name=...)`.
- `validate_full_design(ports=N)` → `(msgs, ok)`; `hfss.ports` → name list.
- `Object3d.model` is **read-only** now; the setter is `obj.is_model = False`.
- `add_subcircuit_dynamic_link(pyaedt_app=hfss, solution_name=None, name=...)`
  — defaults `solution_name` to `hfss.nominal_sweep`, so the HFSS design
  **must have a setup** (make_circuit.py creates `Setup1` @ 128 MHz if none).
- `create_capacitor(name, value, location)` does `set_property("C", value)` —
  a variable-name string (`"$C_ring"`) is a valid value.
- `CircuitPins.connect_to_component(other_pin, page_name=..., use_wire=...)`;
  `create_interface_port` returns an `Excitations` (has `.pins`);
  `create_gnd(location)`.
- `SetupCircuit.add_sweep_count("Freq", start, stop, count, units,
  override_existing_sweep)`; `circuit.create_setup(setup_type="NexximLNA")`.

## Suggested run sequence (mirrors the user's acceptance checks)

1. Confirm attach: model units printed, project/design names correct.
2. `make_ports.py` with `TEST_MODE = True` → only `P_U01` created. Have the
   user eyeball: sheet coincides with `Rectangle1`, integration line spans
   the gap (conductor edge → conductor edge). **Check the `[pairing]` log** —
   the edge pairing is chosen by an azimuthal-direction heuristic; if it
   picked wrong, set `INT_LINE_PAIRING` to `"01-23"` or `"12-30"` manually.
3. `TEST_MODE = False`, rerun (fully idempotent). Verify: 64 ports named
   `P_U01..P_U32, P_L01..P_L32`, `validate_full_design` ok.
4. Ask the user about lower-ring polarity: the X-mirror reverses the
   circumferential direction, so `P_Lxx` integration lines are oriented
   opposite to `P_Uxx` around the ring. `FLIP_LOWER_INT_LINES = True` + rerun
   if they want a consistent convention. **This is an open decision.**
5. `make_circuit.py`. Verify: dynamic link shows 64 pins, 64 caps referencing
   `$C_ring` (script counts and prints this), interface ports on
   `P_U01`/`P_U09`, netlist/validation passes.
6. Known soft spots to watch on first run (unverified against live AEDT):
   - vertex ordering of `Rectangle1` (assumed perimeter order from
     `obj.vertices`);
   - `active_sessions` gRPC discovery of a GUI-launched session;
   - schematic pin/wire geometry (locations are on the 2.54 mm grid but wire
     routing aesthetics may need tweaking — electrical connectivity via page
     ports named after each HFSS port is the load-bearing part);
   - `validate_simple()` on a circuit with an unsolved dynamic link may
     complain about missing solutions — that's expected until HFSS is solved.

## User preferences observed

- Wants concise plans *before* actions, not exploratory tool-call flailing.
- Values idempotency, deterministic names, everything logged to stdout,
  parameterization via `N_PER_RING` (no hardcoded 32/64 anywhere else).
- Never modify imported coil geometry; never overwrite the original project.
