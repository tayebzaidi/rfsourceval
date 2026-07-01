"""
make_circuit.py — Part B of the birdcage co-sim scripting.

Builds a Nexxim circuit design ("BirdcageCosim") in the SAME project as the
HFSS birdcage design, containing:

  1. A dynamic-link subcircuit of the HFSS design (all P_Uxx / P_Lxx pins).
  2. A PROJECT variable  $C_ring  (created only if absent, so your tuned value
     survives reruns) and one capacitor of value $C_ring from every HFSS port
     pin to ground, laid out in a readable grid. Changing $C_ring once retunes
     every capacitor.
  3. Two interface ports (quadrature drive) wired in parallel with the caps of
     two geometrically-90-deg-apart upper-ring ports (auto: P_U01 and
     P_U{1+N/4}). Phase split is up to you at excitation/post-processing.
  4. A Nexxim LNA setup with two linear sweeps: around 64 MHz and 128 MHz.

Prerequisites: run make_ports.py first (the HFSS design must contain the
P_Uxx/P_Lxx lumped ports). If the HFSS design has no solution setup, one is
created (dynamic links must reference a named solution).

Idempotency: if a design named CIRCUIT_DESIGN already exists it is DELETED and
rebuilt from scratch (any manual edits inside it are lost — that is the
point). The HFSS design and the $C_ring value are preserved.

Run me on a COPY of the project. This script saves the project at the end.

Run from a CPython 3.10+ with pyaedt installed, while AEDT is running
(see make_ports.py header for the how-to; NOT legacy Tools > Run Script).

Verified against pyaedt 1.1.0 (ansys.aedt.core) method signatures:
  NexximComponents.add_subcircuit_dynamic_link(pyaedt_app, solution_name,
        ..., name)                       # solution defaults to nominal_sweep
  NexximComponents.create_capacitor(name, value, location, angle, ...)
        # value is set via set_property("C", value) -> variable names OK
  NexximComponents.create_interface_port(name, location, angle, page)
  NexximComponents.create_gnd(location, angle, page)
  CircuitPins.connect_to_component(assignment, page_name=None,
        use_wire=False, ...)
  SetupCircuit.add_sweep_count(sweep_variable, start, stop, count, units,
        count_type, override_existing_sweep)
"""

import re
import sys
import traceback

from ansys.aedt.core import Circuit, Hfss

# --------------------------------------------------------------------------
# CONFIG — edit here
# --------------------------------------------------------------------------
VERSION      = "2026.1"
PROJECT      = None      # r"C:\path\to\Birdcage_2026R1_copy.aedt"; None = active
HFSS_DESIGN  = None      # "HFSSDesign1"; None = active design of that project

CIRCUIT_DESIGN = "BirdcageCosim"
DYNLINK_NAME   = "BirdcageHFSS"

C_RING_VAR  = "$C_ring"  # project variable ($ prefix = project scope in AEDT)
C_RING_INIT = "8.2pF"    # used only when the variable does not exist yet

# Quadrature drive: None -> auto ("P_U01", "P_U{1+N/4}") from the discovered
# upper-ring port count; or set explicitly, e.g. ("P_U01", "P_U09").
DRIVE_PORTS = None
DRIVE_NAMES = ("Drive1_0deg", "Drive2_90deg")   # interface port names

# HFSS nominal setup (created only if the HFSS design has none)
HFSS_SETUP_NAME = "Setup1"
HFSS_SETUP_FREQ = "128MHz"

# LNA sweeps: (start_MHz, stop_MHz, n_points) — refine later in the GUI
LNA_SETUP_NAME = "LNA_Birdcage"
SWEEPS_MHZ = [(54.0, 74.0, 401),     # around 64 MHz (1.5 T)
              (118.0, 138.0, 401)]   # around 128 MHz (3 T)

# Schematic grid layout (AEDT schematic units are meters; grid pitch 2.54 mm)
GRID_COLS = 8
X0, Y0 = 0.0762, 0.0508      # top-left cap of the grid
DX, DY = 0.0508, 0.0381      # column / row spacing
GND_DY = 0.01016             # gnd symbol offset below each cap
DRV_DY = 0.01524             # interface-port offset above its cap
# --------------------------------------------------------------------------

PIN_RE = re.compile(r"^P_([UL])(\d+)$")


def pin_sort_key(name):
    m = PIN_RE.match(name)
    return (0 if m.group(1) == "U" else 1, int(m.group(2)))


def main():
    # ---- attach to HFSS design ----------------------------------------------
    hfss = Hfss(project=PROJECT, design=HFSS_DESIGN, version=VERSION,
                new_desktop=False, non_graphical=False)
    print(f"[attach] HFSS: project='{hfss.project_name}' "
          f"design='{hfss.design_name}'")

    bc_ports = sorted([p for p in hfss.ports if PIN_RE.match(p)],
                      key=pin_sort_key)
    if not bc_ports:
        raise RuntimeError("No P_Uxx/P_Lxx ports found in the HFSS design - "
                           "run make_ports.py first.")
    n_upper = sum(1 for p in bc_ports if p.startswith("P_U"))
    print(f"[ports ] found {len(bc_ports)} birdcage ports "
          f"({n_upper} upper, {len(bc_ports) - n_upper} lower)")

    # Dynamic links reference a named solution -> make sure one exists.
    if not hfss.setups:
        setup = hfss.create_setup(name=HFSS_SETUP_NAME,
                                  Frequency=HFSS_SETUP_FREQ)
        print(f"[setup ] HFSS design had no setup - created "
              f"'{setup.name}' @ {HFSS_SETUP_FREQ}")
    print(f"[setup ] dynamic link will reference: '{hfss.nominal_sweep}'")

    # ---- (re)create the circuit design (idempotent) --------------------------
    if CIRCUIT_DESIGN in hfss.design_list:
        hfss.delete_design(name=CIRCUIT_DESIGN,
                           fallback_design=hfss.design_name)
        print(f"[circ  ] deleted pre-existing design '{CIRCUIT_DESIGN}'")
    circuit = Circuit(project=hfss.project_name, design=CIRCUIT_DESIGN,
                      version=VERSION, new_desktop=False, non_graphical=False)
    sch = circuit.modeler.schematic
    print(f"[circ  ] created circuit design '{circuit.design_name}'")

    # ---- project tuning variable ---------------------------------------------
    if C_RING_VAR in circuit.variable_manager.variables:
        cur = circuit.variable_manager.variables[C_RING_VAR].evaluated_value
        print(f"[var   ] {C_RING_VAR} already exists ({cur}) - keeping it")
    else:
        circuit[C_RING_VAR] = C_RING_INIT
        print(f"[var   ] created project variable {C_RING_VAR} = {C_RING_INIT}")

    # ---- dynamic-link subcircuit ----------------------------------------------
    dyn = sch.add_subcircuit_dynamic_link(pyaedt_app=hfss, name=DYNLINK_NAME)
    if not dyn:
        raise RuntimeError("add_subcircuit_dynamic_link failed")
    print(f"[dyn   ] dynamic link component: {dyn.composed_name}")

    pin_map = {p.name: p for p in dyn.pins if PIN_RE.match(p.name)}
    pins = sorted(pin_map, key=pin_sort_key)
    print(f"[dyn   ] {len(pins)} birdcage pins on the link "
          f"(expected {len(bc_ports)})")
    if len(pins) != len(bc_ports):
        raise RuntimeError(f"Pin mismatch: HFSS has {len(bc_ports)} ports but "
                           f"the link exposes {len(pins)} matching pins.")

    # ---- drive port selection --------------------------------------------------
    if DRIVE_PORTS is None:
        drive = ("P_U01", f"P_U{1 + n_upper // 4:02d}")   # 90 deg apart
    else:
        drive = tuple(DRIVE_PORTS)
    for d in drive:
        if d not in pin_map:
            raise RuntimeError(f"Drive port {d} not found on the dynamic link.")
    print(f"[drive ] quadrature drive on {drive[0]} / {drive[1]} "
          f"({n_upper // 4} gaps = 90 deg apart)")

    # ---- caps + gnd + page-port net per pin, in a grid --------------------------
    cap_of = {}
    for i, pname in enumerate(pins):
        col, row = i % GRID_COLS, i // GRID_COLS
        x, y = X0 + col * DX, Y0 - row * DY
        cap = sch.create_capacitor(name=f"C{pname[2:]}", value=C_RING_VAR,
                                   location=[x, y])
        gnd = sch.create_gnd(location=[x, y - GND_DY])
        # net <pname>: page port at the dyn-link pin + page port at the cap
        pin_map[pname].connect_to_component(cap.pins[0], page_name=pname,
                                            use_wire=False)
        cap.pins[1].connect_to_component(gnd.pins[0], use_wire=True)
        cap_of[pname] = cap
        print(f"[cap   ] {cap.composed_name:<24s} = {C_RING_VAR} "
              f"on net {pname} @ ({x:.4f}, {y:.4f})")

    # ---- interface ports (quadrature drive) --------------------------------------
    for dname, pname in zip(DRIVE_NAMES, drive):
        cap = cap_of[pname]
        x, y = cap.location[0], cap.location[1] + DRV_DY
        iport = sch.create_interface_port(name=dname, location=[x, y])
        # in parallel with the cap: wire the port pin onto the cap's hot pin
        iport.pins[0].connect_to_component(cap.pins[0], use_wire=True)
        print(f"[drive ] interface port '{dname}' -> net {pname}")

    # ---- LNA setup -----------------------------------------------------------------
    setup = circuit.create_setup(name=LNA_SETUP_NAME, setup_type="NexximLNA")
    first = True
    for f0, f1, npts in SWEEPS_MHZ:
        setup.add_sweep_count("Freq", start=f0, stop=f1, count=npts,
                              units="MHz", count_type="Linear",
                              override_existing_sweep=first)
        first = False
    print(f"[lna   ] setup '{setup.name}' sweeps: "
          + ", ".join(f"{a}-{b} MHz ({n} pts)" for a, b, n in SWEEPS_MHZ))

    # ---- checks ----------------------------------------------------------------------
    n_caps = sum(1 for c in sch.components.values()
                 if c.parameters.get("C", "") == C_RING_VAR)
    print(f"\n[check ] capacitors referencing {C_RING_VAR}: {n_caps} "
          f"(expected {len(pins)})")
    ok = circuit.validate_simple()
    print(f"[check ] validate_simple -> {ok}")

    circuit.save_project()
    print(f"[save  ] project saved: {circuit.project_file}")
    circuit.release_desktop(close_projects=False, close_desktop=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
