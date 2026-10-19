"""Map the many ways labs print a test name onto one canonical name + LOINC code.

A lookup table is more reliable than asking the model to do this, and it is what
lets values from different labs line up on one trend chart.
"""
import re

# canonical name: (LOINC, category, aliases)
TESTS = {
    "Hemoglobin": ("718-7", "Blood count", ["hemoglobin", "haemoglobin", "hb", "hgb"]),
    "WBC count": ("6690-2", "Blood count", ["wbc", "total leucocyte count", "total leukocyte count", "tlc", "white blood cell count", "white blood cells", "wbc count"]),
    "RBC count": ("789-8", "Blood count", ["rbc", "rbc count", "red blood cell count", "total rbc count"]),
    "Platelet count": ("777-3", "Blood count", ["platelet count", "platelets", "plt"]),
    "Hematocrit": ("4544-3", "Blood count", ["hematocrit", "haematocrit", "pcv", "packed cell volume", "hct"]),
    "MCV": ("787-2", "Blood count", ["mcv", "mean corpuscular volume"]),
    "ESR": ("4537-7", "Blood count", ["esr", "erythrocyte sedimentation rate"]),
    "Glucose (fasting)": ("1558-6", "Diabetes", ["fasting blood sugar", "fbs", "glucose fasting", "fasting glucose", "blood glucose fasting", "fasting plasma glucose", "glucose, fasting"]),
    "Glucose (post-meal)": ("1521-4", "Diabetes", ["post prandial blood sugar", "ppbs", "glucose pp", "postprandial glucose", "glucose post prandial", "glucose, post prandial"]),
    "Glucose (random)": ("2345-7", "Diabetes", ["random blood sugar", "rbs", "glucose random", "glucose"]),
    "HbA1c": ("4548-4", "Diabetes", ["hba1c", "glycated hemoglobin", "glycosylated hemoglobin", "glycated haemoglobin", "a1c", "hemoglobin a1c"]),
    "Total cholesterol": ("2093-3", "Lipids", ["total cholesterol", "cholesterol", "cholesterol total", "serum cholesterol", "cholesterol, total"]),
    "LDL cholesterol": ("13457-7", "Lipids", ["ldl", "ldl cholesterol", "ldl-c", "low density lipoprotein", "ldl cholesterol direct", "cholesterol ldl"]),
    "HDL cholesterol": ("2085-9", "Lipids", ["hdl", "hdl cholesterol", "hdl-c", "high density lipoprotein", "cholesterol hdl"]),
    "Triglycerides": ("2571-8", "Lipids", ["triglycerides", "triglyceride", "tg", "serum triglycerides"]),
    "VLDL cholesterol": ("13458-5", "Lipids", ["vldl", "vldl cholesterol"]),
    "Creatinine": ("2160-0", "Kidney", ["creatinine", "serum creatinine", "s. creatinine", "creatinine serum"]),
    "Urea": ("3091-6", "Kidney", ["urea", "blood urea", "serum urea"]),
    "BUN": ("3094-0", "Kidney", ["bun", "blood urea nitrogen"]),
    "eGFR": ("33914-3", "Kidney", ["egfr", "estimated gfr", "gfr"]),
    "Uric acid": ("3084-1", "Kidney", ["uric acid", "serum uric acid"]),
    "Sodium": ("2951-2", "Electrolytes", ["sodium", "na", "serum sodium", "na+"]),
    "Potassium": ("2823-3", "Electrolytes", ["potassium", "k", "serum potassium", "k+"]),
    "Chloride": ("2075-0", "Electrolytes", ["chloride", "cl", "serum chloride"]),
    "Calcium": ("17861-6", "Electrolytes", ["calcium", "serum calcium", "total calcium"]),
    "ALT (SGPT)": ("1742-6", "Liver", ["alt", "sgpt", "alt (sgpt)", "sgpt (alt)", "alanine aminotransferase", "alanine transaminase", "alanine transaminase (sgpt)"]),
    "AST (SGOT)": ("1920-8", "Liver", ["ast", "sgot", "ast (sgot)", "sgot (ast)", "aspartate aminotransferase", "aspartate transaminase", "aspartate transaminase (sgot)"]),
    "Alkaline phosphatase": ("6768-6", "Liver", ["alkaline phosphatase", "alp", "alk phos"]),
    "Total bilirubin": ("1975-2", "Liver", ["total bilirubin", "bilirubin total", "bilirubin, total", "bilirubin"]),
    "Direct bilirubin": ("1968-7", "Liver", ["direct bilirubin", "bilirubin direct", "bilirubin, direct", "conjugated bilirubin"]),
    "Indirect bilirubin": ("1971-1", "Liver", ["indirect bilirubin", "bilirubin indirect", "bilirubin, indirect", "unconjugated bilirubin"]),
    "GGT": ("2324-2", "Liver", ["ggt", "gamma gt", "ggtp", "gamma glutamyl transferase", "gamma-glutamyl transferase", "gamma glutamyl transpeptidase"]),
    "Total protein": ("2885-2", "Liver", ["total protein", "protein total", "serum protein", "total proteins"]),
    "Globulin": ("10834-0", "Liver", ["globulin", "serum globulin"]),
    "A/G ratio": ("1759-0", "Liver", ["a/g ratio", "a g ratio", "ag ratio", "albumin globulin ratio", "albumin/globulin ratio"]),
    "Albumin": ("1751-7", "Liver", ["albumin", "serum albumin"]),
    "TSH": ("3016-3", "Thyroid", ["tsh", "thyroid stimulating hormone", "tsh ultrasensitive", "tsh (ultrasensitive)"]),
    "T3": ("3053-6", "Thyroid", ["t3", "total t3", "triiodothyronine", "t3 total"]),
    "T4": ("3026-2", "Thyroid", ["t4", "total t4", "thyroxine", "t4 total"]),
    "Free T4": ("3024-7", "Thyroid", ["free t4", "ft4"]),
    "Vitamin D": ("1989-3", "Vitamins", ["vitamin d", "25-oh vitamin d", "25 hydroxy vitamin d", "vitamin d total", "vitamin d3", "25(oh) vitamin d", "vitamin d (25-oh)", "total vitamin d", "vitamin d, total", "total 25-oh vitamin d", "25-hydroxy vitamin d"]),
    "Vitamin B12": ("2132-9", "Vitamins", ["vitamin b12", "b12", "cobalamin", "vit b12"]),
    "Ferritin": ("2276-4", "Vitamins", ["ferritin", "serum ferritin"]),
    "Iron": ("2498-4", "Vitamins", ["iron", "serum iron"]),
    "CRP": ("1988-5", "Inflammation", ["crp", "c-reactive protein", "c reactive protein", "hs-crp", "hscrp"]),
    "PSA": ("2857-1", "Other", ["psa", "prostate specific antigen", "total psa"]),
}

def _clean(s: str) -> str:
    s = s.lower().strip()
    # "ALANINE TRANSAMINASE.(SGPT)", "GGT.", "A : G RATIO", "S. Creatinine"
    s = re.sub(r"[\*:.]+", " ", s)
    s = re.sub(r"\s*\(\s*", " (", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .-")


_ALIAS = {}
for _name, (_loinc, _cat, _aliases) in TESTS.items():
    _ALIAS[_clean(_name)] = _name
    for _a in _aliases:
        _ALIAS[_clean(_a)] = _name


def canonical(test: str) -> tuple[str | None, str | None, str]:
    """Return (canonical name, LOINC, category) or (None, None, 'Other')."""
    key = _clean(test)
    name = _ALIAS.get(key)
    if not name:
        # drop method/sample hints like "(Serum)" or ", EDTA" and try again
        stripped = _clean(re.sub(r"\(.*?\)|,.*$|\bserum\b|\bplasma\b|\bblood\b", " ", key))
        name = _ALIAS.get(stripped)
    if not name:
        return None, None, "Other"
    loinc, cat, _ = TESTS[name]
    return name, loinc, cat


def category(canonical_name: str | None) -> str:
    if canonical_name and canonical_name in TESTS:
        return TESTS[canonical_name][1]
    return "Other"


def parse_number(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    # commas are thousands separators on lab reports ("1,50,000" platelets)
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group(0)) if m else None


def parse_range(text: str | None) -> tuple[float | None, float | None]:
    """'13.0 - 17.0' -> (13, 17); '< 5.7' -> (None, 5.7); '> 40' -> (40, None)."""
    if not text:
        return None, None
    # labelled bands ("Deficient:<20; Sufficient: 30-100"): use the normal one
    band = re.search(r"\b(?:normal|sufficient|desirable|optimal|adequate|reference|non[- ]?diabetic)\b\s*:?\s*([^;\n]*\d[^;\n]*)", str(text), re.I)
    if band and re.search(r"[A-Za-z]+\s*:", str(text)):
        text = band.group(1)
    t = str(text).replace("–", "-").replace("—", "-").replace(" to ", " - ")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(<=?|≤|up\s*to|upto|less than)\s*(\d+(?:\.\d+)?)", t, re.I)
    if m:
        return None, float(m.group(2))
    m = re.search(r"(>=?|≥|more than|greater than)\s*(\d+(?:\.\d+)?)", t, re.I)
    if m:
        return float(m.group(2)), None
    return None, None


def flag_for(value: float | None, low: float | None, high: float | None) -> str | None:
    if value is None or (low is None and high is None):
        return None
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "normal"
