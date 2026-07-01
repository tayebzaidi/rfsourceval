import os, sys

import numpy as np
from scipy.interpolate import RegularGridInterpolator
from scipy.interpolate import make_interp_spline, BSpline, CubicSpline
import pandas as pd
import matplotlib.pyplot as plt
import re

from sklearn.metrics import root_mean_squared_error

def read_electric_field_data(file_path, scaling_factor = 1):
    # Read the ASCII text file and extract the data
    data = np.genfromtxt(file_path, dtype=float, skip_header=2)#, max_rows= 10)

    # Extract the unique x, y, and z coordinates
    x_coords = np.unique(data[:, 0])
    y_coords = np.unique(data[:, 1])
    z_coords = np.unique(data[:, 2])

    # Reshape the electric field components into 3D arrays
    Ex_real = data[:, 3].reshape((len(x_coords), len(y_coords), len(z_coords)))
    Ex_imag = data[:, 4].reshape((len(x_coords), len(y_coords), len(z_coords)))
    Ey_real = data[:, 5].reshape((len(x_coords), len(y_coords), len(z_coords)))
    Ey_imag = data[:, 6].reshape((len(x_coords), len(y_coords), len(z_coords)))
    Ez_real = data[:, 7].reshape((len(x_coords), len(y_coords), len(z_coords)))
    Ez_imag = data[:, 8].reshape((len(x_coords), len(y_coords), len(z_coords)))

    # Combine real and imaginary parts into complex arrays
    Ex = Ex_real + 1j * Ex_imag
    Ey = Ey_real + 1j * Ey_imag
    Ez = Ez_real + 1j * Ez_imag

    # Handle single scaling factor or multiple scaling factors
    if np.isscalar(scaling_factor):
        # Scale by single scaling factor
        Ex *= scaling_factor
        Ey *= scaling_factor
        Ez *= scaling_factor
        return x_coords, y_coords, z_coords, Ex, Ey, Ez
    else:
        # Handle multiple scaling factors
        efield_bundles = []
        for factor in scaling_factor:
            # Create scaled copies for each factor
            Ex_scaled = Ex * factor
            Ey_scaled = Ey * factor
            Ez_scaled = Ez * factor
            efield_bundles.append((x_coords, y_coords, z_coords, Ex_scaled, Ey_scaled, Ez_scaled))
        return efield_bundles

def interpolate_net_tangential_field(x_coords, y_coords, z_coords, Ex, Ey, Ez, line_points):
    # Create interpolation functions for each electric field component
    #print(x_coords, y_coords, z_coords, Ex.shape, Ey.shape, x_coords.shape)
    Ex_interp = RegularGridInterpolator((x_coords, y_coords, z_coords), Ex, method='linear')
    Ey_interp = RegularGridInterpolator((x_coords, y_coords, z_coords), Ey, method='linear')
    Ez_interp = RegularGridInterpolator((x_coords, y_coords, z_coords), Ez, method='linear')

    # Convert line_points to a numpy array
    line_points = np.array(line_points)
    #print(line_points)

    # Calculate the tangent vectors for all points
    tangent_vectors = np.zeros_like(line_points)
    tangent_vectors[1:-1] = (line_points[2:] - line_points[:-2]) / 2
    tangent_vectors[0] = line_points[1] - line_points[0]
    tangent_vectors[-1] = line_points[-1] - line_points[-2]
    tangent_vectors = tangent_vectors / np.linalg.norm(tangent_vectors, axis=1)[:, np.newaxis]
    #print(tangent_vectors)

    # Interpolate the electric field components at the line points
    Ex_line = Ex_interp(line_points)
    Ey_line = Ey_interp(line_points)
    Ez_line = Ez_interp(line_points)

    # Calculate the tangential electric field components
    E_field_vectors = np.vstack((Ex_line, Ey_line, Ez_line)).T

    E_net_tangential = np.array([np.dot(E_field_vectors[i,:], tangent_vectors[i,:]) for i in range(line_points.shape[0])])

    # Calculate the complex magnitude of the electric field
    E_complex_magnitude = np.sqrt(np.abs(Ex_line)**2 + np.abs(Ey_line)**2 + np.abs(Ez_line)**2)

    return E_net_tangential

def resample_line(line_points, initial_offset, spacing = 1.0, end_distance = 40):
    # Calculate the cumulative distances along the line
    distances = np.sqrt(np.sum(np.diff(line_points, axis=0)**2, axis=1))
    cumulative_distances = np.concatenate(([0], np.cumsum(distances)))
    # print("Original Length Trajectory: ", cumulative_distances)

    # Calculate the total length of the line
    # total_length = cumulative_distances[-1]
    total_length = end_distance

    # Create evenly spaced points along the line
    even_distances = np.arange(initial_offset, total_length, spacing)

    # Perform linear interpolation to find the coordinates of the evenly spaced points
    even_points = np.zeros((len(even_distances), 2))
    even_points[:, 0] = np.interp(even_distances, cumulative_distances, line_points[:, 0])
    even_points[:, 1] = np.interp(even_distances, cumulative_distances, line_points[:, 1])

    # print(line_points)
    # print(even_points)
    #plt.scatter(even_points[:,0],even_points[:,1], 16, color='blue')
    #plt.scatter(line_points[:,0], line_points[:,1], 7, color='red')
    # plt.show()
    return even_points

def get_etan_trajectory(trajectory_file, efield_bundle, x_offset = -0.210, y_offset = -0.09, z_offset = -0.325, initial_offset = 1.5, spacing = 1.0, end_distance = 40, wire_height = 3.0, invert_z = True, invert_x = True):
    # Load points from trajectory file
    line_points = np.genfromtxt(trajectory_file, delimiter=',', dtype=float, skip_header=0)

    line_points_orig = np.copy(line_points)
    
    # Resample line_points to coincide with TF measurements
    line_points = resample_line(line_points, initial_offset, spacing = spacing, end_distance = end_distance) # 1 cm spacing, 40 cm end

    # Add Y dimension to line_points (y = 3cm)
    wire_height = wire_height #cm
    line_points = np.insert(line_points, [1], wire_height, axis=1)

    #Convert from centimeters to meters
    line_points *= 0.01
    #print(line_points)

    # if "Scan1_" in trajectory_file:
    #     print(trajectory_file)
    #     print(line_points[0,:])
    #     print(line_points[-1,:])

    #Shift to correct positioning
    # Bottom left corner of Fusion file is 0,0,0 which represents -210, -325 for x and z
    # x_offset = -0.210
    # y_offset = -0.09
    # z_offset = -0.325
    # print("Line points before inversion and shift")
    # print(end_distance)
    # print(line_points_orig)
    # print(line_points)
    if invert_z:
        multiplier_z = -1
    else:
        multiplier_z = 1

    if invert_x:
        multiplier_x = -1
    else:
        multiplier_x = 1
    line_points[:,0] = (line_points[:,0] + x_offset) * multiplier_x
    line_points[:,1] = line_points[:,1] + y_offset
    line_points[:,2] = (line_points[:,2] + z_offset) * multiplier_z
    # if "Scan1_" in trajectory_file:
    #     print(trajectory_file)
    #     print(line_points[0,:])
    #     print(line_points[-1,:])
    # print(line_points.shape)
    # print(line_points)
    # return

    # if trajectory_file == "TFMagnetWireValidation/Scan1_L2.csv":
    #     print("Scan1 L2: ")
    #     print(line_points)
    #     print(line_points_orig)
    # # elif trajectory_file == "TFMagnetWireValidation/Scan2_L2.csv":
    # #     print("Scan2 L2: ")
    # #     print(line_points)

    x_coords, y_coords, z_coords, Ex, Ey, Ez = efield_bundle
    E_net_tangential = interpolate_net_tangential_field(x_coords, y_coords, z_coords, Ex, Ey, Ez, line_points)
    
    return E_net_tangential

def resample_line_finalpredictions(line_points, initial_offset, spacing = 10.0):
    # Calculate the cumulative distances along the line
    distances = np.sqrt(np.sum(np.diff(line_points, axis=0)**2, axis=1))
    cumulative_distances = np.concatenate(([0], np.cumsum(distances)))

    # Calculate the total length of the line
    total_length = cumulative_distances[-1]
    # print("Total Length: ", total_length)

    # Create evenly spaced points along the line
    even_distances = np.arange(initial_offset, total_length, spacing)

    # Perform linear interpolation to find the coordinates of the evenly spaced points
    even_points = np.zeros((len(even_distances), 3))
    even_points[:, 0] = np.interp(even_distances, cumulative_distances, line_points[:, 0])
    even_points[:, 1] = np.interp(even_distances, cumulative_distances, line_points[:, 1])
    even_points[:, 2] = np.interp(even_distances, cumulative_distances, line_points[:, 2])
    # print(even_points.shape)

    # print(line_points)
    # print(even_points)
    #plt.scatter(even_points[:,0],even_points[:,1], 16, color='blue')
    #plt.scatter(line_points[:,0], line_points[:,1], 7, color='red')
    # plt.show()
    return even_points

def get_etan_trajectory_finalpredictions(trajectory_points, efield_bundle, x_offset = 0, y_offset = 0, z_offset = 0, initial_offset = 0, spacing = 10.0):    
    # Resample line_points to coincide with TF measurements
    # print(trajectory_points)
    line_points = resample_line_finalpredictions(trajectory_points, initial_offset, spacing = spacing) # 1 cm spacing by default

    #Convert to meters
    line_points = 0.001 * line_points # was in mm, need to convert to meters

    #Shift to correct positioning
    line_points[:,0] = line_points[:,0] + x_offset
    line_points[:,1] = line_points[:,1] + y_offset
    line_points[:,2] = line_points[:,2] + z_offset
    # print(line_points.shape)
    # return

    x_coords, y_coords, z_coords, Ex, Ey, Ez = efield_bundle
    E_net_tangential = interpolate_net_tangential_field(x_coords, y_coords, z_coords, Ex, Ey, Ez, line_points)
    
    return E_net_tangential

# Define function to calculate time offset using unsmoothed derivative and single-channel detection
def calculate_time_offset(file_path, derivative_threshold=0.02): # Have used 0.03 as the default before as well
    # Load the data
    data = pd.read_csv(file_path)
    
    # Remove baseline temperature
    baseline_values = data.iloc[0, 3:12].astype('float64')
    data.iloc[:, 3:12] = data.iloc[:, 3:12].astype('float64') - baseline_values
    #print(baseline_values)
    
    # Calculate the derivative (rate of change) directly from the data
    derivative_data = data.iloc[:, 3:12].diff()
    
    # Detect significant change based on the derivative threshold
    significant_change = (derivative_data.abs() > derivative_threshold).any(axis=1)  # Any channel showing a significant change
    start_indices = significant_change[significant_change].index
    
    if not start_indices.empty:
        start_index = start_indices[0]
    else:
        start_index = 0
    
    # Calculate the time offset
    start_time = data.loc[start_index, 'Elapsed (s)']
    
    return start_time

# Define function to get temperature rise at 180 seconds with the time/baseline offsetted data
def get_temperature_rise_at_180_seconds(file_path, offset, temp_rise_time = 180):
    # Load the data
    data = pd.read_csv(file_path)
    # print(data)
    # print(data.columns)
    # print(data['Elapsed (s)'])
    # print(data)
    
    # Remove baseline temperature
    baseline_values = data.iloc[1, 3:12].astype('float64')
    data.iloc[:, 3:12] = data.iloc[:, 3:12].astype('float64') - baseline_values
    
    # Adjust the time offset
    data = data[data['Elapsed (s)'] >= offset].reset_index(drop=True)
    data['Elapsed (s)'] = data['Elapsed (s)'] - offset
    
    # Find the temperature at 180 seconds
    temp_at_temp_rise_time = data[data['Elapsed (s)'] == temp_rise_time].iloc[:, 3:12]
    
    if temp_at_temp_rise_time.empty:
        # If no exact match, interpolate to find the temperature at 180 seconds
        temp_at_temp_rise_time = data.set_index('Elapsed (s)').iloc[:, :3].reindex(data['Elapsed (s)']).interpolate().loc[temp_rise_time]
    
    return temp_at_temp_rise_time

# Function to get all files in the selected directory with their full paths
def list_files_with_paths(directory_path):
    # List to hold full file paths
    files_list = []
    
    # Iterate over all the files in the directory
    for filename in os.listdir(directory_path):
        # Construct full file path
        file_path = os.path.join(directory_path, filename)
        
        # Check if it is a file (not a directory)
        if os.path.isfile(file_path):
            # Append full file path to the list
            files_list.append(file_path)
    
    return files_list

# Function to extract scan numbers from the filename and return as a sortable key
def extract_scan_numbers(filename):
    scan_numbers = re.findall(r'Scan(\d+)', filename)
    scan_numbers = list(map(int, scan_numbers))  # Convert to integers
    return scan_numbers

def get_temperature_rises(directory_path, columns = ['246A Temp', '247A Temp'], temp_rise_time = 180):

    file_paths = list_files_with_paths(directory_path)
    #print(file_paths)

    # Sort files based on scan numbers
    file_paths.sort(key=lambda x: extract_scan_numbers(os.path.basename(x)))
    # print(file_paths)

    temperature_rises = []
    for file_path in file_paths:
        offset = calculate_time_offset(file_path)
        # print(file_path, offset)
        temp_rise = get_temperature_rise_at_180_seconds(file_path, offset, temp_rise_time)
        # print("Temperature Rise: ", temp_rise)
        temperature_rises.append(temp_rise[columns].to_numpy())

    temperature_rises = np.squeeze(np.array(temperature_rises, dtype='float64'))
    return temperature_rises

# Function to read and clean the CSV files by manually splitting columns
def read_and_clean_csv(filepath):
    # Read the file as plain text
    with open(filepath, 'r') as file:
        lines = file.readlines()

    # Drop the first line (metadata)
    lines = lines[1:]
    
    # Split each line based on commas and convert to float
    data = [list(map(float, line.split(','))) for line in lines]
    
    # Create a DataFrame from the cleaned data
    df = pd.DataFrame(data, columns=['X', 'Z'])
    
    return df
    
def get_best_calibration_constant(cals, temps, transfer_function, etans_allscans):
    errors = []
    for i in range(len(cals)):
        tpred = [cals[i] * calculate_integral(transfer_function, etans_allscans[j]) for j in range(len(etans_allscans))]
        # print(tpred)
        error = root_mean_squared_error(temps, tpred)
        errors.append(error)
    
    errors = np.array(errors)
    # print("Calibration Errors: ", errors)
    min_error_idx = np.where(errors == errors.min())[0][0]
    return cals[min_error_idx]

def get_calibration_constants(measured_temps, transfer_function, etans_allscans):
    calibration_constants = [measured_temps[i] / calculate_integral(transfer_function, etans_allscans[i]) for i in range(len(etans_allscans))]
    return calibration_constants

def get_predicted_temperatures(calibration_constant, transfer_function, etans_allscans):
    predicted_temperatures = [calibration_constant * calculate_integral(transfer_function, etans_allscans[i]) for i in range(len(etans_allscans))]
    return predicted_temperatures

def calculate_integral(transfer_function, etan, tf_spacing = 0.1):
    num_points = transfer_function.shape[0]
    wire_length = num_points * tf_spacing
    spacings = np.linspace(0, wire_length, num_points)
    integrand = transfer_function * etan
    integral = np.trapz(integrand, spacings)
    out = np.abs(integral)**2
    return out

# def calculate_integral(transfer_function, etan):
#     num_points = transfer_function.shape[0]
#     spacings = np.linspace(0,40,num_points)
#     integrand = transfer_function * etan
#     integral = sum(integrand)
#     out = np.abs(integral)**2
#     return out

def calculate_integral_dot(transfer_function, etan):
    # num_points = transfer_function.shape[0]
    # spacings = np.linspace(0,40,num_points)
    integral = np.dot(transfer_function, etan)
    out = np.abs(integral)**2
    return out

# def get_interpolated_transfer_function(initial_offset, initial_offset_hires, tf_spacing, tf_spacing_hires, transfer_function, wire_length = 40):
#     spacing_original = np.arange(initial_offset, initial_offset + transfer_function.shape[0] * tf_spacing, tf_spacing)  # Current spacing
#     spacing_hires = np.arange(initial_offset_hires, wire_length, tf_spacing_hires) 
#     interpolator_tf = CubicSpline(spacing_original, transfer_function, bc_type='natural', extrapolate=True)
#     interpolated_transfer_function = interpolator_tf(spacing_hires)
#     return interpolated_transfer_function

def get_interpolated_transfer_function(
    initial_offset, 
    initial_offset_hires, 
    tf_spacing, 
    tf_spacing_hires, 
    transfer_function, 
    wire_length=40,
    n_boundary_points=3  # how many points to use for boundary extrapolation
):
    """
    Returns a piecewise interpolation:
      - Cubic spline within [x_min, x_max],
      - Linear extrapolation outside [x_min, x_max].
    """
    # Original "x" (domain) for the transfer_function
    x_original = np.arange(
        initial_offset, 
        initial_offset + transfer_function.shape[0] * tf_spacing, 
        tf_spacing
    )
    
    # New "x" (domain) for higher-resolution sampling
    x_hires = np.arange(initial_offset_hires, wire_length, tf_spacing_hires)
    
    # Build the cubic spline over the valid domain
    # You can choose a different bc_type if needed
    cs = CubicSpline(x_original, transfer_function, bc_type='not-a-knot')
    
    # Helper function to get slope & intercept using a small window of boundary points
    def linear_fit_boundary(x_vals, y_vals):
        """
        Returns slope and intercept of a least-squares linear fit to (x_vals, y_vals).
        """
        # Fit a line: y = m*x + c
        A = np.column_stack([x_vals, np.ones_like(x_vals)])
        # Solve for [m, c] in a least squares sense
        m, c = np.linalg.lstsq(A, y_vals, rcond=None)[0]
        return m, c
    
    # --- Left boundary linear fit ---
    # We'll take the first `n_boundary_points` from x_original for slope & intercept
    n_left = min(n_boundary_points, len(x_original))  # in case you have fewer data
    x_left = x_original[:n_left]
    y_left = transfer_function[:n_left]
    slope_left, intercept_left = linear_fit_boundary(x_left, y_left)
    
    # --- Right boundary linear fit ---
    # We'll take the last `n_boundary_points` from x_original
    n_right = min(n_boundary_points, len(x_original))
    x_right = x_original[-n_right:]
    y_right = transfer_function[-n_right:]
    slope_right, intercept_right = linear_fit_boundary(x_right, y_right)
    
    x_min = x_original[0]
    x_max = x_original[-1]
    
    # Evaluate piecewise
    def piecewise_eval(x):
        if x < x_min:
            return slope_left * x + intercept_left
        elif x > x_max:
            return slope_right * x + intercept_right
        else:
            return cs(x)
    
    # Vectorized evaluation
    interpolated_transfer_function = np.array([piecewise_eval(xx) for xx in x_hires])
    return interpolated_transfer_function


def convert_etans_to_complex(etan_str: str) -> complex:
    """
    Convert a string of the form 'x.xxxx + y.yyyyi' or 'x.xxxx - y.yyyyi'
    into a Python complex number.
    Example: '0.1234 + 0.5678i' ->  (0.1234+0.5678j)
    """
    # Remove spaces
    clean_str = etan_str.replace(" ", "")
    # Replace 'i' with 'j' for Python's complex notation
    clean_str = clean_str.replace("i", "j")
    # Now Python can parse it directly
    return complex(clean_str)

def load_etans_from_folder_prescaled(folder_path: str) -> list[np.ndarray]:
    """
    Same as load_etans_from_folder but skips B1+ scaling,
    for use when etans are already properly scaled (e.g. Safa's dataset).
    """
    all_etans = []

    csv_files = [f for f in os.listdir(folder_path) if f.endswith(".csv")]
    csv_files.sort(key=lambda f: int(f.split('_')[0][2:]))

    for filename in csv_files:
        if filename.endswith(".csv"):
            file_path = os.path.join(folder_path, filename)
            df = pd.read_csv(file_path)
            etan_complex = df['Etan []'].apply(convert_etans_to_complex)
            etan_array = etan_complex.to_numpy()
            all_etans.append(etan_array)

    return all_etans


def load_etans_from_folder(folder_path: str) -> list[np.ndarray]:
    """
    1. Scans a folder for all .csv files
    2. Reads each CSV into a pandas DataFrame
    3. Converts the 'Etan' column into a NumPy array of complex numbers
    4. Collects each array in a list and returns that list
    """
    all_etans = []

    # Gather CSV files first
    csv_files = [f for f in os.listdir(folder_path) if f.endswith(".csv")]
    
    # Sort based on the integer that appears after "ID" and before "_DBS"
    # e.g., "ID12_DBS.csv" -> f.split('_')[0] = "ID12" -> number = int("12")
    csv_files.sort(key=lambda f: int(f.split('_')[0][2:]))

    for filename in csv_files:
        if filename.endswith(".csv"):
            file_path = os.path.join(folder_path, filename)

            # Read the CSV file
            df = pd.read_csv(file_path)

            # Apply the conversion to complex for each row in 'Etan'
            etan_complex = df['Etan []'].apply(convert_etans_to_complex)

            # Convert to NumPy array
            etan_array = etan_complex.to_numpy()
            # etan_array = etan_array[::-1]

            # Scale according to B1 plus for Sana's etans
            desired_b1p = 4.2e-06
            simulation_b1p = 0.9e-06
            scaling_factor = (desired_b1p / simulation_b1p)
            etan_array *= scaling_factor

            # Append to our list of arrays
            all_etans.append(etan_array)

    return all_etans
