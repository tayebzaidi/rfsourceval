# %% [markdown]
# # Implant-Less Heating: Fiber Optic Probe Temperature Rises
# Plots the temperature rise curves from each CSV in `61026_ImplantLessHeating/`
# (gelled saline phantom, MRI exposure). One figure per file, saved to `Figures/`.
#
# File format: 2 metadata lines (probe serial numbers), then a header row with
# six probe channels (244A/B/C, 245A/B/C). Channels with no data are dropped.

# %%
import os
import sys

import pandas as pd
import matplotlib.pyplot as plt

# utils.py lives in the repo root, one level up from this directory
sys.path.append(os.path.abspath('..'))
from utils import list_files_with_paths

DATA_DIR = '.'
FIG_DIR = 'Figures'
os.makedirs(FIG_DIR, exist_ok=True)

# %%
def load_probe_data(file_path):
    """Read a PicoM/Neoptix-style CSV: skip the 2 serial-number lines,
    parse the header, and keep only probe channels that contain data."""
    data = pd.read_csv(file_path, skiprows=2)
    data.columns = data.columns.str.strip()

    temp_cols = [c for c in data.columns if c.endswith('Temp')]
    data[temp_cols] = data[temp_cols].astype('float64')

    # Drop channels that never recorded anything
    active_cols = [c for c in temp_cols if data[c].notna().any()]
    return data, active_cols

# RF exposure started 20 s into every acquisition. Derivative-based detection
# (utils.calculate_time_offset) is unreliable here — with no implant present the
# heating rate is too close to the probe noise floor.
RF_ON_S = 20.0

# %%
csv_files = sorted(f for f in list_files_with_paths(DATA_DIR) if f.endswith('.csv'))
print('\n'.join(csv_files))

# %%
for file_path in csv_files:
    data, active_cols = load_probe_data(file_path)
    name = os.path.splitext(os.path.basename(file_path))[0]

    # Temperature rise relative to the first sample of each channel
    baseline = data[active_cols].iloc[0]
    delta_t = data[active_cols] - baseline
    elapsed = data['Elapsed (s)']

    rf_on = RF_ON_S

    fig, ax = plt.subplots(figsize=(10, 6))
    for col in active_cols:
        ax.plot(elapsed, delta_t[col], label=col.replace(' Temp', ''), linewidth=1.2)

    ax.axvline(rf_on, color='gray', linestyle='--', linewidth=1,
               label=f'RF on ({rf_on:.0f} s)')

    max_col = delta_t.max().idxmax()
    max_rise = delta_t[max_col].max()
    ax.set_title(f'{name}\nMax rise: {max_rise:.2f} °C ({max_col.replace(" Temp", "")})')
    ax.set_xlabel('Elapsed Time (s)')
    ax.set_ylabel('Temperature Rise (°C)')
    ax.legend(title='Probe')
    ax.grid(alpha=0.3)
    fig.tight_layout()

    out_path = os.path.join(FIG_DIR, f'{name}.png')
    fig.savefig(out_path, dpi=200)
    print(f'Saved {out_path}  (max rise {max_rise:.2f} °C on {max_col})')
    plt.show()

# %% [markdown]
# ## Summary across all exposures

# %%
summary = []
for file_path in csv_files:
    data, active_cols = load_probe_data(file_path)
    delta_t = data[active_cols] - data[active_cols].iloc[0]
    row = {'File': os.path.basename(file_path),
           'Duration (s)': data['Elapsed (s)'].iloc[-1]}
    row.update({c.replace(' Temp', ''): round(delta_t[c].max(), 2) for c in active_cols})
    summary.append(row)

summary_df = pd.DataFrame(summary)
summary_df

# %% [markdown]
# ## SAR back-calculation from the initial heating slope
# For short heating times (before thermal conduction flattens the curve),
# the temperature rise is approximately linear and
#
# $$\mathrm{SAR} = c \cdot \frac{dT}{dt}$$
#
# We fit a line to the first 200 s after RF-on for each probe and convert
# using c ≈ 4186 J/(kg·K) (specific heat of water; gelled saline is close).

# %%
import numpy as np

C_PHANTOM = 4186.0      # J/(kg·K) — water; adjust if the gel recipe differs
FIT_WINDOW_S = 200.0    # fit duration after RF-on

def fit_initial_slopes(data, active_cols, rf_on, window=FIT_WINDOW_S):
    """Linear fit of temperature vs time over [rf_on, rf_on + window].
    Returns {channel: (slope in °C/s, intercept in °C)}."""
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
sar_rows = []
for file_path in csv_files:
    data, active_cols = load_probe_data(file_path)
    name = os.path.splitext(os.path.basename(file_path))[0]

    baseline = data[active_cols].iloc[0]
    delta_t = data[active_cols] - baseline
    elapsed = data['Elapsed (s)']
    rf_on = RF_ON_S

    fits = fit_initial_slopes(data, active_cols, rf_on)

    fig, ax = plt.subplots(figsize=(10, 6))
    t_fit = np.linspace(rf_on, rf_on + FIT_WINDOW_S, 50)
    for col in active_cols:
        slope, intercept = fits[col]
        sar = C_PHANTOM * slope
        probe = col.replace(' Temp', '')
        line, = ax.plot(elapsed, delta_t[col], linewidth=1.0, alpha=0.5,
                        label=f'{probe}: {1e3 * slope:.2f} mK/s → {sar:.2f} W/kg')
        # fit line, shifted into temperature-rise coordinates
        ax.plot(t_fit, slope * t_fit + intercept - baseline[col], '--',
                color=line.get_color(), linewidth=2)
        sar_rows.append({'File': name, 'Probe': probe,
                         'Slope (mK/s)': 1e3 * slope, 'SAR (W/kg)': sar})

    ax.axvspan(rf_on, rf_on + FIT_WINDOW_S, color='gray', alpha=0.12,
               label=f'fit window ({FIT_WINDOW_S:.0f} s)')
    ax.set_title(f'{name} — initial-slope SAR fit')
    ax.set_xlabel('Elapsed Time (s)')
    ax.set_ylabel('Temperature Rise (°C)')
    ax.legend(title='Probe (slope → SAR)')
    ax.grid(alpha=0.3)
    fig.tight_layout()

    out_path = os.path.join(FIG_DIR, f'{name}_SARfit.png')
    fig.savefig(out_path, dpi=200)
    print(f'Saved {out_path}')
    plt.show()

# %% [markdown]
# ### SAR summary (W/kg per probe location)

# %%
sar_df = pd.DataFrame(sar_rows)
sar_table = sar_df.pivot(index='File', columns='Probe', values='SAR (W/kg)').round(3)
sar_df.to_csv(os.path.join(FIG_DIR, 'sar_summary.csv'), index=False)
print(sar_table.to_string())
sar_table

# %% [markdown]
# ## Head-to-head comparison — first 400 s of every acquisition
# One figure: rows = patient side, columns = landmark, shared axes so the
# heating rates are directly comparable across all six exposures.

# %%
COMPARE_WINDOW_S = 400.0
SIDES = ['PatientLeft', 'PatientRight']
LANDMARKS = ['Head', 'Chest', 'Abdomen']
PROBE_COLORS = {'244A': 'tab:blue', '244B': 'tab:orange',
                '244C': 'tab:brown', '245A': 'tab:purple',
                '245B': 'tab:green', '245C': 'tab:red'}

fig, axes = plt.subplots(len(SIDES), len(LANDMARKS), figsize=(14, 8),
                         sharex=True, sharey=True)

for i, side in enumerate(SIDES):
    for j, landmark in enumerate(LANDMARKS):
        ax = axes[i, j]
        file_path = os.path.join(DATA_DIR, f'{side}_6probes_{landmark}Landmark_R1.csv')
        data, active_cols = load_probe_data(file_path)

        window = data[data['Elapsed (s)'] <= COMPARE_WINDOW_S]
        delta_t = window[active_cols] - window[active_cols].iloc[0]

        for col in active_cols:
            probe = col.replace(' Temp', '')
            ax.plot(window['Elapsed (s)'], delta_t[col],
                    color=PROBE_COLORS[probe], linewidth=1.0, label=probe)

        ax.axvline(RF_ON_S, color='gray', linestyle='--', linewidth=0.8)
        ax.set_title(f'{side} — {landmark}')
        ax.grid(alpha=0.3)
        if i == len(SIDES) - 1:
            ax.set_xlabel('Elapsed Time (s)')
        if j == 0:
            ax.set_ylabel('Temperature Rise (°C)')

# One legend for the whole figure (probe colors are consistent everywhere)
handles, labels = axes[0, 0].get_legend_handles_labels()
fig.legend(handles, labels, title='Probe', loc='lower center',
           ncol=len(labels), bbox_to_anchor=(0.5, 1.0))
fig.suptitle(f'First {COMPARE_WINDOW_S:.0f} s of exposure — all acquisitions',
             y=1.1)
fig.tight_layout()

out_path = os.path.join(FIG_DIR, f'comparison_first{COMPARE_WINDOW_S:.0f}s.png')
fig.savefig(out_path, dpi=200, bbox_inches='tight')
print(f'Saved {out_path}')
plt.show()
