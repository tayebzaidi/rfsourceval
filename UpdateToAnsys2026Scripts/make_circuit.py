"""
make_circuit.py — Part B of the birdcage co-sim scripting.

Builds a Nexxim circuit design ("BirdcageCosim") in the SAME project as the
HFSS birdcage design, reproducing the 2021 R1 co-sim schematic:

  1. A dynamic-link subcircuit of the HFSS design (all P_Uxx / P_Lxx pins).
  2. DESIGN-LOCAL variables (visible under Design Properties):
        Ct — ring tuning capacitance        (default 79.3165pF)
        Rt — series loss (ESR) of every cap (default 0.2ohm)
        Cm — drive matching capacitance     (default 29.4750392017492pF)
     If a previous BirdcageCosim design exists, its current values are read
     BEFORE it is deleted and re-applied, so your tuned values survive reruns.
  3. Per HFSS port: a series Rt + Ct chain loading that port ("across the
     gap"). If the dynamic link exposes per-port reference pins the chain is
     wired pin -> ref; otherwise pin -> schematic GND, which is electrically
     identical for an S-parameter link (each port keeps its own internal
     reference — schematic ground is only the netlist reference node).
  4. Quadrature drive on DRIVE_PORTS (default P_U01 / P_U25, checked to be
     +/-90 deg apart around the ring): interface port -> series Rt -> series
     Cm -> onto that port's net, IN ADDITION to the port's own Rt+Ct chain.
  5. A Nexxim LNA setup with two linear sweeps: around 64 MHz and 128 MHz.

To SHOW each port's hidden reference pin on the symbol (2021-R1 look):
right-click the dynamic-link component > Edit Symbol > Pin Locations..., and
pick "Add individual reference pin per port" in the reference pulldown.
There is no scripting API for this symbol edit, and a rerun of this script
rebuilds the link (discarding it) — do it last, cosmetics only.

Prerequisites: run make_ports.py first (the HFSS design must contain the
P_Uxx/P_Lxx lumped ports). If the HFSS design has no solution setup, one is
created (dynamic links must reference a named solution).

Idempotency: starts from scratch every run — if a design named
CIRCUIT_DESIGN exists it is DELETED and rebuilt (manual edits inside it are
lost — that is the point). The HFSS design is never modified (except adding
a setup if none exists) and the Ct/Rt/Cm values are preserved.

Run me on a COPY of the project. This script saves the project at the end.

Run from a CPython 3.10+ with pyaedt installed, while AEDT is running
(see make_ports.py header for the how-to; NOT legacy Tools > Run Script).

Verified against pyaedt 1.1.0 (ansys.aedt.core) method signatures:
  NexximComponents.add_subcircuit_dynamic_link(pyaedt_app, solution_name,
        ..., name)                       # solution defaults to nominal_sweep
  NexximComponents.create_resistor(name, value, location, angle, ...)
  NexximComponents.create_capacitor(name, value, location, angle, ...)
        # value is set via set_property -> variable-name strings OK
  NexximComponents.create_page_port(name, location, angle, label_position)
  NexximComponents.create_interface_port(name, location)
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

# Design-local tuning variables: name -> default expression. Defaults are
# used only when the variable is not found in a pre-existing CIRCUIT_DESIGN.
TUNE_VARS = {
    "Ct": "79.3165pF",            # ring tuning capacitance
    "Rt": "0.2ohm",               # series loss (ESR), reused in drive branch
    "Cm": "29.4750392017492pF",   # drive matching capacitance
}

# Quadrature drive pair (must be +/-90 deg apart around the ring — checked).
DRIVE_PORTS = ("P_U01", "P_U25")
DRIVE_NAMES = ("Drive1_0deg", "Drive2_90deg")   # interface port names

# HFSS nominal setup (created only if the HFSS design has none)
HFSS_SETUP_NAME = "Setup1"
HFSS_SETUP_FREQ = "128MHz"

# LNA sweeps: (start_MHz, stop_MHz, n_points) — refine later in the GUI
LNA_SETUP_NAME = "LNA_Birdcage"
SWEEPS_MHZ = [(54.0, 74.0, 401),     # around 64 MHz (1.5 T)
              (118.0, 138.0, 401)]   # around 128 MHz (3 T)

# Schematic layout (meters; AEDT schematic grid pitch is 2.54 mm).
# Upper-ring chains in a column on the left, lower-ring on the right,
# drive networks top-right — page-port nets carry connectivity, so the
# layout is purely cosmetic.
GRID = 0.00254
X_UPPER  = -80 * GRID        # x of the upper-ring page-port column
X_LOWER  =  80 * GRID        # x of the lower-ring page-port column
Y_TOP    =  46 * GRID        # y of the first row
DY_ROW   =   6 * GRID        # row spacing
DX_RES   =   6 * GRID        # page port -> Rt offset
DX_CAP   =  12 * GRID        # page port -> Ct offset
DX_END   =  18 * GRID        # page port -> chain end (gnd / ref page port)
DRV_X    = 120 * GRID        # drive networks: leftmost (page port) x
DRV_Y0   =  46 * GRID        # first drive network y
DRV_DY   = -12 * GRID        # spacing between the two drive networks
# --------------------------------------------------------------------------

PIN_RE = re.compile(r"^P_([UL])(\d+)$")


def pin_sort_key(name):
    m = PIN_RE.match(name)
    return (0 if m.group(1) == "U" else 1, int(m.group(2)))


def ref_pin_of(pname, pin_names):
    """Return the dynamic link's reference pin for port pname, if exposed."""
    for cand in (f"{pname}_ref", f"ref_{pname}", f"{pname}.ref",
                 f"{pname}:ref", f"{pname}_REF"):
        if cand in pin_names:
            return cand
    return None


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

    # ---- drive-pair geometry check ------------------------------------------
    for d in DRIVE_PORTS:
        if d not in bc_ports:
            raise RuntimeError(f"Drive port {d} not found in the HFSS design.")
    k1, k2 = (int(PIN_RE.match(d).group(2)) for d in DRIVE_PORTS)
    sep_deg = ((k2 - k1) % n_upper) * 360.0 / n_upper
    rel_deg = ((sep_deg + 180.0) % 360.0) - 180.0     # map into (-180, 180]
    print(f"[drive ] {DRIVE_PORTS[1]} sits {sep_deg:g} deg around the ring "
          f"from {DRIVE_PORTS[0]} (= {rel_deg:+g} deg)")
    if abs(rel_deg) != 90.0:
        raise RuntimeError(
            f"DRIVE_PORTS {DRIVE_PORTS} are {rel_deg:+g} deg apart, not "
            f"+/-90 - pick ports {n_upper // 4} gaps apart.")

    # Dynamic links reference a named solution -> make sure one exists.
    if not hfss.setups:
        setup = hfss.create_setup(name=HFSS_SETUP_NAME,
                                  Frequency=HFSS_SETUP_FREQ)
        print(f"[setup ] HFSS design had no setup - created "
              f"'{setup.name}' @ {HFSS_SETUP_FREQ}")
    print(f"[setup ] dynamic link will reference: '{hfss.nominal_sweep}'")

    # ---- start from scratch: read old tuning values, delete, recreate --------
    # Native API throughout this block: pyaedt's design_list property parses
    # GetTopDesignList() with a regex that can misreport circuit designs,
    # which skips the delete and then trips "already in project" /
    # "Unable to locate design" during the recreate.
    oproject = hfss.oproject
    old_vars = {}
    designs = {d.GetName(): d for d in oproject.GetDesigns()}
    print(f"[circ  ] designs in project: {sorted(designs)}")
    if CIRCUIT_DESIGN in designs:
        for vn in TUNE_VARS:
            try:
                old_vars[vn] = designs[CIRCUIT_DESIGN].GetVariableValue(vn)
            except Exception:
                pass
        if not old_vars:
            print("[var   ] no old variable values readable - "
                  "defaults will be used")
        # deleting the active design can misbehave - activate the HFSS one
        oproject.SetActiveDesign(hfss.design_name)
        oproject.DeleteDesign(CIRCUIT_DESIGN)
        if CIRCUIT_DESIGN in [d.GetName() for d in oproject.GetDesigns()]:
            raise RuntimeError(
                f"'{CIRCUIT_DESIGN}' still exists after DeleteDesign - "
                "close any open editor windows of it in the GUI and rerun.")
        print(f"[circ  ] deleted pre-existing design '{CIRCUIT_DESIGN}' "
              "(verified gone)")
    # Create the circuit design natively, then attach pyaedt to it.
    # 2026 R1 changed InsertDesign for circuit designs (Circuit Scripting
    # Guide p. 6-45): design type must be "NexximCircuit"/"Circuit" (no
    # longer "Circuit Design") and the third argument is a technology-file
    # path ("" for none), not a solution name. pyaedt 1.1.0 still sends the
    # legacy form, which 2026 R1 silently ignores ("Failed to create
    # design"), so we do the InsertDesign ourselves and verify.
    last_err = None
    attempts = (("NexximCircuit", CIRCUIT_DESIGN, "", ""),
                ("Circuit", CIRCUIT_DESIGN, "", ""),
                ("Circuit Design", CIRCUIT_DESIGN, "None", ""))  # legacy
    for args in attempts:
        try:
            oproject.InsertDesign(*args)
        except Exception as e:
            last_err = e
        if CIRCUIT_DESIGN in [d.GetName() for d in oproject.GetDesigns()]:
            print(f"[circ  ] InsertDesign{args} succeeded")
            break
    else:
        raise RuntimeError(
            f"No InsertDesign variant created '{CIRCUIT_DESIGN}' "
            f"(last error: {last_err!r}). Check AEDT's Message Manager "
            "for the underlying reason.")
    oproject.SetActiveDesign(CIRCUIT_DESIGN)
    print(f"[circ  ] created circuit design '{CIRCUIT_DESIGN}' "
          "(native InsertDesign, verified)")
    circuit = Circuit(project=hfss.project_name, version=VERSION,
                      new_desktop=False, non_graphical=False)
    if circuit.design_name != CIRCUIT_DESIGN:
        raise RuntimeError(f"pyaedt attached to design "
                           f"'{circuit.design_name}' instead of "
                           f"'{CIRCUIT_DESIGN}'.")
    sch = circuit.modeler.schematic

    # ---- design-local tuning variables ---------------------------------------
    for vn, default in TUNE_VARS.items():
        expr = old_vars.get(vn, default)
        circuit[vn] = expr
        origin = "preserved from old design" if vn in old_vars else "default"
        print(f"[var   ] {vn} = {expr}  ({origin})")

    # ---- dynamic-link subcircuit ----------------------------------------------
    dyn = sch.add_subcircuit_dynamic_link(pyaedt_app=hfss, name=DYNLINK_NAME)
    if not dyn:
        raise RuntimeError("add_subcircuit_dynamic_link failed")
    print(f"[dyn   ] dynamic link component: {dyn.composed_name}")

    pin_map = {p.name: p for p in dyn.pins}
    port_pins = sorted((n for n in pin_map if PIN_RE.match(n)),
                       key=pin_sort_key)
    print(f"[dyn   ] {len(pin_map)} pins on the link, {len(port_pins)} "
          f"matching P_[UL]xx (expected {len(bc_ports)})")
    if len(port_pins) != len(bc_ports):
        raise RuntimeError(f"Pin mismatch: HFSS has {len(bc_ports)} ports but "
                           f"the link exposes {len(port_pins)} matching pins.")

    refs = {p: ref_pin_of(p, pin_map) for p in port_pins}
    differential = all(refs.values())
    if differential:
        print("[wire  ] link exposes per-port reference pins - Rt+Ct chains "
              "wired pin -> ref (explicitly across each gap)")
    else:
        print("[wire  ] link exposes single-ended pins - Rt+Ct chains wired "
              "pin -> GND (electrically identical: each port keeps its own "
              "internal reference; schematic gnd is just the netlist "
              "reference node).")
        print("[wire  ] To SHOW per-port reference pins like the 2021 R1 "
              "schematic: right-click the link component > Edit Symbol > "
              "Pin Locations... > choose 'Add individual reference pin per "
              "port'. GUI-only (no scripting API); reruns discard it.")

    # ---- Rt + Ct chain per port -----------------------------------------------
    for i, pname in enumerate(port_pins):
        tag = pname[2:]                       # U01 ... L32
        upper = pname.startswith("P_U")
        x0 = X_UPPER if upper else X_LOWER
        row = i if upper else i - n_upper
        y = Y_TOP - row * DY_ROW
        rt = sch.create_resistor(name=f"Rt{tag}", value="Rt",
                                 location=[x0 + DX_RES, y])
        ct = sch.create_capacitor(name=f"Ct{tag}", value="Ct",
                                  location=[x0 + DX_CAP, y])
        # net <pname>: page port at the dyn-link pin + page port at the chain
        pin_map[pname].connect_to_component(rt.pins[0], page_name=pname,
                                            use_wire=False)
        rt.pins[1].connect_to_component(ct.pins[0], use_wire=True)
        if differential:
            # close the loop across the gap on the port's own reference pin
            pin_map[refs[pname]].connect_to_component(
                ct.pins[1], page_name=f"{pname}_ref", use_wire=False)
        else:
            gnd = sch.create_gnd(location=[x0 + DX_END, y - 4 * GRID])
            ct.pins[1].connect_to_component(gnd.pins[0], use_wire=True)
        print(f"[chain ] {pname}: Rt{tag} + Ct{tag} -> "
              f"{'ref pin' if differential else 'gnd'}")

    # ---- drive networks: port -> Rt -> Cm -> port net --------------------------
    for j, (dname, pname) in enumerate(zip(DRIVE_NAMES, DRIVE_PORTS)):
        y = DRV_Y0 + j * DRV_DY
        pp = sch.create_page_port(name=pname, location=[DRV_X, y])
        cm = sch.create_capacitor(name=f"Cm{j + 1}", value="Cm",
                                  location=[DRV_X + 6 * GRID, y])
        rm = sch.create_resistor(name=f"Rm{j + 1}", value="Rt",
                                 location=[DRV_X + 12 * GRID, y])
        iport = sch.create_interface_port(name=dname,
                                          location=[DRV_X + 18 * GRID, y])
        pp.pins[0].connect_to_component(cm.pins[0], use_wire=True)
        cm.pins[1].connect_to_component(rm.pins[0], use_wire=True)
        rm.pins[1].connect_to_component(iport.pins[0], use_wire=True)
        print(f"[drive ] '{dname}' -> Rm{j + 1}(Rt) -> Cm{j + 1}(Cm) -> "
              f"net {pname} (in parallel with Rt{pname[2:]}+Ct{pname[2:]})")

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
    comps = list(sch.components.values())
    n_ct = sum(1 for c in comps if c.parameters.get("C", "") == "Ct")
    n_cm = sum(1 for c in comps if c.parameters.get("C", "") == "Cm")
    n_rt = sum(1 for c in comps if c.parameters.get("R", "") == "Rt")
    print(f"\n[check ] caps on Ct: {n_ct} (expected {len(port_pins)}) | "
          f"caps on Cm: {n_cm} (expected {len(DRIVE_PORTS)}) | "
          f"resistors on Rt: {n_rt} "
          f"(expected {len(port_pins) + len(DRIVE_PORTS)})")
    ok = circuit.validate_simple()
    print(f"[check ] validate_simple -> {ok} "
          "(missing-solution complaints are expected until HFSS is solved)")

    circuit.save_project()
    print(f"[save  ] project saved: {circuit.project_file}")
    circuit.release_desktop(close_projects=False, close_desktop=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
