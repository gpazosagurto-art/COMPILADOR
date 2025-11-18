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
    # FIX: si hay callback (lambda), no pasar flush
    if log_cb:
        log_cb(msg)
    else:
        print(msg, flush=True)


def _run(cmd: list[str], cwd: Path | None, env: dict | None, log_cb) -> int:
    _emit(log_cb, f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
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


# ===================== IMPLEMENTACIÓN ROBUSTA DE venv =====================

def _create_venv(target_dir: Path, log_cb) -> tuple[Path, list[str]]:
    """
    Busca un intérprete de Python 3.10–3.12 y crea un venv.
    Orden de búsqueda (Windows):
      1) py -0p (lista rutas)
      2) py -3.12/-3.11/-3.10
      3) where python / where py
      4) rutas típicas (AppData/Program Files)
    En otros OS: which python3.12/3.11/3.10/python3/python.
    Devuelve: (ruta_python_del_venv, comando_pip_del_venv)
    """
    import re
    from shutil import which

    def _try_probe(cmd: list[str]) -> bool:
        _emit(log_cb, f"Probando intérprete: {' '.join(cmd)}")
        try:
            code = _run(cmd + ["-c", "import sys; print(sys.version)"], None, None, log_cb)
            return code == 0
        except Exception:
            return False

    candidates: list[list[str]] = []

    # 1) py -0p
    if which("py"):
        try:
            out = subprocess.check_output(["py", "-0p"], text=True, stderr=subprocess.STDOUT)
            for line in out.splitlines():
                m = re.search(r"(\d\.\d+).*(python\.exe)", line, re.I)
                if m:
                    ver = m.group(1)
                    if ver in {"3.10", "3.11", "3.12"}:
                        path = line.strip().split()[-1]
                        candidates.append([path])
        except Exception:
            pass
        # 2) py -3.x
        for v in ("3.12", "3.11", "3.10"):
            candidates.append(["py", f"-{v}"])

    # 3) where/which
    if os.name == "nt":
        for exe in ("python", "py"):
            try:
                out = subprocess.check_output(["where", exe], text=True, stderr=subprocess.STDOUT)
                for line in out.splitlines():
                    p = line.strip()
                    if p.lower().endswith("python.exe"):
                        candidates.append([p])
                    elif p.lower().endswith("py.exe"):
                        for v in ("3.12", "3.11", "3.10"):
                            candidates.append([p, f"-{v}"])
            except Exception:
                pass
    else:
        for exe in ("python3.12", "python3.11", "python3.10", "python3", "python"):
            if which(exe):
                candidates.append([exe])

    # 4) rutas típicas Windows
    if os.name == "nt":
        probable = []
        for base in (
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
        ):
            if not base:
                continue
            probable += [
                Path(base) / "Programs" / "Python" / "Python312" / "python.exe",
                Path(base) / "Programs" / "Python" / "Python311" / "python.exe",
                Path(base) / "Programs" / "Python" / "Python310" / "python.exe",
                Path(base) / "Python312" / "python.exe",
                Path(base) / "Python311" / "python.exe",
                Path(base) / "Python310" / "python.exe",
            ]
        for p in probable:
            if p.exists():
                candidates.append([str(p)])

    # Añadir sys.executable si es Python real (útil en dev)
    if sys.executable and os.path.basename(sys.executable).lower().startswith("python"):
        candidates.insert(0, [sys.executable])

    # de-dup preservando orden
    seen = set()
    uniq: list[list[str]] = []
    for c in candidates:
        k = tuple(c)
        if k not in seen:
            seen.add(k)
            uniq.append(c)

    for interp in uniq:
        if not _try_probe(interp):
            continue
        _emit(log_cb, f"Usando intérprete: {' '.join(interp)}")
        code = _run(interp + ["-m", "venv", str(target_dir)], None, None, log_cb)
        if code == 0:
            pybin = target_dir / "Scripts" / "python.exe"
            if not pybin.exists():
                pybin = target_dir / "bin" / "python"
            return pybin, [str(pybin), "-m", "pip"]

    _emit(log_cb, "No se encontró Python 3.10–3.12 en el sistema o no está en PATH/launcher.")
    _emit(log_cb, "Instala Python desde https://www.python.org/downloads/windows/ con:")
    _emit(log_cb, " - Add Python to PATH (checkbox)")
    _emit(log_cb, " - Instalar 'py launcher for all users'")
    raise RuntimeError("No se pudo crear el entorno virtual (¿Python instalado y accesible?).")

# =================== FIN IMPLEMENTACIÓN venv ===================


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

    base = [
        str(pybin),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist),
        "--workpath",
        str(build),
    ]
    # Siempre ONEDIR (no añadimos --onefile)
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

    target = (
        str(proj / "pyinstaller.spec")
        if (proj / "pyinstaller.spec").exists()
        else str(proj / "app.py")
    )
    code = _run(base + [target], cwd=proj, env=None, log_cb=log_cb)
    if code != 0:
        raise RuntimeError("PyInstaller falló; revisa los logs.")

    out_dir = dist / "app"  # onedir estándar
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

