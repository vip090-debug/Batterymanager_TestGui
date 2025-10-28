"""PyQt5 GUI application providing Modbus TCP test servers and client."""
from __future__ import annotations

import logging
import queue
import sys
from pathlib import Path
from copy import deepcopy
from typing import Dict, Mapping

from PyQt5 import QtCore, QtGui, QtWidgets

from .client import read_registers, write_register
from .config import ConfigManager
from .data_model import parse_register_value
from .logging_setup import LOG_FORMAT, setup_logging, update_log_level
from .servers import BaseServer, ServerManager, ServerState

LOGGER = logging.getLogger(__name__)
STYLE_PATH = Path(__file__).resolve().parent.parent / "styles" / "app.qss"


def _load_stylesheet() -> str:
    """Return the application stylesheet if available."""

    try:
        return STYLE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        LOGGER.warning("Stylesheet not found at %s", STYLE_PATH)
    except OSError as exc:
        LOGGER.warning("Failed to read stylesheet %s: %s", STYLE_PATH, exc)
    return ""

REGISTER_TYPES = ["holding", "input", "coils", "discrete"]


class WorkerSignals(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object)


class Worker(QtCore.QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # pragma: no cover - GUI feedback path
            LOGGER.exception("Worker execution failed")
            self.signals.error.emit(str(exc))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


class LogEmitter(QtCore.QObject):
    log_received = QtCore.pyqtSignal(object)

    def __init__(self, log_queue: queue.Queue[logging.LogRecord]) -> None:
        super().__init__()
        self._queue = log_queue
        self._active = True
        self._thread = QtCore.QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._poll_queue)
        self._thread.start()

    @QtCore.pyqtSlot()
    def _poll_queue(self) -> None:
        while self._active:
            try:
                record = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if record is None:
                continue
            self.log_received.emit(record)

    def stop(self) -> None:
        self._active = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:  # pragma: no cover - unlikely
            pass
        self._thread.quit()
        self._thread.wait(1000)


class LogWidget(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        controls_layout = QtWidgets.QHBoxLayout()

        level_label = QtWidgets.QLabel("Log level:")
        self.level_combo = QtWidgets.QComboBox()
        self.level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.level_combo.setCurrentText("INFO")
        self.level_combo.currentTextChanged.connect(update_log_level)
        controls_layout.addWidget(level_label)
        controls_layout.addWidget(self.level_combo)
        controls_layout.addStretch()

        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        layout.addLayout(controls_layout)
        layout.addWidget(self.text_edit)

        self._formatter = logging.Formatter(LOG_FORMAT)

    def append_record(self, record: logging.LogRecord) -> None:
        message = self._formatter.format(record)
        self.text_edit.appendPlainText(message)
        self.text_edit.verticalScrollBar().setValue(self.text_edit.verticalScrollBar().maximum())


class ConfigurationDialog(QtWidgets.QDialog):
    def __init__(self, config: Mapping[str, Mapping[str, int] | str]) -> None:
        super().__init__()
        self.setWindowTitle("Konfiguration")
        self._config = deepcopy(config)
        layout = QtWidgets.QVBoxLayout(self)

        self.battery_host = QtWidgets.QLineEdit(str(config["battery_server"]["host"]))
        self.battery_port = QtWidgets.QLineEdit(str(config["battery_server"]["port"]))
        self.battery_unit = QtWidgets.QLineEdit(str(config["battery_server"]["unit_id"]))

        self.master_host = QtWidgets.QLineEdit(str(config["master_server"]["host"]))
        self.master_port = QtWidgets.QLineEdit(str(config["master_server"]["port"]))
        self.master_unit = QtWidgets.QLineEdit(str(config["master_server"]["unit_id"]))

        layout.addWidget(self._build_group("Battery Server", self.battery_host, self.battery_port, self.battery_unit))
        layout.addWidget(self._build_group("Master Server", self.master_host, self.master_port, self.master_unit))

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self._validate_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _build_group(
        self, title: str, host_edit: QtWidgets.QLineEdit, port_edit: QtWidgets.QLineEdit, unit_edit: QtWidgets.QLineEdit
    ) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(group)
        form.addRow("Host:", host_edit)
        form.addRow("Port:", port_edit)
        form.addRow("Unit ID:", unit_edit)
        return group

    def _validate_and_accept(self) -> None:
        try:
            battery = self._validate_section(self.battery_host, self.battery_port, self.battery_unit)
            master = self._validate_section(self.master_host, self.master_port, self.master_unit)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Ungültige Eingabe", str(exc))
            return
        self._config["battery_server"].update(battery)
        self._config["master_server"].update(master)
        self.accept()

    def _validate_section(
        self, host_edit: QtWidgets.QLineEdit, port_edit: QtWidgets.QLineEdit, unit_edit: QtWidgets.QLineEdit
    ) -> Dict[str, int | str]:
        host = host_edit.text().strip() or "127.0.0.1"
        try:
            port = int(port_edit.text())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError as exc:  # pragma: no cover - GUI validation
            raise ValueError("Port muss eine Zahl zwischen 1 und 65535 sein.") from exc
        try:
            unit_id = int(unit_edit.text())
            if not (0 <= unit_id <= 247):
                raise ValueError
        except ValueError as exc:  # pragma: no cover - GUI validation
            raise ValueError("Unit ID muss zwischen 0 und 247 liegen.") from exc
        return {"host": host, "port": port, "unit_id": unit_id}

    def get_config(self) -> Mapping[str, Mapping[str, int] | str]:
        return self._config


class ServerTab(QtWidgets.QWidget):
    def __init__(self, *, title: str, server: BaseServer, thread_pool: QtCore.QThreadPool) -> None:
        super().__init__()
        self.server = server
        self.thread_pool = thread_pool
        self.setObjectName(title)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._create_info_group(title))
        layout.addWidget(self._create_rw_group())
        layout.addStretch()

        self.status_timer = QtCore.QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start()

    def _create_info_group(self, title: str) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox(f"{title} Kontrolle")
        layout = QtWidgets.QGridLayout(group)

        layout.addWidget(QtWidgets.QLabel("Host:"), 0, 0)
        self.host_label = QtWidgets.QLabel(self.server.host)
        layout.addWidget(self.host_label, 0, 1)

        layout.addWidget(QtWidgets.QLabel("Port:"), 1, 0)
        self.port_label = QtWidgets.QLabel(str(self.server.port))
        layout.addWidget(self.port_label, 1, 1)

        layout.addWidget(QtWidgets.QLabel("Unit ID:"), 2, 0)
        self.unit_label = QtWidgets.QLabel(str(self.server.unit_id))
        layout.addWidget(self.unit_label, 2, 1)

        self.status_label = QtWidgets.QLabel("Stopped")
        layout.addWidget(QtWidgets.QLabel("Status:"), 3, 0)
        layout.addWidget(self.status_label, 3, 1)

        self.start_button = QtWidgets.QPushButton("Start")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)

        self.start_button.clicked.connect(self._start_server)
        self.stop_button.clicked.connect(self._stop_server)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addStretch()
        layout.addLayout(button_layout, 4, 0, 1, 2)

        return group

    def _create_rw_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Read / Write")
        layout = QtWidgets.QGridLayout(group)

        layout.addWidget(QtWidgets.QLabel("Registertyp:"), 0, 0)
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(REGISTER_TYPES)
        layout.addWidget(self.type_combo, 0, 1)

        layout.addWidget(QtWidgets.QLabel("Adresse:"), 1, 0)
        self.address_edit = QtWidgets.QLineEdit()
        layout.addWidget(self.address_edit, 1, 1)

        layout.addWidget(QtWidgets.QLabel("Anzahl:"), 2, 0)
        self.count_edit = QtWidgets.QLineEdit("1")
        layout.addWidget(self.count_edit, 2, 1)

        layout.addWidget(QtWidgets.QLabel("Wert(e):"), 3, 0)
        self.value_edit = QtWidgets.QLineEdit()
        self.value_edit.setPlaceholderText("Komma-separiert für mehrere Werte")
        layout.addWidget(self.value_edit, 3, 1)

        self.read_button = QtWidgets.QPushButton("Read")
        self.write_button = QtWidgets.QPushButton("Write")

        self.read_button.clicked.connect(self._handle_read)
        self.write_button.clicked.connect(self._handle_write)

        layout.addWidget(self.read_button, 4, 0)
        layout.addWidget(self.write_button, 4, 1)

        self.result_box = QtWidgets.QTextEdit()
        self.result_box.setReadOnly(True)
        layout.addWidget(self.result_box, 5, 0, 1, 2)

        return group

    def update_server_info(self) -> None:
        self.host_label.setText(self.server.host)
        self.port_label.setText(str(self.server.port))
        self.unit_label.setText(str(self.server.unit_id))
        self._update_status()

    def _update_status(self) -> None:
        running = self.server.is_running()
        self.status_label.setText("Running" if running else "Stopped")
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _start_server(self) -> None:
        LOGGER.info('Starting server %s', self.server.name)
        try:
            self.server.start()
        except PermissionError as exc:
            QtWidgets.QMessageBox.critical(self, "Start fehlgeschlagen", str(exc))
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "Start fehlgeschlagen", str(exc))
        self._update_status()

    def _stop_server(self) -> None:
        LOGGER.info('Stopping server %s', self.server.name)
        self.server.stop()
        self._update_status()

    def _handle_read(self) -> None:
        try:
            address = int(self.address_edit.text())
            count = int(self.count_edit.text())
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Adresse und Anzahl müssen ganze Zahlen sein.")
            return

        register_type = self.type_combo.currentText()

        worker = Worker(
            read_registers,
            host=self.server.host,
            port=self.server.port,
            unit_id=self.server.unit_id,
            register_type=register_type,
            address=address,
            count=count,
        )
        worker.signals.result.connect(lambda result: self._display_read_result(register_type, address, result))
        worker.signals.error.connect(self._display_error)
        self.thread_pool.start(worker)

    def _handle_write(self) -> None:
        register_type = self.type_combo.currentText()
        try:
            address = int(self.address_edit.text())
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Adresse muss eine ganze Zahl sein.")
            return

        try:
            values = self._parse_values(register_type, self.value_edit.text())
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Fehler", str(exc))
            return

        worker = Worker(
            write_register,
            host=self.server.host,
            port=self.server.port,
            unit_id=self.server.unit_id,
            register_type=register_type,
            address=address,
            value=values,
        )
        worker.signals.result.connect(lambda _: self._display_write_success(register_type, address, values))
        worker.signals.error.connect(self._display_error)
        self.thread_pool.start(worker)

    def _parse_values(self, register_type: str, text: str):
        if register_type == "coils":
            if not text:
                raise ValueError("Bitte einen Wert für die Coil angeben (0 oder 1).")
            parts = [part.strip() for part in text.split(",") if part.strip()]
            if not parts:
                raise ValueError("Bitte gültige Werte eingeben.")
            bools = []
            for part in parts:
                if part in {"1", "true", "True"}:
                    bools.append(True)
                elif part in {"0", "false", "False"}:
                    bools.append(False)
                else:
                    raise ValueError("Coil-Werte müssen 0 oder 1 sein.")
            return bools if len(bools) > 1 else bools[0]
        else:
            if not text:
                raise ValueError("Bitte Werte für das Register eingeben.")
            parts = [part.strip() for part in text.split(",") if part.strip()]
            if not parts:
                raise ValueError("Bitte gültige Werte eingeben.")
            try:
                ints = [parse_register_value(part) for part in parts]
            except ValueError as exc:
                raise ValueError(str(exc))
            return ints if len(ints) > 1 else ints[0]

    def _display_read_result(self, register_type: str, address: int, result) -> None:
        if isinstance(result, list):
            formatted = ", ".join(str(value) for value in result)
        else:
            formatted = str(result)
        message = f"Read {register_type} @ {address}: {formatted}"
        LOGGER.info(message)
        self.result_box.append(message)

    def _display_write_success(self, register_type: str, address: int, values) -> None:
        message = f"Write {register_type} @ {address}: OK ({values})"
        LOGGER.info(message)
        self.result_box.append(message)

    def _display_error(self, message: str) -> None:
        LOGGER.error("Modbus operation failed: %s", message)
        QtWidgets.QMessageBox.warning(self, "Modbus-Fehler", message)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Modbus Test App")
        self.resize(900, 600)

        self.config_manager = ConfigManager()
        self.config_manager.ensure_exists()
        config = self.config_manager.get_config()

        initials = deepcopy(config["initial_registers"])

        battery_state = ServerState(
            host=config["battery_server"]["host"],
            port=int(config["battery_server"]["port"]),
            unit_id=int(config["battery_server"]["unit_id"]),
            initials=deepcopy(initials),
        )
        master_state = ServerState(
            host=config["master_server"]["host"],
            port=int(config["master_server"]["port"]),
            unit_id=int(config["master_server"]["unit_id"]),
            initials=deepcopy(initials),
        )

        self.server_manager = ServerManager(battery_state, master_state)

        self.thread_pool = QtCore.QThreadPool.globalInstance()

        self.tabs = QtWidgets.QTabWidget()
        self.battery_tab = ServerTab(title="Battery Server", server=self.server_manager.battery_server, thread_pool=self.thread_pool)
        self.master_tab = ServerTab(title="Master Server", server=self.server_manager.master_server, thread_pool=self.thread_pool)
        self.tabs.addTab(self.battery_tab, "Battery Server")
        self.tabs.addTab(self.master_tab, "Master Server")

        self.log_widget = LogWidget()

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.log_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

        self._create_menu()

        self.log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
        setup_logging(self.log_queue)
        self.log_emitter = LogEmitter(self.log_queue)
        self.log_emitter.log_received.connect(self.log_widget.append_record)

        LOGGER.info("Application started")

    def _create_menu(self) -> None:
        config_action = QtWidgets.QAction("Konfiguration...", self)
        config_action.triggered.connect(self._open_config_dialog)

        menu = self.menuBar().addMenu("Einstellungen")
        menu.addAction(config_action)

    def _open_config_dialog(self) -> None:
        config = self.config_manager.get_config()
        dialog = ConfigurationDialog(config)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            new_config = dialog.get_config()
            self.config_manager.save(new_config)
            running_battery = self.server_manager.battery_server.is_running()
            running_master = self.server_manager.master_server.is_running()
            if running_battery:
                self.server_manager.battery_server.stop()
            if running_master:
                self.server_manager.master_server.stop()
            self.server_manager.apply_configuration(new_config)
            self.battery_tab.update_server_info()
            self.master_tab.update_server_info()
            if running_battery:
                try:
                    self.server_manager.battery_server.start()
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(self, "Fehler", str(exc))
            if running_master:
                try:
                    self.server_manager.master_server.start()
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(self, "Fehler", str(exc))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # pragma: no cover - GUI close path
        self.server_manager.stop_all()
        if hasattr(self, "log_emitter"):
            self.log_emitter.stop()
        super().closeEvent(event)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    stylesheet = _load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
