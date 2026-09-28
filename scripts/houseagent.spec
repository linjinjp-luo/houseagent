# PyInstaller spec - builds dist\HouseAgent\HouseAgent.exe (one-folder, no console window).
# Run via scripts\build.ps1, which builds the frontend first.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
BACKEND = ROOT / "backend"

hidden = (
    collect_submodules("houseagent")
    + collect_submodules("uvicorn")
    + ["anthropic", "openai", "pystray._win32", "tzdata", "multipart", "python_multipart"]
)

a = Analysis(
    [str(BACKEND / "houseagent" / "__main__.py")],
    pathex=[str(BACKEND)],
    datas=[
        (str(BACKEND / "alembic"), "alembic"),
        (str(ROOT / "frontend" / "dist"), "frontend_dist"),
    ],
    hiddenimports=hidden,
    excludes=["pytest", "mypy", "ruff", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HouseAgent", console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="HouseAgent")
