# %% [markdown]
# # 3T RF Source Validation: Fiber Optic Probe Temperature Rises
# Analysis of the `061626_3TRFSourceVal/` dataset — three scans taken at the
# chest landmark of the rectangular phantom (gelled saline), MRI exposure.
#
# Three acquisitions:
#   - scan1: PatientRight chest
#   - scan2: PatientLeft chest
#   - scan3: PatientLeft chest (Take 2)
#
# scan1 and scan3 are the best representations of the two experiment
# configurations (right vs. left), so they get a dedicated head-to-head.
# scan2 vs. scan3 are compared both zeroed and un-zeroed to show the effect
# of the starting temperature.
#
# File format note: unlike the earlier 61026 dataset, these CSVs have the
# column header on line 1 (no serial-number preamble), so skiprows=0.
# Eight probe channels record data: 246A, 247A/B/C, 244A/B, 243A/B.

# %%
import os
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# utils.py lives in the repo root, one level up from this directory
sys.path.append(os.path.abspath('..'))
from utils import list_files_with_paths

DATA_DIR = '.'
FIG_DIR = 'Figures'
os.makedirs(FIG_DIR, exist_ok=True)

# RF exposure started 20 s into every acquisition (true for all experiments).
# Derivative-based detection is unreliable here — with no implant the heating
# rate is too close to the probe noise floor.
RF_ON_S = 20.0
FIT_WINDOW_S = 200.0    # slope-fit duration after RF-on
C_PHANTOM = 4186.0      # J/(kg·K) — water; gelled saline is close

# Scan bookkeeping: label -> filename, plus a friendly description
SCANS = {
    'scan1': ('scan1_PatientRightChest.csv', 'PatientRight Chest'),
    'scan2': ('scan2_PatientLeftChest.csv', 'PatientLeft Chest'),
    'scan3': ('scan3_PatientLeftChest_Take2.csv', 'PatientLeft Chest (Take 2)'),
}

# Physical probe arrangement along the phantom's long (Z) axis. For Patient
# Right this runs from -Z (bottom of phantom) up to +Z (toward the top of the
# patient): 246A at the bottom, 243B at the top. The probe holder is rotated
# 180 deg for Patient Left, so the order simply reverses.
# (246B/246C are wired but recorded no data in these scans.)
HOLDER_ORDER_RIGHT = ['246A', '246B', '246C', '247A', '247B', '247C',
                      '244A', '244B', '243A', '243B']

def zorder_probes(scan_label, active_cols):
    """Active channels ordered -Z (bottom) -> +Z (top of patient), accounting
    for the holder rotation between Patient Right and Patient Left."""
    desc = SCANS[scan_label][1]
    order = HOLDER_ORDER_RIGHT if 'Right' in desc else HOLDER_ORDER_RIGHT[::-1]
    by_probe = {c.replace(' Temp', ''): c for c in active_cols}
    return [(by_probe[p], p) for p in order if p in by_probe]

def probe_styles(scan_label, active_cols):
    """Color + label for each active channel. Color runs along a sequential
    colormap by Z position (so the same physical location reads the same color
    regardless of orientation); labels carry the Z rank and probe ID, with the
    -Z/+Z ends called out. Returns (styles_by_col, columns_in_Z_order)."""
    ordered = zorder_probes(scan_label, active_cols)
    n = len(ordered)
    styles = {}
    for i, (col, probe) in enumerate(ordered):
        end = ' (-Z, bottom)' if i == 0 else ' (+Z, top)' if i == n - 1 else ''
        styles[col] = {
            'color': plt.cm.viridis(i / max(n - 1, 1)),
            'zpos': i + 1,
            'label': f'Z{i + 1} {probe}{end}',
        }
    return styles, [col for col, _ in ordered]

# %%
def load_probe_data(file_path):
    """Read a CSV from this dataset (header on line 1) and keep only the probe
    channels that actually contain data."""
    data = pd.read_csv(file_path)
    data.columns = data.columns.str.strip()

    temp_cols = [c for c in data.columns if c.endswith('Temp')]
    data[temp_cols] = data[temp_cols].astype('float64')

    active_cols = [c for c in temp_cols if data[c].notna().any()]
    return data, active_cols

def get_baseline(data, active_cols):
    """First *valid* sample of each channel. Some channels come online a sample
    or two after t=0 (NaN at row 0), so iloc[0] alone would drop them."""
    return data[active_cols].bfill().iloc[0]

def fit_initial_slopes(data, active_cols, rf_on=RF_ON_S, window=FIT_WINDOW_S):
    """Linear fit of temperature vs time over [rf_on, rf_on + window].
    Returns {channel: (slope °C/s, intercept °C)}."""
    elapsed = data['Elapsed (s)']
    mask = (elapsed >= rf_on) & (elapsed <= rf_on + window)
    t = elapsed[mask].to_numpy()
    fits = {}
    for col in active_cols:
        y = data.loc[mask, col].to_numpy()
        valid = ~np.isnan(y)
        slope, intercept = np.polyfit(t[valid], y[valid], 1)
        fits[col] = (slope, intercept)
    return fits

# %%
csv_files = sorted(f for f in list_files_with_paths(DATA_DIR) if f.endswith('.csv'))
print('\n'.join(csv_files))

# %% [markdown]
# ## Per-scan temperature rise curves

# %%
for label, (fname, desc) in SCANS.items():
    file_path = os.path.join(DATA_DIR, fname)
    data, active_cols = load_probe_data(file_path)

    baseline = get_baseline(data, active_cols)
    delta_t = data[active_cols] - baseline
    elapsed = data['Elapsed (s)']

    styles, ordered_cols = probe_styles(label, active_cols)
    fig, ax = plt.subplots(figsize=(10, 6))
    for col in ordered_cols:
        s = styles[col]
        ax.plot(elapsed, delta_t[col], color=s['color'],
                label=s['label'], linewidth=1.2)
    ax.axvline(RF_ON_S, color='gray', linestyle='--', linewidth=1,
               label=f'RF on ({RF_ON_S:.0f} s)')

    max_col = delta_t.max().idxmax()
    max_rise = delta_t[max_col].max()
    ax.set_title(f'{label}: {desc}\nMax rise: {max_rise:.2f} °C '
                 f'({max_col.replace(" Temp", "")})')
    ax.set_xlabel('Elapsed Time (s)')
    ax.set_ylabel('Temperature Rise (°C)')
    ax.legend(title='Z1=-Z (bottom) → +Z (top)', ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()

    out_path = os.path.join(FIG_DIR, f'{label}_temprise.png')
    fig.savefig(out_path, dpi=200)
    print(f'Saved {out_path}  (max rise {max_rise:.2f} °C on {max_col})')
    plt.show()

# %% [markdown]
# ## SAR back-calculation from the initial heating slope
# SAR = c · dT/dt, with the slope fit over the first 200 s after RF-on
# (t = 20–220 s) and c ≈ 4186 J/(kg·K).

# %%
sar_rows = []
for label, (fname, desc) in SCANS.items():
    file_path = os.path.join(DATA_DIR, fname)
    data, active_cols = load_probe_data(file_path)

    baseline = get_baseline(data, active_cols)
    delta_t = data[active_cols] - baseline
    elapsed = data['Elapsed (s)']
    fits = fit_initial_slopes(data, active_cols)

    styles, ordered_cols = probe_styles(label, active_cols)
    fig, ax = plt.subplots(figsize=(10, 6))
    t_fit = np.linspace(RF_ON_S, RF_ON_S + FIT_WINDOW_S, 50)
    for col in ordered_cols:
        slope, intercept = fits[col]
        sar = C_PHANTOM * slope
        probe = col.replace(' Temp', '')
        s = styles[col]
        ax.plot(elapsed, delta_t[col], color=s['color'],
                linewidth=1.0, alpha=0.6,
                label=f"{s['label']}: {1e3 * slope:.2f} mK/s → {sar:.2f} W/kg")
        ax.plot(t_fit, slope * t_fit + intercept - baseline[col], '--',
                color=s['color'], linewidth=2)
        sar_rows.append({'Scan': label, 'Description': desc, 'Probe': probe,
                         'Z position': s['zpos'],
                         'Slope (mK/s)': 1e3 * slope, 'SAR (W/kg)': sar})

    ax.axvspan(RF_ON_S, RF_ON_S + FIT_WINDOW_S, color='gray', alpha=0.12,
               label=f'fit window ({FIT_WINDOW_S:.0f} s)')
    ax.set_title(f'{label}: {desc} — initial-slope SAR fit')
    ax.set_xlabel('Elapsed Time (s)')
    ax.set_ylabel('Temperature Rise (°C)')
    ax.legend(title='Z1=-Z (bottom) → +Z (top): slope → SAR', ncol=2, fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()

    out_path = os.path.join(FIG_DIR, f'{label}_SARfit.png')
    fig.savefig(out_path, dpi=200)
    print(f'Saved {out_path}')
    plt.show()

# %% [markdown]
# ### SAR summary (W/kg per probe, per scan)

# %%
sar_df = pd.DataFrame(sar_rows)
sar_table = sar_df.pivot(index='Scan', columns='Probe', values='SAR (W/kg)').round(3)
sar_df.to_csv(os.path.join(FIG_DIR, 'sar_summary.csv'), index=False)
print(sar_table.to_string())
sar_table

# %% [markdown]
# ## Head-to-head comparisons
# Helper: side-by-side panels with shared axes and consistent probe colors.

# %%
def compare_scans(scan_labels, zeroed=True, window_s=None, title=None,
                  out_name=None):
    """Side-by-side temperature plots for the given scans.
    zeroed=True subtracts each channel's first sample (temperature rise);
    zeroed=False shows raw absolute temperature."""
    fig, axes = plt.subplots(1, len(scan_labels), figsize=(7 * len(scan_labels), 6),
                             sharex=True, sharey=True)
    if len(scan_labels) == 1:
        axes = [axes]

    for ax, label in zip(axes, scan_labels):
        fname, desc = SCANS[label]
        data, active_cols = load_probe_data(os.path.join(DATA_DIR, fname))

        elapsed = data['Elapsed (s)']
        if window_s is not None:
            sel = elapsed <= window_s
            data, elapsed = data[sel], elapsed[sel]

        if zeroed:
            y = data[active_cols] - get_baseline(data, active_cols)
        else:
            y = data[active_cols]

        # Color encodes Z position consistently across panels; per-panel legend
        # because the probe<->Z mapping flips between Right and Left orientation.
        styles, ordered_cols = probe_styles(label, active_cols)
        for col in ordered_cols:
            s = styles[col]
            ax.plot(elapsed, y[col], color=s['color'],
                    label=s['label'], linewidth=1.1)

        ax.axvline(RF_ON_S, color='gray', linestyle='--', linewidth=0.8)
        ax.set_title(f'{label}: {desc}')
        ax.set_xlabel('Elapsed Time (s)')
        ax.grid(alpha=0.3)
        ax.legend(title='Z1=-Z (bottom) → +Z (top)', ncol=2,
                  fontsize=7, loc='upper left', framealpha=0.9)

    ylabel = 'Temperature Rise (°C)' if zeroed else 'Absolute Temperature (°C)'
    axes[0].set_ylabel(ylabel)

    if title:
        fig.suptitle(title, y=1.02, fontsize=13, fontweight='bold')
    fig.tight_layout()

    if out_name:
        out_path = os.path.join(FIG_DIR, out_name)
        fig.savefig(out_path, dpi=200, bbox_inches='tight')
        print(f'Saved {out_path}')
    plt.show()

# %% [markdown]
# ### scan1 vs scan3 — best representations of the two experiments
# (PatientRight vs. PatientLeft chest). Zeroed temperature rise.

# %%
compare_scans(['scan1', 'scan3'], zeroed=True,
              title='scan1 vs scan3 — temperature rise (zeroed)',
              out_name='compare_scan1_scan3_zeroed.png')

# %% [markdown]
# ### scan2 vs scan3 — zeroed (temperature rise)

# %%
compare_scans(['scan2', 'scan3'], zeroed=True,
              title='scan2 vs scan3 — temperature rise (zeroed)',
              out_name='compare_scan2_scan3_zeroed.png')

# %% [markdown]
# ### scan2 vs scan3 — NOT zeroed (absolute temperature)
# Shows the impact of the different starting temperatures between the two takes.

# %%
compare_scans(['scan2', 'scan3'], zeroed=False,
              title='scan2 vs scan3 — absolute temperature (not zeroed)',
              out_name='compare_scan2_scan3_absolute.png')
