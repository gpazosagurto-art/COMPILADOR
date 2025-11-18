# main.py
# GUI PySide6 con QDarkStyle, 2 modos (ZIP / Carpeta + drag&drop), logs, progreso,
# icono opcional y salida automática en la carpeta del ZIP o de la carpeta de proyecto.

from pathlib import Path
import sys

from PySide6 import QtCore, QtGui, QtWidgets
import qdarkstyle

from builder_core import (
    build_from_zip, build_from_dir, BuildOptions, BuildSignals
)

APP_TITLE = "🧱 Compilador .exe (PyInstaller)"
FOOTER_TEXT = "© 2025 Gabriel Golker"


class DropList(QtWidgets.QListWidget):
    """Área para soltar archivos/carpeta; sólo muestra, la copia se hace en build."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)  # <-- FIX
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e: QtGui.QDragMoveEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent):
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.exists():
                    self.addItem(str(p))
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class BuildWorker(QtCore.QThread):
    def __init__(self, mode_zip: bool, path: Path, opts: BuildOptions, parent=None):
        super().__init__(parent)
        self.mode_zip = mode_zip
        self.path = path
        self.opts = opts
        self.signals = BuildSignals()

    def run(self):
        try:
            if self.mode_zip:
                out_dir = build_from_zip(
                    zip_path=self.path,
                    opts=self.opts,
                    log_cb=lambda t: self.signals.log.emit(t),
                    phase_cb=lambda p: self.signals.progress.emit(p),
                )
            else:
                out_dir = build_from_dir(
                    proj_dir=self.path,
                    opts=self.opts,
                    log_cb=lambda t: self.signals.log.emit(t),
                    phase_cb=lambda p: self.signals.progress.emit(p),
                )
            self.signals.done.emit(True, str(out_dir), "")
        except Exception as e:
            self.signals.done.emit(False, "", str(e))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(980, 680)
        self._icon_path: Path | None = None
        self._last_output_dir: Path | None = None

        # Tabs: ZIP / Carpeta
        tabs = QtWidgets.QTabWidget()

        # --- Tab ZIP ---
        zip_tab = QtWidgets.QWidget()
        self.zip_path_lbl = QtWidgets.QLabel("ZIP del proyecto: (no seleccionado)")
        self.zip_pick_btn = QtWidgets.QPushButton("Elegir ZIP…")

        zip_l = QtWidgets.QVBoxLayout(zip_tab)
        zip_l.addWidget(self.zip_path_lbl)
        zip_l.addWidget(self.zip_pick_btn)
        zip_l.addStretch(1)

        # --- Tab Carpeta (armar) ---
        dir_tab = QtWidgets.QWidget()
        self.dir_path_lbl = QtWidgets.QLabel("Carpeta del proyecto: (no seleccionada)")
        self.dir_pick_btn = QtWidgets.QPushButton("Elegir carpeta del proyecto…")

        self.drop_list = DropList()
        self.drop_list.setToolTip(
            "Arrastra aquí archivos o carpetas para incluirlos en el proyecto "
            "(solo listado). Copia los archivos tú mismo a la carpeta elegida si quieres que queden allí."
        )

        dir_l = QtWidgets.QVBoxLayout(dir_tab)
        dir_l.addWidget(self.dir_path_lbl)
        dir_l.addWidget(self.dir_pick_btn)
        dir_l.addWidget(QtWidgets.QLabel("Arrastra archivos/carpeta a esta lista (solo display):"))
        dir_l.addWidget(self.drop_list, stretch=1)

        tabs.addTab(zip_tab, "Desde ZIP")
        tabs.addTab(dir_tab, "Armar carpeta")

        # Controles comunes
        self.noconsole_chk = QtWidgets.QCheckBox("Ocultar consola (--noconsole)")
        self.icon_btn = QtWidgets.QPushButton("Elegir icono (.ico)…")
        self.icon_lbl = QtWidgets.QLabel("Sin icono")
        icon_row = QtWidgets.QHBoxLayout()
        icon_row.addWidget(self.icon_btn)
        icon_row.addWidget(self.icon_lbl)
        icon_row.addStretch(1)

        self.start_btn = QtWidgets.QPushButton("Compilar (ONEDIR)")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.log.setFont(mono)

        self.open_out_btn = QtWidgets.QPushButton("Abrir carpeta de salida")
        self.open_out_btn.setEnabled(False)

        footer = QtWidgets.QLabel(FOOTER_TEXT)
        footer.setAlignment(QtCore.Qt.AlignHCenter)

        # Layout principal
        central = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(central)
        title = QtWidgets.QLabel(APP_TITLE)
        f = title.font()
        f.setPointSize(14)
        f.setBold(True)
        title.setFont(f)
        v.addWidget(title)
        v.addWidget(tabs, stretch=0)
        v.addWidget(self.noconsole_chk)
        v.addLayout(icon_row)
        v.addWidget(QtWidgets.QLabel("Logs"))
        v.addWidget(self.log, stretch=1)
        v.addWidget(self.progress)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.start_btn)
        row.addStretch(1)
        row.addWidget(self.open_out_btn)
        v.addLayout(row)
        v.addSpacing(6)
        v.addWidget(footer)
        self.setCentralWidget(central)

        # Estado
        self._zip_path: Path | None = None
        self._proj_dir: Path | None = None
        self._worker: BuildWorker | None = None

        # Conexiones
        self.zip_pick_btn.clicked.connect(self.pick_zip)
        self.dir_pick_btn.clicked.connect(self.pick_dir)
        self.icon_btn.clicked.connect(self.pick_icon)
        self.start_btn.clicked.connect(self.start_build)
        self.open_out_btn.clicked.connect(self.open_output_folder)

        self.statusBar().showMessage("Listo.")

    # -------- acciones --------

    def pick_zip(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Seleccionar ZIP del proyecto", "", "ZIP (*.zip)")
        if p:
            self._zip_path = Path(p)
            self.zip_path_lbl.setText(f"ZIP del proyecto: {self._zip_path}")
            # Modo ZIP activo, limpia el otro
            self._proj_dir = None
            self.dir_path_lbl.setText("Carpeta del proyecto: (no seleccionada)")

    def pick_dir(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "Seleccionar carpeta del proyecto", "")
        if p:
            self._proj_dir = Path(p)
            self.dir_path_lbl.setText(f"Carpeta del proyecto: {self._proj_dir}")
            # Modo DIR activo, limpia el otro
            self._zip_path = None
            self.zip_path_lbl.setText("ZIP del proyecto: (no seleccionado)")

    def pick_icon(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Seleccionar icono .ico", "", "Icono (*.ico)")
        if p:
            self._icon_path = Path(p)
            self.icon_lbl.setText(str(self._icon_path))
            self.setWindowIcon(QtGui.QIcon(str(self._icon_path)))

    def start_build(self):
        self.log.clear()
        self.progress.setValue(0)
        self.open_out_btn.setEnabled(False)
        self._last_output_dir = None

        if self._zip_path:
            mode_zip = True
            path = self._zip_path
        elif self._proj_dir:
            mode_zip = False
            path = self._proj_dir
        else:
            QtWidgets.QMessageBox.warning(self, "Falta fuente", "Selecciona un ZIP o una carpeta de proyecto.")
            return

        opts = BuildOptions(
            noconsole=self.noconsole_chk.isChecked(),
            icon_path=str(self._icon_path) if self._icon_path else None
        )

        self._worker = BuildWorker(mode_zip, path, opts, self)
        self._worker.signals.log.connect(self.append_log)
        self._worker.signals.progress.connect(self.progress.setValue)
        self._worker.signals.done.connect(self.build_done)

        self.toggle_inputs(False)
        self.statusBar().showMessage("Compilando…")
        self._worker.start()

    def append_log(self, text: str):
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def build_done(self, ok: bool, out_path: str, err: str):
        self.toggle_inputs(True)
        if ok:
            self.progress.setValue(100)
            self._last_output_dir = Path(out_path)
            self.statusBar().showMessage("Build finalizado")
            self.append_log("\n=== Build finalizado ===")
            self.append_log(f"Salida: {self._last_output_dir}")
            self.open_out_btn.setEnabled(True)
            QtWidgets.QMessageBox.information(self, "Éxito", f"Build listo en:\n{self._last_output_dir}\nY el ZIP al lado.")
        else:
            self.statusBar().showMessage("Error en build")
            self.append_log("\n=== ERROR ===")
            self.append_log(err)
            QtWidgets.QMessageBox.critical(self, "Error", err)

    def open_output_folder(self):
        if self._last_output_dir and self._last_output_dir.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._last_output_dir)))

    def toggle_inputs(self, enabled: bool):
        self.zip_pick_btn.setEnabled(enabled)
        self.dir_pick_btn.setEnabled(enabled)
        self.icon_btn.setEnabled(enabled)
        self.noconsole_chk.setEnabled(enabled)
        self.start_btn.setEnabled(enabled)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyside6"))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
