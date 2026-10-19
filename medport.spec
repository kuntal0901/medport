# PyInstaller recipe for the Windows download:  py -m PyInstaller medport.spec --noconfirm
# Produces dist/MedPort/MedPort.exe plus its files (one folder starts faster than one file and
# antivirus tools flag it less often).
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("uvicorn") + ["multipart", "python_multipart", "qrcode.image.svg", "pymupdf"]

a = Analysis(
    ["run_medport.py"],
    pathex=["."],
    datas=[("medport/templates", "medport/templates"), ("medport/static", "medport/static"),
           ("samples/*.pdf", "samples")],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "reportlab"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MedPort", console=True,
          icon="build/medport.ico", version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="MedPort")
