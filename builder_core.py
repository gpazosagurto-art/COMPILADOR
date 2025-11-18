# builder_core.py
# Lógica de compilación separada: extrae ZIP, crea venv, instala deps, ejecuta PyInstaller, empaqueta artefacto.

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
    onefile: bool = False
    noconsole: bool = False

class BuildSignals(QtCore.QObject):
    log = QtCore.Signal(str)
    progress = QtCore.Signal(int)
    done = QtCore.Signal(bool, str, str)  # ok, artifact_path, error

def _emit(log_cb, msg: str):
    if log_cb:
        log_cb(msg)
    else:
        print(msg, flush=True)

def _run(cmd: list[str], cwd: Path | None, env: dict | None, log_cb) -> int:
    _emit(log_cb, f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        _emit(log_cb, line.rstrip())
    return proc.wait()

def _extract_zip(zip_file: Path, tmp_base: Path, log_cb) -> Path:
    proj_root = tmp_base / "proj"
    proj_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_file) as z:
        for m in z.infolist():
            p = Path(m.filename)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"Ruta peligrosa en ZIP: {m.filename}")
        z.extractall(proj_root)
    _emit(log_cb, f"ZIP extraído en: {proj_root}")
    # detectar raíz
    if (proj_root / "app.py").exists():
        return proj_root
    for p in proj_root.iterdir():
        if p.is_dir() and (p / "app.py").exists():
            return p
    return proj_root

def _create_venv(target_dir: Path, log_cb) -> tuple[Path, list[str]]:
    # Intentar con el intérprete actual si es Python, si no: 'py' o 'python'
    candidates = []
    if sys.executable and sys.executable.lower().endswith("python.exe"):
        candidates.append(sys.executable)
    candidates += ["py", "python"]
    for py in candidates:
        try:
            code = _run([py, "-m", "venv", str(target_dir)], cwd=None, env=None, log_cb=log_cb)
            if code == 0:
                pybin = target_dir / "Scripts" / "python.exe"
                if not pybin.exists():
                    pybin = target_dir / "bin" / "python"
                return pybin, [str(pybin), "-m", "pip"]
        except Exception:
            pass
    raise RuntimeError("No se pudo crear el entorno virtual (¿Python instalado?).")

def build_project_from_zip(
    zip_file: Path,
    tmp_base: Path,
    opts: BuildOptions,
    log_cb=None,
    phase_cb=None,
) -> Path:
    """
    Devuelve la ruta a un ZIP de artefacto (onedir zip o onefile zip).
    Progreso por fases: 5, 20, 40, 70, 90, 100
    """
    phase_cb = phase_cb or (lambda p: None)

    # 1) Extraer
    phase_cb(5)
    proj = _extract_zip(zip_file, tmp_base, log_cb)
    missing = [n for n in ["app.py"] if not (proj / n).exists()]
    if missing:
        raise FileNotFoundError(f"Faltan archivos requeridos: {', '.join(missing)}")

    # 2) venv
    phase_cb(20)
    venv_dir = proj / ".venv_build"
    dist = proj / "dist_out"
    build = proj / "build_out"
    shutil.rmtree(venv_dir, ignore_errors=True)
    shutil.rmtree(dist, ignore_errors=True)
    shutil.rmtree(build, ignore_errors=True)
    pybin, pip_cmd = _create_venv(venv_dir, log_cb)

    # 3) deps
    phase_cb(40)
    _run(pip_cmd + ["install", "--upgrade", "pip", "wheel", "setuptools"], None, None, log_cb)
    _run(pip_cmd + ["install", "pyinstaller"], None, None, log_cb)
    req = proj / "requirements.txt"
    if req.exists():
        _run(pip_cmd + ["install", "-r", str(req)], None, None, log_cb)
    else:
        _emit(log_cb, "No se encontró requirements.txt; continuo con stdlib.")

    # 4) PyInstaller
    phase_cb(70)
    base_cmd = [str(pybin), "-m", "PyInstaller", "--clean", "--noconfirm",
                "--distpath", str(dist), "--workpath", str(build)]
    if opts.onefile:
        base_cmd.append("--onefile")
    if opts.noconsole:
        base_cmd.append("--noconsole")
    if (proj / "icon.ico").exists():
        base_cmd += ["--icon", str(proj / "icon.ico")]

    add_data = proj / "build_add_data.txt"
    if add_data.exists():
        for line in add_data.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and ";" in line:
                base_cmd += ["--add-data", line]

    target = str(proj / "pyinstaller.spec") if (proj / "pyinstaller.spec").exists() else str(proj / "app.py")
    code = _run(base_cmd + [target], cwd=proj, env=None, log_cb=log_cb)
    if code != 0:
        raise RuntimeError("PyInstaller falló; revisa los logs.")

    # 5) Empaquetar artefacto
    phase_cb(90)
    out_zip = proj / "artefacto.zip"
    if (dist / "app").exists():  # onedir
        shutil.make_archive(out_zip.with_suffix("").as_posix(), "zip", root_dir=dist / "app")
        phase_cb(100)
        return out_zip
    onefile_exe = next(dist.glob("*.exe"), None)
    if onefile_exe:
        with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(onefile_exe, arcname=onefile_exe.name)
        phase_cb(100)
        return out_zip

    raise FileNotFoundError("No se encontraron artefactos en dist/.")
