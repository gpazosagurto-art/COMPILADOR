# main.py
# Compilador GUI: seleccionas un ZIP con un proyecto Python y genera un .exe con PyInstaller.
# PySide6 + QDarkStyle, barra de progreso, logs en vivo y footer centrado.

from pathlib import Path
import sys
import tempfile
import shutil
from PySide6 import QtCore, QtGui, QtWidgets
import qdarkstyle

from builder_core import (
    build_project_from_zip,
    BuildOptions,
    BuildSignals,
)

APP_TITLE = "🧱 Compilador .exe (PyInstaller)"
FOOTER_TEXT = "© 2025 Gabriel Golker"

class BuildWorker(QtCore.QThread):
    def __init__(self, zip_path: Path, opts: BuildOptions, parent=None):
        super().__init__(parent)
        self.zip_path = zip_path
        self.opts = opts
        self.signals = BuildSignals()
        self._tmp_root = None

    def run(self):
        # Fases para la barra de progreso
        try:
            self._tmp_root = Path(tempfile.mkdtemp(prefix="compilegui_"))
            self.signals.progress.emit(1)
            out_zip = build_project_from_zip(
                zip_file=self.zip_path,
                tmp_base=self._tmp_root,
                opts=self.opts,
                log_cb=lambda t: self.signals.log.emit(t),
                phase_cb=lambda p: self.signals.progress.emit(p),
            )
            self.signals.done.emit(True, str(out_zip), "")
        except Exception as e:
            self.signals.done.emit(False, "", str(e))
        finally:
            if self._tmp_root:
                shutil.rmtree(self._tmp_root, ignore_errors=True)

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QtGui.QIcon("icon.ico") if Path("icon.ico").exists() else self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon))
        self.resize(920, 640)

        # Widgets
        self.zip_label = QtWidgets.QLabel("ZIP del proyecto: (no seleccionado)")
        self.zip_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.pick_btn = QtWidgets.QPushButton("Seleccionar ZIP…")
        self.onefile_chk = QtWidgets.QCheckBox("Empaquetar en un solo archivo (--onefile)")
        self.noconsole_chk = QtWidgets.QCheckBox("Ocultar consola (--noconsole)")
        self.start_btn = QtWidgets.QPushButton("Generar .exe")
        self.start_btn.setEnabled(False)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.log.setFont(font)

        self.save_btn = QtWidgets.QPushButton("Guardar artefacto…")
        self.save_btn.setEnabled(False)

        self.footer = QtWidgets.QLabel(FOOTER_TEXT)
        self.footer.setAlignment(QtCore.Qt.AlignHCenter)
        footer_font = self.footer.font()
        footer_font.setPointSize(9)
        self.footer.setFont(footer_font)

        # Layout
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.pick_btn)
        top_row.addWidget(self.onefile_chk)
        top_row.addWidget(self.noconsole_chk)
        top_row.addStretch(1)
        top_row.addWidget(self.start_btn)

        central = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(central)
        v.addWidget(QtWidgets.QLabel(APP_TITLE))
        v.addWidget(self.zip_label)
        v.addLayout(top_row)
        v.addWidget(QtWidgets.QLabel("Logs"))
        v.addWidget(self.log, stretch=1)
        v.addWidget(self.progress)
        v.addWidget(self.save_btn, alignment=QtCore.Qt.AlignRight)
        v.addSpacing(6)
        v.addWidget(self.footer)
        self.setCentralWidget(central)

        # Estado
        self._zip_path: Path | None = None
        self._artifact_path: Path | None = None
        self._worker: BuildWorker | None = None

        # Conexiones
        self.pick_btn.clicked.connect(self.pick_zip)
        self.start_btn.clicked.connect(self.start_build)
        self.save_btn.clicked.connect(self.save_artifact)

        # Arranque visual
        self.statusBar().showMessage("Listo.")

    # --- UI actions ---

    def pick_zip(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Seleccionar ZIP del proyecto", "", "ZIP (*.zip)")
        if path:
            self._zip_path = Path(path)
            self.zip_label.setText(f"ZIP del proyecto: {self._zip_path}")
            self.start_btn.setEnabled(True)

    def start_build(self):
        if not self._zip_path:
            return
        self.log.clear()
        self.progress.setValue(0)
        self._artifact_path = None
        self.save_btn.setEnabled(False)

        opts = BuildOptions(
            onefile=self.onefile_chk.isChecked(),
            noconsole=self.noconsole_chk.isChecked(),
        )
        self._worker = BuildWorker(self._zip_path, opts, self)
        self._worker.signals.log.connect(self.append_log)
        self._worker.signals.progress.connect(self.progress.setValue)
        self._worker.signals.done.connect(self.build_done)

        self.toggle_inputs(False)
        self.statusBar().showMessage("Compilando…")
        self._worker.start()

    def append_log(self, text: str):
        self.log.appendPlainText(text.rstrip())
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def build_done(self, ok: bool, artifact_path: str, err: str):
        self.toggle_inputs(True)
        self.progress.setValue(100 if ok else self.progress.value())
        if ok:
            self._artifact_path = Path(artifact_path)
            self.statusBar().showMessage("¡Build terminado!")
            self.append_log("\n=== Build finalizado con éxito ===")
            self.append_log(f"Artefacto: {self._artifact_path}")
            self.save_btn.setEnabled(True)
            QtWidgets.QMessageBox.information(self, "Éxito", "Build finalizado. Puedes guardar el artefacto.")
        else:
            self.statusBar().showMessage("Error en build")
            self.append_log("\n=== ERROR ===")
            self.append_log(err)
            QtWidgets.QMessageBox.critical(self, "Error", err)

    def toggle_inputs(self, enabled: bool):
        self.pick_btn.setEnabled(enabled)
        self.onefile_chk.setEnabled(enabled)
        self.noconsole_chk.setEnabled(enabled)
        self.start_btn.setEnabled(enabled)

    def save_artifact(self):
        if not self._artifact_path or not self._artifact_path.exists():
            return
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Guardar artefacto", self._artifact_path.name, "ZIP (*.zip)")
        if dest:
            try:
                shutil.copyfile(self._artifact_path, dest)
                QtWidgets.QMessageBox.information(self, "Guardado", "Artefacto guardado correctamente.")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error guardando", str(e))

def main():
    app = QtWidgets.QApplication(sys.argv)
    # QDarkStyle para PySide6
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyside6"))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
