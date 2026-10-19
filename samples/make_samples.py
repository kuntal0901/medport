"""Generate fictional sample reports for trying the app. No real patient data.

    python samples/make_samples.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent
PATIENT = "Asha Verma (fictional)"


def lab_report(fname, lab, date, rows):
    c = canvas.Canvas(str(OUT / fname), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, h - 50, lab)
    c.setFont("Helvetica", 9)
    c.drawString(40, h - 66, "NABL accredited laboratory  |  Sample report for testing")
    c.setFont("Helvetica", 10)
    c.drawString(40, h - 95, f"Patient Name: {PATIENT}")
    c.drawString(330, h - 95, "Age/Sex: 62 Y / F")
    c.drawString(40, h - 110, "Date of Birth: 14/03/1963")
    c.drawString(330, h - 110, f"Sample Collected: {date}")
    c.drawString(40, h - 125, "Ref. By: Dr. R. Iyer")
    c.drawString(330, h - 125, f"Reported: {date}")
    y = h - 160
    c.setFont("Helvetica-Bold", 10)
    for x, t in ((40, "Test"), (250, "Result"), (330, "Unit"), (420, "Biological Ref. Interval")):
        c.drawString(x, y, t)
    c.line(40, y - 4, 555, y - 4)
    y -= 20
    for row in rows:
        if isinstance(row, str):
            c.setFont("Helvetica-Bold", 10)
            c.drawString(40, y, row)
            y -= 16
            continue
        test, val, unit, rng = row
        c.setFont("Helvetica", 10)
        c.drawString(40, y, test)
        c.drawString(250, y, val)
        c.drawString(330, y, unit)
        c.drawString(420, y, rng)
        y -= 16
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(40, 40, "*** End of report ***  Results relate only to the sample tested.")
    c.save()


def discharge(fname):
    c = canvas.Canvas(str(OUT / fname), pagesize=A4)
    w, h = A4
    lines = [
        ("Helvetica-Bold", 14, "City General Hospital - Discharge Summary"),
        ("Helvetica", 10, f"Patient: {PATIENT}        UHID: CGH-000123"),
        ("Helvetica", 10, "Date of Admission: 02/02/2025        Date of Discharge: 06/02/2025"),
        ("Helvetica", 10, ""),
        ("Helvetica", 10, "Final Diagnosis: Type 2 diabetes mellitus; Hypertension; Community acquired pneumonia"),
        ("Helvetica", 10, ""),
        ("Helvetica", 10, "Allergies: Penicillin, Sulfa drugs"),
        ("Helvetica", 10, ""),
        ("Helvetica", 10, "History: Known case of diabetes for 8 years, hypertension for 5 years."),
        ("Helvetica", 10, "Admitted with fever and cough for 4 days. Chest X-ray showed right lower zone consolidation."),
        ("Helvetica", 10, ""),
        ("Helvetica-Bold", 10, "Discharge Medications:"),
        ("Helvetica", 10, "1. Tab. Metformin 500 mg twice daily after meals"),
        ("Helvetica", 10, "2. Tab. Telmisartan 40 mg once daily"),
        ("Helvetica", 10, "3. Tab. Azithromycin 500 mg once daily for 3 days"),
        ("Helvetica", 10, "4. Tab. Atorvastatin 10 mg at night"),
        ("Helvetica", 10, ""),
        ("Helvetica", 10, "Follow up with HbA1c and lipid profile after 3 months."),
    ]
    y = h - 50
    for font, size, text in lines:
        c.setFont(font, size)
        c.drawString(40, y, text)
        y -= 18
    c.save()


if __name__ == "__main__":
    lab_report("2024-08-lab.pdf", "Sunrise Diagnostics", "12/08/2024", [
        "DIABETES",
        ("Fasting Blood Sugar", "142", "mg/dL", "70 - 100"),
        ("HbA1c", "7.9", "%", "4.0 - 5.6"),
        "LIPID PROFILE",
        ("Total Cholesterol", "228", "mg/dL", "< 200"),
        ("LDL Cholesterol", "151", "mg/dL", "< 100"),
        ("HDL Cholesterol", "41", "mg/dL", "> 40"),
        ("Triglycerides", "182", "mg/dL", "< 150"),
        "KIDNEY",
        ("Serum Creatinine", "0.9", "mg/dL", "0.6 - 1.1"),
        ("TSH", "3.2", "µIU/mL", "0.4 - 4.0"),
    ])
    lab_report("2025-01-lab.pdf", "Sunrise Diagnostics", "20/01/2025", [
        ("Haemoglobin", "11.2", "g/dL", "12.0 - 15.0"),
        ("Total Leucocyte Count", "7,800", "/cumm", "4000 - 11000"),
        ("Platelet Count", "2,10,000", "/cumm", "150000 - 410000"),
        ("Fasting Blood Sugar", "131", "mg/dL", "70 - 100"),
        ("HbA1c", "7.4", "%", "4.0 - 5.6"),
        ("Serum Creatinine", "1.0", "mg/dL", "0.6 - 1.1"),
        ("Vitamin D (25-OH)", "18.5", "ng/mL", "30 - 100"),
    ])
    lab_report("2025-05-lab.pdf", "MetroPath Labs", "15/05/2025", [
        ("Glucose, Fasting", "118", "mg/dL", "70 - 100"),
        ("Glycated Hemoglobin", "6.8", "%", "4.0 - 5.6"),
        ("Cholesterol, Total", "184", "mg/dL", "< 200"),
        ("LDL-C", "102", "mg/dL", "< 100"),
        ("HDL-C", "46", "mg/dL", "> 40"),
        ("Triglycerides", "141", "mg/dL", "< 150"),
        ("Creatinine", "1.2", "mg/dL", "0.6 - 1.1"),
        ("eGFR", "52", "mL/min/1.73m2", "> 60"),
        ("SGPT (ALT)", "28", "U/L", "7 - 35"),
    ])
    discharge("2025-02-discharge.pdf")
    print("wrote samples to", OUT)
