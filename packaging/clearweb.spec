# PyInstaller spec: onedir build of ClearWeb for Windows 11.
# usage: pyinstaller packaging/clearweb.spec --noconfirm   (run from the repository root; see packaging/build.ps1)
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
PKG = os.path.join(ROOT, "clearweb")

datas = [
    (os.path.join(PKG, "collector.js"), "clearweb"),
    (os.path.join(PKG, "selector.js"), "clearweb"),
    (os.path.join(PKG, "rules"), os.path.join("clearweb", "rules")),
    (os.path.join(PKG, "web"), os.path.join("clearweb", "web")),
]

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("winrt")          # Windows OCR bindings are imported lazily
    + ["clearweb.server", "clearweb.scanner", "clearweb.ocr", "clearweb.crawl", "clearweb.detect",
       "clearweb.textnorm", "clearweb.result_writer", "clearweb.urlnorm", "clearweb.paths", "PIL.Image"]
)

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "testsites", "tools", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ClearWeb", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ClearWeb", upx=False)
