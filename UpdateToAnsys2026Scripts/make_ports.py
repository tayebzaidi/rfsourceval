"""
make_ports.py — Part A of the birdcage co-sim port scripting.

Generates all 2*N_PER_RING (default 64) port-sheet rectangles of a birdcage MRI
coil from a single hand-drawn reference rectangle, and assigns a 50-ohm lumped
port with an explicit two-point integration line to each one.

Workflow
--------
1. Attaches to the already-running AEDT 2026 R1 session (new_desktop=False).
2. Reads the 4 corner vertices of the reference rectangle (REF_RECT, default
   "Rectangle1") directly from the model — no manual coordinate entry needed.
3. Verifies the coil is centered on the Z axis in XY (bounding-box check) and
   finds the axial midplane z_mid used for the upper->lower mirror.
4. Upper ring : rotate reference corners about +Z by k*(360/N) deg, k=0..N-1.
   Lower ring : 180-deg rotation about the RADIAL axis through (0, 0, z_mid)
                at the reference rectangle's azimuth, so each lower-ring port
                lands at the same azimuth as (directly below) its same-numbered
                upper-ring counterpart; then optional LOWER_RING_PHI_OFFSET,
                then the same Z rotations.
5. For every corner set, creates a covered-polyline sheet "PortSheet_<Uxx|Lxx>"
   and a lumped port "P_<Uxx|Lxx>" whose integration line runs from the
   midpoint of one conductor-side edge to the midpoint of the opposite edge
   (i.e. across the gap).

Idempotency
-----------
Reruns first delete every boundary matching ^P_[UL]\\d+$ and every sheet
matching ^PortSheet_[UL]\\d+$, then recreate everything. The imported coil
geometry is never touched. The only (optional, logged, reversible) change to a
pre-existing object is setting REF_RECT to non-model so it does not sit
coincident with PortSheet_U01 during meshing (NON_MODEL_REF_RECT).

Run me on a COPY of the project. This script saves the project at the end.

Run from a CPython 3.10+ that has pyaedt installed (pip install pyaedt),
either from a plain Windows terminal while AEDT is running, or through the
PyAEDT "Run Script" button in the AEDT Automation tab.
Do NOT use AEDT's legacy Tools > Run Script (that is IronPython — no pyaedt).

Verified against pyaedt 1.1.0 (ansys.aedt.core) method signatures:
  Modeler3D.create_polyline(points, cover_surface, close_surface, name, ...)
  Hfss.lumped_port(assignment, integration_line=[[p0],[p1]], impedance,
                   name, renormalize, ...)
  Hfss.validate_full_design(design, output_dir, ports) -> (list, bool)
"""

import re
import sys
import traceback

import numpy as np
from ansys.aedt.core import Hfss

# --------------------------------------------------------------------------
# CONFIG — edit here
# --------------------------------------------------------------------------
VERSION = "2026.1"
PROJECT = None          # r"C:\path\to\Birdcage_2026R1_copy.aedt"; None = active project
DESIGN  = None          # "HFSSDesign1"; None = active design of that project

N_PER_RING = 32         # gaps per end ring (ports total = 2 * N_PER_RING)
IMPEDANCE  = 50.0       # ohms, all ports
REF_RECT   = "Rectangle1"   # hand-drawn reference port sheet on the UPPER ring

LOWER_RING_PHI_OFFSET = 0.0     # deg, if lower-ring gaps are azimuthally staggered

# Which opposite-edge pairing of the 4 reference vertices spans the gap:
#   "auto"  — pick the pairing whose midpoint-to-midpoint direction is most
#             azimuthal (gap E-field in a ring is azimuthal). Logged; verify!
#   "01-23" — integration line mid(v0,v1) -> mid(v2,v3)
#   "12-30" — integration line mid(v1,v2) -> mid(v3,v0)
INT_LINE_PAIRING = "auto"

# Swap the start/end points of EVERY integration line (keeps the same edge
# pairing, reverses the arrow direction). Use when the pairing is right but
# the polarity is backwards.
REVERSE_INT_LINES = True

# The 180-deg radial-axis rotation mirrors the circumferential direction, so
# lower-ring integration lines point the opposite way around the ring than
# upper-ring ones. Set True to swap the lower-ring endpoints for a consistent
# polarity convention around both rings.
FLIP_LOWER_INT_LINES = True

TEST_MODE = False       # True: create ONLY PortSheet_U01/P_U01, then stop so
                        # you can visually confirm it coincides with REF_RECT.

NON_MODEL_REF_RECT = True   # set REF_RECT non-model (it stays in the tree)

XY_CENTER_TOL_FRAC = 0.01   # warn if XY bbox center is off-axis by more than
                            # this fraction of the largest bbox dimension
# --------------------------------------------------------------------------

PORT_RE  = re.compile(r"^P_[UL]\d+$")
SHEET_RE = re.compile(r"^PortSheet_[UL]\d+$")


def rot_z(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0.0],
                     [np.sin(a),  np.cos(a), 0.0],
                     [0.0,        0.0,       1.0]])


def mirror_about_radial_axis_at(phi_deg, z_mid, pts):
    """180-deg rotation about the radial axis at azimuth phi_deg through
    (0, 0, z_mid).

    Points at azimuth phi_deg stay at azimuth phi_deg, so the lower-ring
    port generated from the reference lands directly below it (same x, y)."""
    a = np.radians(phi_deg)
    c, s = np.cos(a), np.sin(a)
    R = np.array([[2.0 * c * c - 1.0, 2.0 * c * s,       0.0],
                  [2.0 * c * s,       2.0 * s * s - 1.0, 0.0],
                  [0.0,               0.0,              -1.0]])
    out = (R @ pts.T).T
    out[:, 2] += 2.0 * z_mid
    return out


def pick_int_line_pairing(corners):
    """Choose the opposite-edge pairing whose direction is most azimuthal.

    Returns (pairing_key, start_idx_pair, end_idx_pair)."""
    center = corners.mean(axis=0)
    r_hat = np.array([center[0], center[1], 0.0])
    nr = np.linalg.norm(r_hat)
    if nr < 1e-12:
        raise RuntimeError(
            "Reference rectangle center is on the Z axis - cannot infer the "
            "azimuthal direction. Set INT_LINE_PAIRING explicitly.")
    r_hat /= nr
    t_hat = np.cross([0.0, 0.0, 1.0], r_hat)   # azimuthal (tangential) dir

    cands = {"01-23": ((0, 1), (2, 3)), "12-30": ((1, 2), (3, 0))}
    scores = {}
    for key, (sa, sb) in cands.items():
        v = (corners[sb[0]] + corners[sb[1]]) / 2.0 \
            - (corners[sa[0]] + corners[sa[1]]) / 2.0
        scores[key] = abs(np.dot(v / np.linalg.norm(v), t_hat))
    best = max(scores, key=scores.get)
    print(f"[pairing] azimuthal-alignment scores: "
          + ", ".join(f"{k}: {v:.3f}" for k, v in scores.items())
          + f" -> chose '{best}'")
    if abs(scores["01-23"] - scores["12-30"]) < 0.2:
        print("[pairing] WARNING: scores are close - VERIFY the integration "
              "line direction of P_U01 in the GUI and, if wrong, set "
              "INT_LINE_PAIRING to the other value and rerun.")
    return best, cands[best][0], cands[best][1]


def cleanup(hfss):
    """Delete anything this script may have created on a previous run."""
    removed_b = [bd.name for bd in hfss.boundaries if PORT_RE.match(bd.name)]
    if removed_b:
        print(f"[cleanup] deleting {len(removed_b)} old ports (one batched "
              "call)...", flush=True)
        hfss.oboundary.DeleteBoundaries(removed_b)
    sheets = [o for o in hfss.modeler.get_objects_w_string("PortSheet_")
              if SHEET_RE.match(o)]
    if sheets:
        print(f"[cleanup] deleting {len(sheets)} old sheets...", flush=True)
        hfss.modeler.delete(sheets)
    print(f"[cleanup] removed {len(removed_b)} old ports, "
          f"{len(sheets)} old sheets", flush=True)


def main():
    hfss = Hfss(project=PROJECT, design=DESIGN, version=VERSION,
                new_desktop=False, non_graphical=False)
    print(f"[attach] project='{hfss.project_name}' design='{hfss.design_name}'")
    print(f"[units ] model units: {hfss.modeler.model_units}")

    # ---- reference rectangle ------------------------------------------------
    if REF_RECT not in hfss.modeler.object_names:
        raise RuntimeError(f"Reference rectangle '{REF_RECT}' not found.")
    ref = hfss.modeler[REF_RECT]
    verts = [v.position for v in ref.vertices]
    if len(verts) != 4:
        raise RuntimeError(f"'{REF_RECT}' has {len(verts)} vertices, expected 4.")
    corners = np.array(verts, dtype=float)
    print(f"[ref   ] '{REF_RECT}' corners (perimeter order from AEDT):")
    for i, c in enumerate(corners):
        print(f"         v{i}: [{c[0]:.6g}, {c[1]:.6g}, {c[2]:.6g}]")

    # ---- coil centering check ----------------------------------------------
    bb = [float(v) for v in hfss.modeler.get_model_bounding_box()]
    xmid, ymid = (bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0
    z_mid = (bb[2] + bb[5]) / 2.0
    max_dim = max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
    print(f"[bbox  ] model bounding box: {bb}")
    print(f"[bbox  ] center: [{xmid:.6g}, {ymid:.6g}, {z_mid:.6g}] "
          f"(z_mid used as mirror plane)")
    if max(abs(xmid), abs(ymid)) > XY_CENTER_TOL_FRAC * max_dim:
        print("[bbox  ] WARNING: coil is NOT centered on the Z axis in XY. "
              "The Z rotations assume the coil axis is the Z axis - inspect "
              "before trusting the generated sheets!")

    # ---- integration-line pairing ------------------------------------------
    if INT_LINE_PAIRING == "auto":
        pairing, sa, sb = pick_int_line_pairing(corners)
    elif INT_LINE_PAIRING in ("01-23", "12-30"):
        pairing = INT_LINE_PAIRING
        sa, sb = {"01-23": ((0, 1), (2, 3)),
                  "12-30": ((1, 2), (3, 0))}[pairing]
    else:
        raise RuntimeError(f"Bad INT_LINE_PAIRING: {INT_LINE_PAIRING!r}")
    if REVERSE_INT_LINES:
        sa, sb = sb, sa
        print("[pairing] REVERSE_INT_LINES: integration-line start/end swapped")

    # ---- build all port definitions ----------------------------------------
    step = 360.0 / N_PER_RING
    ref_center = corners.mean(axis=0)
    phi_ref = np.degrees(np.arctan2(ref_center[1], ref_center[0]))
    print(f"[mirror] lower ring: 180-deg rotation about the radial axis at "
          f"azimuth {phi_ref:.3f} deg (P_Lxx directly below P_Uxx)")
    lower = mirror_about_radial_axis_at(phi_ref, z_mid, corners)
    port_defs = []   # (name, corners(4,3), int_start(3,), int_end(3,))
    for ring, base, phi0 in (("U", corners, 0.0),
                             ("L", lower, LOWER_RING_PHI_OFFSET)):
        for k in range(N_PER_RING):
            R = rot_z(phi0 + k * step)
            ck = (R @ base.T).T
            s = (ck[sa[0]] + ck[sa[1]]) / 2.0
            e = (ck[sb[0]] + ck[sb[1]]) / 2.0
            if ring == "L" and FLIP_LOWER_INT_LINES:
                s, e = e, s
            port_defs.append((f"{ring}{k + 1:02d}", ck, s, e))

    # ---- idempotent cleanup, then create ------------------------------------
    cleanup(hfss)

    if NON_MODEL_REF_RECT and ref.is_model:
        ref.is_model = False
        print(f"[ref   ] set '{REF_RECT}' to non-model (reversible in the GUI) "
              "so it is not coincident with PortSheet_U01 at mesh time.")

    n_target = 1 if TEST_MODE else len(port_defs)
    created = []
    for name, ck, s, e in port_defs[:n_target]:
        sheet = hfss.modeler.create_polyline(
            [list(p) for p in ck],
            close_surface=True, cover_surface=True,
            name=f"PortSheet_{name}")
        port = hfss.lumped_port(
            assignment=sheet.name,
            integration_line=[list(s), list(e)],
            impedance=IMPEDANCE,
            name=f"P_{name}",
            renormalize=True)
        if not port:
            raise RuntimeError(f"lumped_port failed for {name}")
        created.append(port.name)
        print(f"[create] {sheet.name:>16s}  {port.name:<6s} "
              f"int-line [{s[0]:.5g},{s[1]:.5g},{s[2]:.5g}] -> "
              f"[{e[0]:.5g},{e[1]:.5g},{e[2]:.5g}]")

    # ---- checks --------------------------------------------------------------
    ports_now = [p for p in hfss.ports if PORT_RE.match(p)]
    print(f"\n[check ] birdcage ports in design: {len(ports_now)} "
          f"(expected {n_target})")
    missing = [f"P_{n}" for n, *_ in port_defs[:n_target]
               if f"P_{n}" not in ports_now]
    if missing:
        print(f"[check ] MISSING: {missing}")

    if TEST_MODE:
        print("\n[TEST_MODE] Only P_U01 was created. Confirm PortSheet_U01 "
              f"coincides with '{REF_RECT}' and its integration line spans "
              "the gap, then set TEST_MODE = False and rerun (the rerun "
              "recreates U01 too - it is fully idempotent).")
    else:
        val, ok = hfss.validate_full_design(ports=len(port_defs))
        print(f"[check ] validate_full_design -> ok={ok}")
        for line in val:
            print(f"         {line}")

    if not FLIP_LOWER_INT_LINES:
        print("\n[NOTE  ] The 180-deg radial-axis mirror reverses the circumferential "
              "direction on the lower ring, so P_Lxx integration lines are "
              "oriented opposite to P_Uxx relative to the ring direction. "
              "If you want one consistent polarity convention, set "
              "FLIP_LOWER_INT_LINES = True and rerun.")

    hfss.save_project()
    print(f"[save  ] project saved: {hfss.project_file}")
    hfss.release_desktop(close_projects=False, close_desktop=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
