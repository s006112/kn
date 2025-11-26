<?php
session_start();

// Newton-Raphson calculation functions (moved to global scope)
function calculateFIV($if_value, $row) {
    try {
        $fiv = 0;
        $fiv += isset($row['FIV_0']) ? $row['FIV_0'] : 0;
        $fiv += (isset($row['FIV_1']) ? $row['FIV_1'] : 0) * $if_value;
        $fiv += (isset($row['FIV_2']) ? $row['FIV_2'] : 0) * pow($if_value, 2);
        $fiv += (isset($row['FIV_3']) ? $row['FIV_3'] : 0) * pow($if_value, 3);
        $fiv += (isset($row['FIV_4']) ? $row['FIV_4'] : 0) * pow($if_value, 4);
        $fiv += (isset($row['FIV_5']) ? $row['FIV_5'] : 0) * pow($if_value, 5);
        $fiv += (isset($row['FIV_6']) ? $row['FIV_6'] : 0) * pow($if_value, 6);
        return $fiv;
    } catch (Exception $e) {
        return 1.0; // Default fallback value
    }
}

function calculateFIVDerivative($if_value, $row) {
    try {
        $fiv_derivative = 0;
        $fiv_derivative += isset($row['FIV_1']) ? $row['FIV_1'] : 0;
        $fiv_derivative += (isset($row['FIV_2']) ? $row['FIV_2'] : 0) * 2 * $if_value;
        $fiv_derivative += (isset($row['FIV_3']) ? $row['FIV_3'] : 0) * 3 * pow($if_value, 2);
        $fiv_derivative += (isset($row['FIV_4']) ? $row['FIV_4'] : 0) * 4 * pow($if_value, 3);
        $fiv_derivative += (isset($row['FIV_5']) ? $row['FIV_5'] : 0) * 5 * pow($if_value, 4);
        $fiv_derivative += (isset($row['FIV_6']) ? $row['FIV_6'] : 0) * 6 * pow($if_value, 5);
        return $fiv_derivative;
    } catch (Exception $e) {
        return 0.0; // Default fallback value
    }
}

function calculateFIL($if_value, $row) {
    try {
        $fil = 0;
        $fil += isset($row['FIL_0']) ? $row['FIL_0'] : 0;
        $fil += (isset($row['FIL_1']) ? $row['FIL_1'] : 0) * $if_value;
        $fil += (isset($row['FIL_2']) ? $row['FIL_2'] : 0) * pow($if_value, 2);
        $fil += (isset($row['FIL_3']) ? $row['FIL_3'] : 0) * pow($if_value, 3);
        $fil += (isset($row['FIL_4']) ? $row['FIL_4'] : 0) * pow($if_value, 4);
        $fil += (isset($row['FIL_5']) ? $row['FIL_5'] : 0) * pow($if_value, 5);
        $fil += (isset($row['FIL_6']) ? $row['FIL_6'] : 0) * pow($if_value, 6);

        // If all coefficients are zero or missing, return 1.0 as a reasonable default
        if ($fil == 0) {
            return 1.0;
        }

        return $fil;
    } catch (Exception $e) {
        return 1.0; // Default fallback value
    }
}

function calculateFILDerivative($if_value, $row) {
    try {
        $fil_derivative = 0;
        $fil_derivative += isset($row['FIL_1']) ? $row['FIL_1'] : 0;
        $fil_derivative += (isset($row['FIL_2']) ? $row['FIL_2'] : 0) * 2 * $if_value;
        $fil_derivative += (isset($row['FIL_3']) ? $row['FIL_3'] : 0) * 3 * pow($if_value, 2);
        $fil_derivative += (isset($row['FIL_4']) ? $row['FIL_4'] : 0) * 4 * pow($if_value, 3);
        $fil_derivative += (isset($row['FIL_5']) ? $row['FIL_5'] : 0) * 5 * pow($if_value, 4);
        $fil_derivative += (isset($row['FIL_6']) ? $row['FIL_6'] : 0) * 6 * pow($if_value, 5);
        return $fil_derivative;
    } catch (Exception $e) {
        return 0.0; // Default fallback value
    }
}

function calculateObjectiveFunction($if_value, $k_eta, $k_phi, $row) {
    try {
        $fiv = calculateFIV($if_value, $row);
        $fil = calculateFIL($if_value, $row);

        // f(target_If) = kη × (target_If / 1000) × FIV(target_If) - kΦ × FIL(target_If)
        $f = $k_eta * ($if_value / 1000.0) * $fiv - $k_phi * $fil;
        return $f;
    } catch (Exception $e) {
        return 0.0; // Default fallback value
    }
}

function calculateObjectiveFunctionDerivative($if_value, $k_eta, $k_phi, $row) {
    try {
        $fiv = calculateFIV($if_value, $row);
        $fiv_derivative = calculateFIVDerivative($if_value, $row);
        $fil_derivative = calculateFILDerivative($if_value, $row);

        // f'(target_If) = kη × (FIV(target_If)/1000 + (target_If/1000) × FIV'(target_If)) - kΦ × FIL'(target_If)
        $f_derivative = $k_eta * ($fiv / 1000.0 + ($if_value / 1000.0) * $fiv_derivative) - $k_phi * $fil_derivative;

        // Division by zero protection
        if (abs($f_derivative) < 1e-10) {
            return 1e-10; // Small non-zero value to prevent division by zero
        }

        return $f_derivative;
    } catch (Exception $e) {
        return 1e-10; // Default fallback value to prevent division by zero
    }
}

// Calculate forward voltage at target current and junction temperature
function calculateVf($target_if, $target_tj, $row) {
    try {
        // Step 1: Get absolute voltage at target current (FIV returns absolute voltage, not factor)
        $vf_at_25C = calculateFIV($target_if, $row);

        // Step 2: Apply temperature derating using existing vf_factor from Phase 8
        // Calculate FTV(target_Tj) - temperature voltage factor
        $vf_factor = 0;
        $vf_factor += isset($row['FTV_0']) ? $row['FTV_0'] : 0;
        $vf_factor += (isset($row['FTV_1']) ? $row['FTV_1'] : 0) * $target_tj;
        $vf_factor += (isset($row['FTV_2']) ? $row['FTV_2'] : 0) * pow($target_tj, 2);
        $vf_factor += (isset($row['FTV_3']) ? $row['FTV_3'] : 0) * pow($target_tj, 3);
        $vf_factor += (isset($row['FTV_4']) ? $row['FTV_4'] : 0) * pow($target_tj, 4);
        $vf_factor += (isset($row['FTV_5']) ? $row['FTV_5'] : 0) * pow($target_tj, 5);
        $vf_factor += (isset($row['FTV_6']) ? $row['FTV_6'] : 0) * pow($target_tj, 6);

        // Step 3: Apply temperature derating factor
        $vf_final = $vf_at_25C * $vf_factor;

        return $vf_final;
    } catch (Exception $e) {
        return 3.0; // Default fallback voltage
    }
}

// Calculate forward voltage with detailed debug information
function calculateVfWithDebug($target_if, $target_tj, $row) {
    try {
        // Step 1: Get absolute voltage at target current (FIV returns absolute voltage, not factor)
        $vf_at_25C = calculateFIV($target_if, $row);

        // Step 2: Calculate FTV(target_Tj) - temperature voltage factor
        $vf_factor = 0;
        $vf_factor += isset($row['FTV_0']) ? $row['FTV_0'] : 0;
        $vf_factor += (isset($row['FTV_1']) ? $row['FTV_1'] : 0) * $target_tj;
        $vf_factor += (isset($row['FTV_2']) ? $row['FTV_2'] : 0) * pow($target_tj, 2);
        $vf_factor += (isset($row['FTV_3']) ? $row['FTV_3'] : 0) * pow($target_tj, 3);
        $vf_factor += (isset($row['FTV_4']) ? $row['FTV_4'] : 0) * pow($target_tj, 4);
        $vf_factor += (isset($row['FTV_5']) ? $row['FTV_5'] : 0) * pow($target_tj, 5);
        $vf_factor += (isset($row['FTV_6']) ? $row['FTV_6'] : 0) * pow($target_tj, 6);

        // Step 3: Apply temperature derating factor
        $vf_final = $vf_at_25C * $vf_factor;

        return [
            'vf_final' => $vf_final,
            'vf_at_25C' => $vf_at_25C,
            'fiv' => $vf_at_25C,  // FIV is the absolute voltage
            'ftv' => $vf_factor,  // FTV is the temperature factor
            'vf_test' => 'N/A'    // Not used since FIV returns absolute voltage
        ];
    } catch (Exception $e) {
        return [
            'vf_final' => 3.0,
            'vf_at_25C' => 3.0,
            'fiv' => 3.0,
            'ftv' => 1.0,
            'vf_test' => 'N/A'
        ];
    }
}

// Database connection (identical to general.php)
$servername = "localhost";
$username = "baltechind_kenny";
$password = "Kenny123";
$dbname = "baltechind_grow";
$_SESSION_db = new mysqli($servername, $username, $password, $dbname);
if ($_SESSION_db->connect_error) die("Connection failed: " . $_SESSION_db->connect_error);

// Phase 2: Handle form submission and store parameters in session
$form_submitted = false;
$validation_errors = [];
$success_message = "";

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['calculate_params'])) {
    $form_submitted = true;

    // Validate and store Target CCT (from dropdown)
    if (isset($_POST['target_cct']) && !empty($_POST['target_cct']) && is_numeric($_POST['target_cct'])) {
        $_SESSION['target_cct'] = (float)$_POST['target_cct'];
    } else {
        $validation_errors[] = "Please select a Target CCT from the dropdown";
    }

    // Validate and store Target Luminaire Lumen Output
    if (isset($_POST['target_lumen']) && is_numeric($_POST['target_lumen']) && $_POST['target_lumen'] > 0) {
        $_SESSION['target_lumen'] = (float)$_POST['target_lumen'];
    } else {
        $validation_errors[] = "Target Luminaire Lumen Output must be a positive number";
    }

    // Validate and store Target Luminaire Efficacy
    if (isset($_POST['target_efficacy'])) {
        $value = str_replace(',', '.', $_POST['target_efficacy']); // allow "12,5" style
        if (is_numeric($value) && (float)$value > 0) {
            $_SESSION['target_efficacy'] = (float)$value;
        } else {
            $validation_errors[] = "Target Luminaire Efficacy must be a positive number (lm/W)";
        }
    }
    // Validate and store Luminaire Optical Transmission Rate
    if (isset($_POST['optical_transmission']) && is_numeric($_POST['optical_transmission']) && $_POST['optical_transmission'] >= 1 && $_POST['optical_transmission'] <= 100) {
        $_SESSION['optical_transmission'] = (float)$_POST['optical_transmission'];
    } else {
        $validation_errors[] = "Luminaire Optical Transmission Rate must be between 1-100 percent";
    }

    // Validate and store Power Supply Efficiency
    if (isset($_POST['power_efficiency']) && is_numeric($_POST['power_efficiency']) && $_POST['power_efficiency'] >= 1 && $_POST['power_efficiency'] <= 100) {
        $_SESSION['power_efficiency'] = (float)$_POST['power_efficiency'];
    } else {
        $validation_errors[] = "Power Supply Efficiency must be between 1-100 percent";
    }

    // Validate and store Junction Temperature
    if (isset($_POST['junction_temp']) && is_numeric($_POST['junction_temp'])) {
        $_SESSION['junction_temp'] = (float)$_POST['junction_temp'];
    } else {
        $validation_errors[] = "Junction Temperature must be a valid number (°C)";
    }

    // Validate and store Maximum LED Chain Voltage
    if (isset($_POST['v_chain_max']) && is_numeric($_POST['v_chain_max']) && $_POST['v_chain_max'] > 0) {
        $_SESSION['v_chain_max'] = (float)$_POST['v_chain_max'];
    } else {
        $validation_errors[] = "Maximum LED Chain Voltage must be a positive number (V)";
    }

    // Validate and store SMT Cost in RMB
    if (isset($_POST['smt_cost_rmb']) && is_numeric($_POST['smt_cost_rmb']) && $_POST['smt_cost_rmb'] >= 0) {
        $_SESSION['smt_cost_rmb'] = (float)$_POST['smt_cost_rmb'];
    } else {
        $validation_errors[] = "SMT Cost in RMB must be a positive number or zero";
    }

    // Validate and store USD Exchange Rate
    if (isset($_POST['usd_rate']) && is_numeric($_POST['usd_rate']) && $_POST['usd_rate'] > 0) {
        $_SESSION['usd_rate'] = (float)$_POST['usd_rate'];
    } else {
        $validation_errors[] = "USD Exchange Rate must be a positive number greater than 0";
    }

    // Validate and store Target CRI (from dropdown)
    if (isset($_POST['target_cri']) && !empty($_POST['target_cri']) && is_numeric($_POST['target_cri'])) {
        $_SESSION['target_cri'] = (float)$_POST['target_cri'];
    } else {
        $validation_errors[] = "Please select a Target CRI from the dropdown";
    }

    // Set success message if no validation errors
    if (empty($validation_errors)) {
        $success_message = "Parameters successfully stored! Ready for LED count calculations.";
    }
}

// Retrieve stored values for form display
$target_cct = isset($_SESSION['target_cct']) ? $_SESSION['target_cct'] : '';
$target_lumen = isset($_SESSION['target_lumen']) ? $_SESSION['target_lumen'] : '';
$target_efficacy = isset($_SESSION['target_efficacy']) ? $_SESSION['target_efficacy'] : '';
$junction_temp = isset($_SESSION['junction_temp']) ? $_SESSION['junction_temp'] : 65; // Default to 65°C
$v_chain_max = isset($_SESSION['v_chain_max']) ? $_SESSION['v_chain_max'] : 50; // Default to 50V
$smt_cost_rmb = isset($_SESSION['smt_cost_rmb']) ? $_SESSION['smt_cost_rmb'] : 0.01; // Default to 0.01 RMB
$usd_rate = isset($_SESSION['usd_rate']) ? $_SESSION['usd_rate'] : 7.00; // Default to 7.00 RMB per USD
$optical_transmission = isset($_SESSION['optical_transmission']) ? $_SESSION['optical_transmission'] : 80; // Default to 80%
$power_efficiency = isset($_SESSION['power_efficiency']) ? $_SESSION['power_efficiency'] : 85; // Default to 85%
$target_cri = isset($_SESSION['target_cri']) ? $_SESSION['target_cri'] : '';

// Phase 1: Read LED_CoE table structure and data
$table_info = [];
$connection_status = "Failed";
$error_message = "";

try {
    // Check if LED_CoE table exists and get its structure
    $structure_sql = "DESCRIBE LED_CoE";
    $structure_result = $_SESSION_db->query($structure_sql);

    if ($structure_result) {
        while ($row = $structure_result->fetch_assoc()) {
            $table_info[] = $row;
        }

        $connection_status = "Success";
    } else {
        $error_message = "Error reading table structure: " . $_SESSION_db->error;
    }

    // Get unique values for CCT and CRI dropdowns
    $cct_options = [];
    $cri_options = [];

    if ($connection_status === "Success") {
        // Get unique CCT values for dropdown
        $cct_sql = "SELECT DISTINCT CCT FROM LED_CoE WHERE CCT IS NOT NULL ORDER BY CCT ASC";
        $cct_result = $_SESSION_db->query($cct_sql);
        if ($cct_result) {
            while ($row = $cct_result->fetch_assoc()) {
                $cct_options[] = $row['CCT'];
            }
        }

        // Get unique CRI values for dropdown
        $cri_sql = "SELECT DISTINCT CRI FROM LED_CoE WHERE CRI IS NOT NULL ORDER BY CRI ASC";
        $cri_result = $_SESSION_db->query($cri_sql);
        if ($cri_result) {
            while ($row = $cri_result->fetch_assoc()) {
                $cri_options[] = $row['CRI'];
            }
        }
    }

} catch (Exception $e) {
    $error_message = "Exception: " . $e->getMessage();
}

// Step 4.2: Basic LED Candidate Query
$led_candidates = [];
$led_config_solutions = [];
$candidate_count = 0;
$query_executed = false;

// Calculate derived LED requirements from luminaire specifications
$target_led_lumen = 0;
$target_led_efficacy = 0;

if ($form_submitted && empty($validation_errors) && !empty($_SESSION['target_lumen']) && !empty($_SESSION['target_efficacy']) && !empty($_SESSION['optical_transmission']) && !empty($_SESSION['power_efficiency'])) {
    // Derive Target LED Lumen Output = Target Luminaire Lumen Output ÷ (Optical Transmission Rate % ÷ 100)
    $optical_factor = $_SESSION['optical_transmission'] / 100.0;
    $target_led_lumen = $_SESSION['target_lumen'] / $optical_factor;

    // Derive Target LED Efficacy = Target Luminaire Efficacy ÷ (Optical Transmission Rate % ÷ 100 × Power Supply Efficiency % ÷ 100)
    $power_factor = $_SESSION['power_efficiency'] / 100.0;
    $combined_efficiency = $optical_factor * $power_factor;
    $target_led_efficacy = $_SESSION['target_efficacy'] / $combined_efficiency;
}

// Only execute query if form was submitted and we have valid CCT and CRI
if ($form_submitted && empty($validation_errors) && !empty($_SESSION['target_cct']) && !empty($_SESSION['target_cri'])) {
    try {
        // Reconnect to database for LED candidate query
        $query_db = new mysqli($servername, $username, $password, $dbname);
        if (!$query_db->connect_error) {
            $candidate_sql = "SELECT * FROM LED_CoE WHERE CCT = ? AND CRI = ?";
            $candidate_stmt = $query_db->prepare($candidate_sql);

            if ($candidate_stmt) {
                $candidate_stmt->bind_param("dd", $_SESSION['target_cct'], $_SESSION['target_cri']);
                $candidate_stmt->execute();
                $candidate_result = $candidate_stmt->get_result();

                if ($candidate_result) {
                    while ($row = $candidate_result->fetch_assoc()) {
                        // Step 4.4: Add Temperature Derating Calculations
                        $tj = $_SESSION['junction_temp'];

                        // Calculate Lumen Factor using 6th-order polynomial
                        $lumen_factor = 0;
                        try {
                            $lumen_factor =
                                (isset($row['FTL_0']) ? $row['FTL_0'] : 0) +
                                (isset($row['FTL_1']) ? $row['FTL_1'] : 0) * $tj +
                                (isset($row['FTL_2']) ? $row['FTL_2'] : 0) * pow($tj, 2) +
                                (isset($row['FTL_3']) ? $row['FTL_3'] : 0) * pow($tj, 3) +
                                (isset($row['FTL_4']) ? $row['FTL_4'] : 0) * pow($tj, 4) +
                                (isset($row['FTL_5']) ? $row['FTL_5'] : 0) * pow($tj, 5) +
                                (isset($row['FTL_6']) ? $row['FTL_6'] : 0) * pow($tj, 6);
                        } catch (Exception $e) {
                            $lumen_factor = 1.0; // Default to no derating if calculation fails
                        }

                        // Step 4.5: Calculate Vf Factor using 6th-order polynomial
                        $vf_factor = 0;
                        try {
                            $vf_factor =
                                (isset($row['FTV_0']) ? $row['FTV_0'] : 0) +
                                (isset($row['FTV_1']) ? $row['FTV_1'] : 0) * $tj +
                                (isset($row['FTV_2']) ? $row['FTV_2'] : 0) * pow($tj, 2) +
                                (isset($row['FTV_3']) ? $row['FTV_3'] : 0) * pow($tj, 3) +
                                (isset($row['FTV_4']) ? $row['FTV_4'] : 0) * pow($tj, 4) +
                                (isset($row['FTV_5']) ? $row['FTV_5'] : 0) * pow($tj, 5) +
                                (isset($row['FTV_6']) ? $row['FTV_6'] : 0) * pow($tj, 6);
                        } catch (Exception $e) {
                            $vf_factor = 1.0; // Default to no derating if calculation fails
                        }

                        // Step 5.3: Calculate kη (Reference LPW with Factor)
                        $k_eta = 0;
                        try {
                            if ($target_led_efficacy > 0) {
                                $k_eta = $target_led_efficacy * $vf_factor;
                            }
                        } catch (Exception $e) {
                            $k_eta = 0;
                        }

                        // Step 5.4: Calculate kΦ (Reference Lumen with Factor)
                        $k_phi = 0;
                        try {
                            if (isset($row['lm_test']) && $row['lm_test'] > 0) {
                                $k_phi = $row['lm_test'] * $lumen_factor;
                            }
                        } catch (Exception $e) {
                            $k_phi = 0;
                        }

                        // Step 6.1: Newton-Raphson Iteration Setup (moved before lumen calculations)
                        // Newton-Raphson iteration variables
                        $target_if = 10.0;        // Starting value in mA
                        $tolerance = 0.0001;       // Convergence criteria
                        $max_iterations = 100;    // Maximum iterations to prevent infinite loops
                        $iteration_count = 0;     // Track iterations
                        $converged = false;       // Convergence status

                        // Step 6.7: Implement Newton-Raphson Iteration Loop (moved before lumen calculations)
                        // Validate prerequisites before starting iteration
                        if ($k_eta > 0 && $k_phi > 0 && isset($row['If_max']) && $row['If_max'] > 0) {
                            try {
                                while ($iteration_count < $max_iterations && !$converged) {
                                    $iteration_count++;

                                    // Calculate objective function and its derivative
                                    $f = calculateObjectiveFunction($target_if, $k_eta, $k_phi, $row);
                                    $f_derivative = calculateObjectiveFunctionDerivative($target_if, $k_eta, $k_phi, $row);

                                    // Check convergence
                                    if (abs($f) < $tolerance) {
                                        $converged = true;
                                        break;
                                    }

                                    // Calculate next iteration
                                    $temp_if = $target_if - ($f / $f_derivative);

                                    // Boundary checking
                                    if ($temp_if < 0 || $temp_if > $row['If_max']) {
                                        $target_if = $target_if + 10; // Increment by 10 mA if out of bounds
                                    } else {
                                        $target_if = $temp_if;
                                    }

                                    // Additional safety check to prevent runaway values
                                    if ($target_if > $row['If_max']) {
                                        $target_if = $row['If_max'];
                                        break;
                                    }
                                }
                            } catch (Exception $e) {
                                $target_if = 50.0; // Reset to default if iteration fails
                                $converged = false;
                            }
                        } else {
                            $target_if = 50.0; // Default value if prerequisites not met
                            $converged = false;
                        }

                        // Step 8.2: Implement Accurate Lumen Calculations (using calculated target_if)
                        $lumen_at_25C_target_if = 0;
                        $lumen_at_target_Tj_target_if = 0;
                        $led_count = 0;

                        try {

                            // Formula 1: Lumen at 25°C and Target Current
                            // lumen@Tj_25C@target_if = lm_test × FIL(target_if)
                            if (isset($row['lm_test']) && $row['lm_test'] > 0) {
                                $fil_at_target_if = calculateFIL($target_if, $row);
                                $lumen_at_25C_target_if = $row['lm_test'] * $fil_at_target_if;

                            }

                            // Formula 2: Lumen at Target Junction Temperature and Target Current
                            // lumen@target_Tj@target_if = lumen@Tj_25C@target_if × FTL(target_Tj)
                            if ($lumen_at_25C_target_if > 0 && isset($_SESSION['junction_temp'])) {
                                $ftl_at_target_tj = $lumen_factor; // This is already calculated FTL(target_Tj)
                                $lumen_at_target_Tj_target_if = $lumen_at_25C_target_if * $ftl_at_target_tj;

                            }

                            // Formula 3: Accurate LED Count
                            // LED_count = target_led_lumen_output ÷ lumen@target_Tj@target_if
                            if ($target_led_lumen > 0 && $lumen_at_target_Tj_target_if > 0) {
                                $led_count = ceil($target_led_lumen / $lumen_at_target_Tj_target_if);
                            }

                        } catch (Exception $e) {
                            $lumen_at_25C_target_if = 0;
                            $lumen_at_target_Tj_target_if = 0;
                            $led_count = 0;
                        }



                        // Add calculated factors to the row data
                        $row['calculated_lumen_factor'] = $lumen_factor;
                        $row['calculated_vf_factor'] = $vf_factor;
                        $row['k_eta'] = $k_eta;
                        $row['k_phi'] = $k_phi;
                        $row['lumen_at_25C_target_if'] = $lumen_at_25C_target_if;
                        $row['lumen_at_target_Tj_target_if'] = $lumen_at_target_Tj_target_if;
                        $row['led_count'] = $led_count;
                        $row['target_if'] = $target_if;
                        $row['iteration_count'] = $iteration_count;
                        $row['converged'] = $converged;

                        $led_candidates[] = $row;
                    }
                    $candidate_count = count($led_candidates);
                    $query_executed = true;

                    // Phase 9: Calculate Series-Parallel Configuration Solutions
                    $led_config_solutions = [];

                    foreach ($led_candidates as $candidate_index => $candidate) {
                        $solutions = [];

                        // Get required values from Phase 8
                        $required_led_count = isset($candidate['led_count']) ? $candidate['led_count'] : 0;
                        $target_if = isset($candidate['target_if']) ? $candidate['target_if'] : 0;
                        $target_tj = isset($_SESSION['junction_temp']) ? $_SESSION['junction_temp'] : 65;
                        $v_chain_max = isset($_SESSION['v_chain_max']) ? $_SESSION['v_chain_max'] : 50;

                        if ($required_led_count > 0 && $target_if > 0) {
                            // Calculate Vf@target_if@target_Tj for this LED with debug info
                            $vf_debug = calculateVfWithDebug($target_if, $target_tj, $candidate);
                            $vf_single = $vf_debug['vf_final'];

                            // Algorithm implementation
                            $P = 1; // Parallel strings
                            $solution_index = 0;
                            $max_parallel = min(20, $required_led_count); // Limit to 20 parallel strings

                            while ($P <= $max_parallel && $solution_index < 10) { // Limit to 10 solutions per candidate
                                $led_count_working = $required_led_count;
                                $led_add = 0;

                                // Find integer series count
                                while (($led_count_working % $P) != 0) {
                                    $led_count_working++;
                                    $led_add++;
                                }

                                $S = $led_count_working / $P; // Series count

                                // Check minimum series constraint
                                if ($S >= 2) {
                                    $V_chain = $S * $vf_single;

                                    // Check voltage constraint
                                    if ($V_chain <= $v_chain_max) {
                                        $solutions[] = [
                                            'P' => $P,
                                            'S' => $S,
                                            'led_add' => $led_add,
                                            'V_chain' => $V_chain,
                                            'total_leds' => $led_count_working,
                                            'vf_single' => $vf_single,
                                            'vf_at_25C' => $vf_debug['vf_at_25C'],
                                            'fiv' => $vf_debug['fiv'],
                                            'ftv' => $vf_debug['ftv'],
                                            'vf_test' => $vf_debug['vf_test']
                                        ];
                                        $solution_index++;
                                    }
                                }

                                $P++;
                            }

                            // Sort solutions by Series Count (S) descending, then Additional LEDs ascending, then Chain Voltage descending
                            usort($solutions, function($a, $b) {
                                // First priority: Series Count (S) descending (highest S values first)
                                if ($a['S'] != $b['S']) {
                                    if ($a['S'] > $b['S']) return -1;
                                    if ($a['S'] < $b['S']) return 1;
                                }

                                // Second priority: Additional LEDs ascending (fewest additional LEDs first)
                                if ($a['led_add'] != $b['led_add']) {
                                    if ($a['led_add'] > $b['led_add']) return 1;
                                    if ($a['led_add'] < $b['led_add']) return -1;
                                }

                                // Third priority: Chain Voltage descending (higher voltage first)
                                if ($b['V_chain'] > $a['V_chain']) return 1;
                                if ($b['V_chain'] < $a['V_chain']) return -1;
                                return 0;
                            });
                        }

                        $led_config_solutions[$candidate_index] = $solutions;
                    }
                }
                $candidate_stmt->close();
            }
            $query_db->close();
        }
    } catch (Exception $e) {
        // Silently handle query errors for now
        $candidate_count = 0;
    }
}

$_SESSION_db->close();
?>

<!DOCTYPE html>
<html>
<head>
    <title>LLE</title>
    <link rel="icon" type="image/png" href="LLE.png">
    <meta charset="UTF-8">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "San Francisco", "Helvetica Neue", Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .status-box {
            padding: 15px;
            margin: 20px 0;
            border-radius: 5px;
            border: 1px solid;
        }
        .success {
            background-color: #d4edda;
            border-color: #c3e6cb;
            color: #155724;
        }
        .error {
            background-color: #f8d7da;
            border-color: #f5c6cb;
            color: #721c24;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 20px 0;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        th {
            background-color: #f2f2f2;
            font-weight: bold;
        }
        .section {
            margin: 30px 0;
            padding: 20px;
            border: 1px solid #ddd;
            border-radius: 5px;
        }
        .section h3 {
            margin-top: 0;
            color: #333;
        }
        .options-list {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin: 10px 0;
        }
        .option-item {
            background-color: #e9ecef;
            padding: 5px 10px;
            border-radius: 3px;
            font-size: 14px;
        }
        .form-container {
            background-color: #f8f9fa;
            padding: 25px;
            border-radius: 8px;
            margin: 20px 0;
            border: 2px solid #007bff;
        }
        .form-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin: 20px 0;
        }
        .form-column {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        .form-row {
            display: flex;
            align-items: center;
            gap: 15px;
        }
        .form-row label {
            min-width: 200px;
            font-weight: bold;
            color: #333;
        }
        .form-row input {
            padding: 8px 12px;
            border: 1px solid #ccc;
            border-radius: 4px;
            font-size: 14px;
            width: 150px;
        }
        .form-row input:focus {
            outline: none;
            border-color: #007bff;
            box-shadow: 0 0 5px rgba(0,123,255,0.3);
        }
        .submit-btn {
            background-color: #007bff;
            color: white;
            padding: 12px 30px;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            margin-top: 20px;
        }
        .submit-btn:hover {
            background-color: #0056b3;
        }
        .validation-error {
            background-color: #f8d7da;
            border: 1px solid #f5c6cb;
            color: #721c24;
            padding: 10px;
            border-radius: 4px;
            margin: 10px 0;
        }
        .success-message {
            background-color: #d1ecf1;
            border: 1px solid #bee5eb;
            color: #0c5460;
            padding: 10px;
            border-radius: 4px;
            margin: 10px 0;
            font-weight: bold;
        }
    </style>
</head>

<body>
<div class="container">
    <h1>LLE Solution Development Tool <span style="font-size: 0.5em; font-weight: normal;">by Ampco KN - Phase 10</span></h1>

    <!-- Phase 2: Parameter Input Form -->
    <div class="form-container">
        <h2 style="margin-top: 0; color: #007bff;">LED Count Calculation Parameters</h2>
        <p>Enter the target specifications for your LED luminaire design:</p>

        <?php if ($form_submitted && !empty($validation_errors)): ?>
            <div class="validation-error">
                <strong>Please correct the following errors:</strong>
                <ul style="margin: 5px 0;">
                    <?php foreach ($validation_errors as $error): ?>
                        <li><?= htmlspecialchars($error) ?></li>
                    <?php endforeach; ?>
                </ul>
            </div>
        <?php endif; ?>

        <?php if (!empty($success_message)): ?>
            <div class="success-message">
                <?= htmlspecialchars($success_message) ?>
            </div>
        <?php endif; ?>

        <form method="POST" action="">
            <div class="form-grid">
                <!-- Left Column: LED Specifications -->
                <div class="form-column">
                    <div class="form-row">
                        <label for="target_cct">Target CCT (Color Temperature):</label>
                        <select id="target_cct"
                                name="target_cct"
                                required
                                style="padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; width: 150px;">
                            <option value="">Select CCT...</option>
                            <?php foreach ($cct_options as $cct): ?>
                                <option value="<?= htmlspecialchars($cct) ?>"
                                        <?= ($target_cct == $cct) ? 'selected' : '' ?>>
                                    <?= htmlspecialchars($cct) ?>K
                                </option>
                            <?php endforeach; ?>
                        </select>
                        <span style="color: #666;">Kelvin (K)</span>
                    </div>

                    <div class="form-row">
                        <label for="target_cri">Target CRI (Color Rendering Index):</label>
                        <select id="target_cri"
                                name="target_cri"
                                required
                                style="padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; width: 150px;">
                            <option value="">Select CRI...</option>
                            <?php foreach ($cri_options as $cri): ?>
                                <option value="<?= htmlspecialchars($cri) ?>"
                                        <?= ($target_cri == $cri) ? 'selected' : '' ?>>
                                    <?= htmlspecialchars($cri) ?>
                                </option>
                            <?php endforeach; ?>
                        </select>
                        <span style="color: #666;">CRI value</span>
                    </div>

                    <div class="form-row">
                        <label for="target_lumen">Target Luminaire Lumen Output:</label>
                        <input type="number"
                               id="target_lumen"
                               name="target_lumen"
                               value="<?= htmlspecialchars($target_lumen) ?>"
                               placeholder="e.g., 5000, 10000, 15000 luminaire lumens"
                               step="1"
                               min="0"
                               required>
                        <span style="color: #666;">lumens (lm)</span>
                    </div>

                    <div class="form-row">
                        <label for="optical_transmission">Luminaire Optical Transmission Rate:</label>
                        <input type="number"
                               id="optical_transmission"
                               name="optical_transmission"
                               value="<?= htmlspecialchars($optical_transmission) ?>"
                               placeholder="Default: 80% (typical lens/reflector efficiency)"
                               step="1"
                               min="1"
                               max="100"
                               required>
                        <span style="color: #666;">percent (%)</span>
                    </div>

                    <div class="form-row">
                        <label for="power_efficiency">Power Supply Efficiency:</label>
                        <input type="number"
                               id="power_efficiency"
                               name="power_efficiency"
                               value="<?= htmlspecialchars($power_efficiency) ?>"
                               placeholder="Default: 85% (typical driver efficiency)"
                               step="0.1"
                               min="1"
                               max="100"
                               required>
                        <span style="color: #666;">percent (%)</span>
                    </div>
                </div>

                <!-- Right Column: Performance & Operating Conditions -->
                <div class="form-column">
                    <div class="form-row">
                        <label for="target_efficacy">Target Luminaire Efficacy:</label>
                        <input type="number"
                               id="target_efficacy"
                               name="target_efficacy"
                               value="<?= htmlspecialchars($target_efficacy) ?>"
                               placeholder="e.g., 100, 150, 200 luminaire lm/W"
                               step="0.1"
                               min="1"
                               required>
                        <span style="color: #666;">lumens per watt (lm/W)</span>
                    </div>

                    <div class="form-row">
                        <label for="junction_temp">Junction Temperature (Tj):</label>
                        <input type="number"
                               id="junction_temp"
                               name="junction_temp"
                               value="<?= htmlspecialchars($junction_temp) ?>"
                               placeholder="Default: 65°C (typical operating temperature)"
                               step="1"
                               min="-40"
                               max="200"
                               required>
                        <span style="color: #666;">degrees Celsius (°C)</span>
                    </div>

                    <div class="form-row">
                        <label for="v_chain_max">Maximum LED Chain Voltage:</label>
                        <input type="number"
                               id="v_chain_max"
                               name="v_chain_max"
                               value="<?= htmlspecialchars($v_chain_max) ?>"
                               placeholder="Default: 50V (maximum voltage per LED string)"
                               step="1"
                               min="1"
                               max="1000"
                               required>
                        <span style="color: #666;">volts (V)</span>
                    </div>

                    <div class="form-row">
                        <label for="smt_cost_rmb">SMT Cost in RMB:</label>
                        <input type="number"
                               id="smt_cost_rmb"
                               name="smt_cost_rmb"
                               value="<?= htmlspecialchars($smt_cost_rmb) ?>"
                               placeholder="Default: 0.01 RMB (SMT assembly cost per LED)"
                               step="0.001"
                               min="0"
                               required>
                        <span style="color: #666;">RMB per LED</span>
                    </div>

                    <div class="form-row">
                        <label for="usd_rate">USD Exchange Rate:</label>
                        <input type="number"
                               id="usd_rate"
                               name="usd_rate"
                               value="<?= htmlspecialchars($usd_rate) ?>"
                               placeholder="Default: 7.00 (RMB per 1 USD)"
                               step="0.01"
                               min="0.01"
                               required>
                        <span style="color: #666;">RMB per USD</span>
                    </div>
                </div>
            </div>

            <button type="submit" name="calculate_params" class="submit-btn">
                Store Parameters & Prepare Calculations
            </button>
        </form>
    </div>

    <!-- Step 4.2: Display LED Candidate Count -->
    <?php if ($query_executed): ?>
        <div class="section">
            <h3>LED Candidate Search Results</h3>
            <?php if ($candidate_count > 0): ?>

                <table style="margin-top: 20px;">
                    <thead>
                        <tr>
                            <th>Model</th>
                            <th>CCT (K)</th>
                            <th>CRI</th>
                            <th>Fixture (lm)</th>
                            <th>Input Power (W)</th>
                            <th>LED Count</th>
                            <th>Lm/LED</th>
                            <th>If (mA)</th>
                            <th>LED + SMT (USD)</th>
                            <th>LED Quote Time</th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php
                        // Use the same cost-based sorting logic as the configuration solutions section
                        if (!empty($led_config_solutions)) {
                            // Create array of candidate indices with their first configuration USD costs for sorting
                            $candidate_costs_for_search = [];
                            foreach ($led_candidates as $candidate_index => $candidate) {
                                if (!empty($led_config_solutions[$candidate_index])) {
                                    $first_solution = $led_config_solutions[$candidate_index][0];
                                    $led_cost_usd = isset($candidate['USD']) && $candidate['USD'] > 0 ? $first_solution['total_leds'] * $candidate['USD'] : 0;
                                    $smt_cost_usd = $first_solution['total_leds'] * $smt_cost_rmb / $usd_rate;
                                    $total_cost_usd = $led_cost_usd + $smt_cost_usd;
                                    $candidate_costs_for_search[] = [
                                        'index' => $candidate_index,
                                        'cost' => $total_cost_usd,
                                        'candidate' => $candidate
                                    ];
                                }
                            }

                            // Sort candidates by USD cost of first configuration (ascending - lowest cost first)
                            usort($candidate_costs_for_search, function($a, $b) {
                                if ($a['cost'] == $b['cost']) return 0;
                                return ($a['cost'] < $b['cost']) ? -1 : 1;
                            });

                            $sorted_candidates = $candidate_costs_for_search;
                        } else {
                            // Fallback to original order if no configuration solutions
                            $sorted_candidates = [];
                            foreach ($led_candidates as $candidate_index => $candidate) {
                                $sorted_candidates[] = [
                                    'index' => $candidate_index,
                                    'candidate' => $candidate
                                ];
                            }
                        }
                        ?>

                        <?php foreach ($sorted_candidates as $candidate_data): ?>
                            <?php
                            $candidate_index = $candidate_data['index'];
                            $candidate = $candidate_data['candidate'];
                            ?>
                            <tr>
                                <td><?= htmlspecialchars(isset($candidate['Model']) ? $candidate['Model'] : 'N/A') ?></td>
                                <td><?= isset($candidate['CCT']) ? number_format($candidate['CCT'], 0) : 'N/A' ?></td>
                                <td><?= isset($candidate['CRI']) ? number_format($candidate['CRI'], 0) : 'N/A' ?></td>
                                <td><?php
                                    // Fixture (lm) = Lm/LED * LED Count * Luminaire Optical Transmission Rate / 100
                                    $fixture_lm = 0;
                                    if (!empty($led_config_solutions[$candidate_index]) && isset($led_config_solutions[$candidate_index][0]['total_leds'])) {
                                        $first_solution = $led_config_solutions[$candidate_index][0];
                                        $lm_per_led = isset($candidate['lumen_at_target_Tj_target_if']) ? $candidate['lumen_at_target_Tj_target_if'] : 0;
                                        $led_count_val = $first_solution['total_leds'];
                                        $optical_rate = isset($_SESSION['optical_transmission']) ? $_SESSION['optical_transmission'] / 100.0 : 0;
                                        if ($lm_per_led > 0 && $led_count_val > 0 && $optical_rate > 0) {
                                            $fixture_lm = $lm_per_led * $led_count_val * $optical_rate;
                                        }
                                    }
                                    echo $fixture_lm > 0 ? number_format($fixture_lm, 0) : 'N/A';
                                ?></td>
                                <td><?php
                                    // Input Power (W) = Fixture (lm) / Target Luminaire Efficacy
                                    $input_power = 0;
                                    if (isset($fixture_lm) && $fixture_lm > 0 && isset($target_efficacy) && $target_efficacy > 0) {
                                        $input_power = $fixture_lm / $target_efficacy;
                                    }
                                    echo $input_power > 0 ? number_format($input_power, 1) : 'N/A';
                                ?></td>
                                <td><?php
                                    // Total LED Count from first configuration
                                    if (!empty($led_config_solutions[$candidate_index]) && isset($led_config_solutions[$candidate_index][0]['total_leds'])) {
                                        echo number_format($led_config_solutions[$candidate_index][0]['total_leds'], 0);
                                    } else {
                                        echo 'N/A';
                                    }
                                ?></td>
                                <td><?= isset($candidate['lumen_at_target_Tj_target_if']) && $candidate['lumen_at_target_Tj_target_if'] > 0 ? number_format($candidate['lumen_at_target_Tj_target_if'], 1) : 'N/A' ?></td>
                                <td><?= isset($candidate['target_if']) ? number_format($candidate['target_if'], 1) : 'N/A' ?></td>
                                <td><?php
                                    // LED + SMT Cost USD from first configuration
                                    if (!empty($led_config_solutions[$candidate_index]) && isset($led_config_solutions[$candidate_index][0]['total_leds'])) {
                                        $first_solution = $led_config_solutions[$candidate_index][0];
                                        $led_cost_usd = isset($candidate['USD']) && $candidate['USD'] > 0 ? $first_solution['total_leds'] * $candidate['USD'] : 0;
                                        $smt_cost_usd = $first_solution['total_leds'] * $smt_cost_rmb / $usd_rate;
                                        $total_cost_usd = $led_cost_usd + $smt_cost_usd;
                                        echo ($led_cost_usd > 0 || $smt_cost_usd > 0) ? number_format($total_cost_usd, 2) : 'N/A';
                                    } else {
                                        echo 'N/A';
                                    }
                                ?></td>
                                <td><?= isset($candidate['Quote']) && !empty($candidate['Quote']) ? htmlspecialchars($candidate['Quote']) : 'N/A' ?></td>
                            </tr>
                        <?php endforeach; ?>
                    </tbody>
                </table>

                <!-- Phase 9: LED Series-Parallel Configuration Solutions -->
                <div class="success-message" style="margin-top: 20px;">
                    Found <?= $candidate_count ?> LED model(s) matching CCT: <?= number_format($_SESSION['target_cct'], 0) ?>K and CRI: <?= number_format($_SESSION['target_cri'], 0) ?>
                </div>

                <!-- Phase 9: LED Series-Parallel Configuration Solutions -->
                <div style="margin-top: 30px;">
                    <h3 style="color: #007bff; margin-bottom: 15px;">LED Series-Parallel Configuration Solutions</h3>
                    <p style="margin-bottom: 15px; color: #666;">
                        Feasible LED configurations considering maximum chain voltage of <?= number_format(isset($_SESSION['v_chain_max']) ? $_SESSION['v_chain_max'] : 50, 0) ?>V.
                        Models are ordered by lowest cost (first configuration), configurations within each model are sorted by series count descending, then by fewest additional LEDs.
                        Each model shows up to 10 configurations with highest voltage utilization first.
                    </p>

                    <?php if (!empty($led_config_solutions)): ?>
                        <?php
                        // Create array of candidate indices with their first configuration USD costs for sorting
                        $candidate_costs = [];
                        foreach ($led_candidates as $candidate_index => $candidate) {
                            if (!empty($led_config_solutions[$candidate_index])) {
                                $first_solution = $led_config_solutions[$candidate_index][0];
                                $led_cost_usd = isset($candidate['USD']) && $candidate['USD'] > 0 ? $first_solution['total_leds'] * $candidate['USD'] : 0;
                                $smt_cost_usd = $first_solution['total_leds'] * $smt_cost_rmb / $usd_rate;
                                $total_cost_usd = $led_cost_usd + $smt_cost_usd;
                                $candidate_costs[] = [
                                    'index' => $candidate_index,
                                    'cost' => $total_cost_usd,
                                    'candidate' => $candidate
                                ];
                            }
                        }

                        // Sort candidates by USD cost of first configuration (ascending - lowest cost first)
                        usort($candidate_costs, function($a, $b) {
                            if ($a['cost'] == $b['cost']) return 0;
                            return ($a['cost'] < $b['cost']) ? -1 : 1;
                        });
                        ?>

                        <?php foreach ($candidate_costs as $candidate_data): ?>
                            <?php
                            $candidate_index = $candidate_data['index'];
                            $candidate = $candidate_data['candidate'];
                            ?>
                            <?php if (!empty($led_config_solutions[$candidate_index])): ?>
                                <div style="margin-bottom: 25px;">
                                    <h4 style="color: #333; margin-bottom: 10px;">
                                        Model: <?= htmlspecialchars($candidate['Model']) ?>
                                        (Required: <?= number_format($candidate['led_count'], 0) ?> LEDs, Vf: <?= number_format($led_config_solutions[$candidate_index][0]['V_chain'], 1) ?>V)
                                    </h4>

                                    <table style="font-size: 14px; margin-bottom: 15px;">
                                        <thead>
                                            <tr>
                                                <th>Configuration</th>
                                                <th>Parallel (P)</th>
                                                <th>Series (S)</th>
                                                <th>Added LEDs</th>
                                                <th>Voltage (V)</th>
                                                <th>Current (mA)</th>
                                                <th>Power (W)</th>
                                                <th>LED + SMT (USD)</th>
                                                <th>LED + SMT (RMB)</th>
                                                <th>Total LED Count</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            <?php foreach (array_slice($led_config_solutions[$candidate_index], 0, 10) as $solution): ?>
                                                <tr <?= $solution['led_add'] == 0 ? 'style="background-color: #d4edda;"' : '' ?>>
                                                    <td><?= $solution['P'] ?>P<?= $solution['S'] ?>S</td>
                                                    <td><?= number_format($solution['P'], 0) ?></td>
                                                    <td><?= number_format($solution['S'], 0) ?></td>
                                                    <td <?= $solution['led_add'] == 0 ? 'style="font-weight: bold; color: #155724;"' : '' ?>>
                                                        <?= number_format($solution['led_add'], 0) ?>
                                                    </td>
                                                    <td><?= number_format($solution['V_chain'], 1) ?></td>
                                                    <td><?php
                                                        // Total Current = Target Current × Parallel Strings
                                                        $total_current = isset($candidate['target_if']) && $candidate['target_if'] > 0 ? $candidate['target_if'] * $solution['P'] : 0;
                                                        echo $total_current > 0 ? number_format($total_current, 1) : 'N/A';
                                                    ?></td>
                                                    <td><?php
                                                        // Power (W) = Voltage (V) × Current (mA) ÷ 1000
                                                        $voltage = isset($solution['V_chain']) ? $solution['V_chain'] : 0;
                                                        $current_ma = isset($candidate['target_if']) && $candidate['target_if'] > 0 ? $candidate['target_if'] * $solution['P'] : 0;
                                                        $power_watts = ($voltage > 0 && $current_ma > 0) ? ($voltage * $current_ma / 1000) : 0;
                                                        echo $power_watts > 0 ? number_format($power_watts, 1) : 'N/A';
                                                    ?></td>
                                                    <td><?php
                                                        // LED + SMT Cost USD = LED Cost (USD) + (Total LED Count × SMT Cost in RMB ÷ USD Exchange Rate)
                                                        $led_cost_usd = isset($candidate['USD']) && $candidate['USD'] > 0 ? $solution['total_leds'] * $candidate['USD'] : 0;
                                                        $smt_cost_usd = $solution['total_leds'] * $smt_cost_rmb / $usd_rate;
                                                        $total_cost_usd = $led_cost_usd + $smt_cost_usd;
                                                        echo ($led_cost_usd > 0 || $smt_cost_usd > 0) ? number_format($total_cost_usd, 2) : 'N/A';
                                                    ?></td>
                                                    <td><?php
                                                        // LED + SMT Cost RMB = LED Cost (RMB) + (Total LED Count × SMT Cost in RMB)
                                                        $led_cost_rmb = isset($candidate['RMB']) && $candidate['RMB'] > 0 ? $solution['total_leds'] * $candidate['RMB'] : 0;
                                                        $smt_cost_rmb_total = $solution['total_leds'] * $smt_cost_rmb;
                                                        $total_cost_rmb = $led_cost_rmb + $smt_cost_rmb_total;
                                                        echo ($led_cost_rmb > 0 || $smt_cost_rmb_total > 0) ? number_format($total_cost_rmb, 2) : 'N/A';
                                                    ?></td>
                                                    <td><?= number_format($solution['total_leds'], 0) ?></td>
                                                </tr>
                                            <?php endforeach; ?>
                                        </tbody>
                                    </table>
                                </div>
                            <?php else: ?>
                                <div style="margin-bottom: 15px; padding: 10px; background-color: #f8d7da; border-radius: 5px; color: #721c24;">
                                    <strong>Model: <?= htmlspecialchars($candidate['Model']) ?></strong><br>
                                    No feasible configurations found within <?= number_format($_SESSION['v_chain_max'], 0) ?>V constraint.
                                </div>
                            <?php endif; ?>
                        <?php endforeach; ?>
                    <?php else: ?>
                        <div style="padding: 15px; background-color: #f8d7da; border-radius: 5px; color: #721c24;">
                            No configuration solutions available. Please ensure LED count calculations are completed.
                        </div>
                    <?php endif; ?>
                </div>

                <!-- Temperature Derating Information -->
                <div style="margin: 15px 0; padding: 10px; background-color: #f8f9fa; border-radius: 5px; border-left: 4px solid #007bff;">
                    <strong>Temperature Derating Information:</strong><br>
                    The Lumen Factor and Vf Factor values shown below are calculated using 6th-order polynomial equations
                    based on the junction temperature of <?= number_format($_SESSION['junction_temp'], 1) ?>°C.
                    These factors represent the performance adjustment from nominal test conditions.
                </div>

                <!-- Newton-Raphson Optimization Information -->
                <div style="margin: 15px 0; padding: 10px; background-color: #fff3cd; border-radius: 5px; border-left: 4px solid #ffc107;">
                    <strong>Newton-Raphson Current Optimization:</strong><br>
                    The Target Current values are calculated using Newton-Raphson iteration to find the optimal operating current
                    that balances your target LED efficacy (<?= number_format($target_led_efficacy, 1) ?> lm/W) and
                    LED lumen output (<?= number_format($target_led_lumen, 0) ?> lm) requirements derived from luminaire specifications.
                </div>

            <?php else: ?>
                <div class="validation-error">
                    No LED models found matching CCT: <?= number_format($_SESSION['target_cct'], 0) ?>K and CRI: <?= number_format($_SESSION['target_cri'], 0) ?>
                    <br>Please try different CCT or CRI values.
                </div>
            <?php endif; ?>
        </div>
    <?php endif; ?>

    <div class="status-box <?= $connection_status === 'Success' ? 'success' : 'error' ?>">
        <strong>Database Connection Status:</strong> <?= $connection_status ?>
        <?php if ($error_message): ?>
            <br><strong>Error:</strong> <?= htmlspecialchars($error_message) ?>
        <?php endif; ?>
    </div>

    <?php if ($connection_status === "Success"): ?>
        <!-- Sample data section removed for cleaner interface -->
    <?php else: ?>

        <div class="section">
            <h3>Database Connection Failed</h3>
            <p>Unable to proceed with LED_CoE table analysis due to database connection issues.</p>
            <p>However, you can still use the parameter input form above to store calculation parameters.</p>
            <p>Please check:</p>
            <ul>
                <li>Database server is running</li>
                <li>LED_CoE table exists in the baltechind_grow database</li>
                <li>Connection credentials are correct</li>
            </ul>
        </div>

    <?php endif; ?>

</div>
</body>
</html>