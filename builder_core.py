# builder_core.py
# Lógica de compilación: ZIP o CARPETA → venv → pip → PyInstaller (ONEDIR) → copiar/zip a carpeta destino.

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import zipfile
import subprocess
import sys
import shutil
import os
import tempfile

from PySide6 import QtCore

@dataclass
class BuildOptions:
    noconsole: bool = False
    icon_path: str | None = None  # .ico opcional

class BuildSignals(QtCore.QObject):
    log = QtCore.Signal(str)
    progress = QtCore.Signal(int)
    done = QtCore.Signal(bool, str, str)  # ok, out_dir_or_zip, error

def _emit(log_cb, msg: str):
    (log_cb or print)(msg, flush=True)

def _run(cmd: list[str], cwd: Path | None, env: dict | None, log_cb) -> int:
    _emit(log_cb, f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    for line in proc.stdout:
        _emit(log_cb, line.rstrip())
    return proc.wait()

def _safe_unzip(zip_file: Path, dest: Path):
    with zipfile.ZipFile(zip_file) as z:
        for m in z.infolist():
            p = Path(m.filename)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"Ruta peligrosa en ZIP: {m.filename}")
        z.extractall(dest)

def _detect_root_with_app_py(root: Path) -> Path:
    if (root / "app.py").exists():
        return root
    for p in root.iterdir():
        if p.is_dir() and (p / "app.py").exists():
            return p
    return root

def _create_venv(target_dir: Path, log_cb) -> tuple[Path, list[str]]:
    candidates = []
    if sys.executable and sys.executable.lower().endswith("python.exe"):
        candidates.append(sys.executable)
    candidates += ["py", "python"]
    for py in candidates:
        try:
            code = _run([py, "-m", "venv", str(target_dir)], None, None, log_cb)
            if code == 0:
                pybin = target_dir / "Scripts" / "python.exe"
                if not pybin.exists():
                    pybin = target_dir / "bin" / "python"
                return pybin, [str(pybin), "-m", "pip"]
        except Exception:
            pass
    raise RuntimeError("No se pudo crear el entorno virtual (¿Python instalado?).")

def _pyinstaller_onedir(
    proj: Path, pybin: Path, pip_cmd: list[str], opts: BuildOptions, log_cb
) -> Path:
    dist = proj / "dist_out"
    build = proj / "build_out"
    shutil.rmtree(dist, ignore_errors=True)
    shutil.rmtree(build, ignore_errors=True)

    _run(pip_cmd + ["install", "--upgrade", "pip", "wheel", "setuptools"], None, None, log_cb)
    _run(pip_cmd + ["install", "pyinstaller"], None, None, log_cb)

    req = proj / "requirements.txt"
    if req.exists():
        _run(pip_cmd + ["install", "-r", str(req)], None, None, log_cb)
    else:
        _emit(log_cb, "Aviso: no hay requirements.txt; continuo con stdlib.")

    base = [str(pybin), "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(dist), "--workpath", str(build)]
    # Siempre ONEDIR
    # (No añadimos --onefile)
    if opts.noconsole:
        base.append("--noconsole")
    if opts.icon_path:
        base += ["--icon", opts.icon_path]

    add_data = proj / "build_add_data.txt"
    if add_data.exists():
        for line in add_data.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and ";" in line:
                base += ["--add-data", line]

    target = str(proj / "pyinstaller.spec") if (proj / "pyinstaller.spec").exists() else str(proj / "app.py")
    code = _run(base + [target], cwd=proj, env=None, log_cb=log_cb)
    if code != 0:
        raise RuntimeError("PyInstaller falló; revisa los logs.")

    # onedir típico: dist/app/
    out_dir = dist / "app"
    if not out_dir.exists():
        raise FileNotFoundError("No se encontró carpeta onedir en dist/app")
    return out_dir

def _copy_and_zip_onedir(onedir: Path, dest_parent: Path, base_name: str, log_cb) -> Path:
    out_dir = dest_parent / f"{base_name}_onedir"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    shutil.copytree(onedir, out_dir)

    out_zip = dest_parent / f"{base_name}_onedir.zip"
    if out_zip.exists():
        out_zip.unlink()
    shutil.make_archive(out_zip.with_suffix("").as_posix(), "zip", root_dir=out_dir)
    _emit(log_cb, f"Salida onedir: {out_dir}")
    _emit(log_cb, f"Zip: {out_zip}")
    return out_dir  # devolvemos carpeta principal

# ----------------- API PÚBLICA -----------------

def build_from_zip(zip_path: Path, opts: BuildOptions, log_cb=None, phase_cb=None) -> Path:
    """Compila desde un ZIP. Guarda onedir/zip en la MISMA carpeta del ZIP."""
    phase = phase_cb or (lambda p: None)
    phase(5)
    tmp_root = Path(tempfile.mkdtemp(prefix="compile_zip_"))
    try:
        proj_root = tmp_root / "proj"
        proj_root.mkdir(parents=True, exist_ok=True)
        _safe_unzip(zip_path, proj_root)
        src = _detect_root_with_app_py(proj_root)
        if not (src / "app.py").exists():
            raise FileNotFoundError("Falta app.py en el proyecto.")

        phase(25)
        venv_dir = src / ".venv_build"
        shutil.rmtree(venv_dir, ignore_errors=True)
        pybin, pip_cmd = _create_venv(venv_dir, log_cb)

        phase(60)
        onedir = _pyinstaller_onedir(src, pybin, pip_cmd, opts, log_cb)

        phase(85)
        # Guardar en carpeta del ZIP
        dest_parent = zip_path.parent
        base_name = zip_path.stem
        out_dir = _copy_and_zip_onedir(onedir, dest_parent, base_name, log_cb)

        phase(100)
        return out_dir
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

def build_from_dir(proj_dir: Path, opts: BuildOptions, log_cb=None, phase_cb=None) -> Path:
    """Compila desde una CARPETA. Guarda onedir/zip en ESA MISMA carpeta."""
    phase = phase_cb or (lambda p: None)
    phase(10)

    src = _detect_root_with_app_py(proj_dir)
    if not (src / "app.py").exists():
        raise FileNotFoundError("Falta app.py en la carpeta.")

    phase(30)
    venv_dir = src / ".venv_build"
    shutil.rmtree(venv_dir, ignore_errors=True)
    pybin, pip_cmd = _create_venv(venv_dir, log_cb)

    phase(70)
    onedir = _pyinstaller_onedir(src, pybin, pip_cmd, opts, log_cb)

    phase(90)
    dest_parent = proj_dir
    base_name = proj_dir.name
    out_dir = _copy_and_zip_onedir(onedir, dest_parent, base_name, log_cb)

    phase(100)
    return out_dir
