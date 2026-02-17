# algorithm_core.py
# Pure core math/physics/solver helpers (no Flask, no DB, no cost sorting)

import math


def _num(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _isset(row, key):
    return key in row and row[key] is not None


def _poly6_value(x_value, row, prefix):
    value = 0
    value += row[f"{prefix}_0"] if _isset(row, f"{prefix}_0") else 0
    value += (row[f"{prefix}_1"] if _isset(row, f"{prefix}_1") else 0) * x_value
    value += (row[f"{prefix}_2"] if _isset(row, f"{prefix}_2") else 0) * pow(x_value, 2)
    value += (row[f"{prefix}_3"] if _isset(row, f"{prefix}_3") else 0) * pow(x_value, 3)
    value += (row[f"{prefix}_4"] if _isset(row, f"{prefix}_4") else 0) * pow(x_value, 4)
    value += (row[f"{prefix}_5"] if _isset(row, f"{prefix}_5") else 0) * pow(x_value, 5)
    value += (row[f"{prefix}_6"] if _isset(row, f"{prefix}_6") else 0) * pow(x_value, 6)
    return value


def _poly6_derivative(x_value, row, prefix):
    derivative = 0
    derivative += row[f"{prefix}_1"] if _isset(row, f"{prefix}_1") else 0
    derivative += (row[f"{prefix}_2"] if _isset(row, f"{prefix}_2") else 0) * 2 * x_value
    derivative += (row[f"{prefix}_3"] if _isset(row, f"{prefix}_3") else 0) * 3 * pow(x_value, 2)
    derivative += (row[f"{prefix}_4"] if _isset(row, f"{prefix}_4") else 0) * 4 * pow(x_value, 3)
    derivative += (row[f"{prefix}_5"] if _isset(row, f"{prefix}_5") else 0) * 5 * pow(x_value, 4)
    derivative += (row[f"{prefix}_6"] if _isset(row, f"{prefix}_6") else 0) * 6 * pow(x_value, 5)
    return derivative


def calculateFIV(if_value, row):
    try:
        fiv = _poly6_value(if_value, row, "FIV")
        return _num(fiv, 1.0)
    except Exception:
        return 1.0


def calculateFIVDerivative(if_value, row):
    try:
        fiv_derivative = _poly6_derivative(if_value, row, "FIV")
        return _num(fiv_derivative, 0.0)
    except Exception:
        return 0.0


def calculateFIL(if_value, row):
    try:
        fil = _poly6_value(if_value, row, "FIL")
        fil = _num(fil, 0.0)
        if fil == 0:
            return 1.0
        return fil
    except Exception:
        return 1.0


def calculateFILDerivative(if_value, row):
    try:
        fil_derivative = _poly6_derivative(if_value, row, "FIL")
        return _num(fil_derivative, 0.0)
    except Exception:
        return 0.0


def calculateObjectiveFunction(if_value, k_eta, k_phi, row):
    try:
        fiv = calculateFIV(if_value, row)
        fil = calculateFIL(if_value, row)
        f = k_eta * (if_value / 1000.0) * fiv - k_phi * fil
        return _num(f, 0.0)
    except Exception:
        return 0.0


def calculateObjectiveFunctionDerivative(if_value, k_eta, k_phi, row):
    try:
        fiv = calculateFIV(if_value, row)
        fiv_derivative = calculateFIVDerivative(if_value, row)
        fil_derivative = calculateFILDerivative(if_value, row)
        f_derivative = (
            k_eta * (fiv / 1000.0 + (if_value / 1000.0) * fiv_derivative) - k_phi * fil_derivative
        )
        if abs(f_derivative) < 1e-10:
            return 1e-10
        return _num(f_derivative, 1e-10)
    except Exception:
        return 1e-10


def calculateVfWithDebug(target_if, target_tj, row):
    try:
        vf_at_25C = calculateFIV(target_if, row)
        vf_factor = _poly6_value(target_tj, row, "FTV")
        vf_factor = _num(vf_factor, 0.0)
        vf_final = vf_at_25C * vf_factor
        return {
            "vf_final": _num(vf_final, 3.0),
            "vf_at_25C": _num(vf_at_25C, 3.0),
            "fiv": _num(vf_at_25C, 3.0),
            "ftv": _num(vf_factor, 1.0),
            "vf_test": "N/A",
        }
    except Exception:
        return {
            "vf_final": 3.0,
            "vf_at_25C": 3.0,
            "fiv": 3.0,
            "ftv": 1.0,
            "vf_test": "N/A",
        }
