import sys
import os
import time
import subprocess
import re
import shutil
import sqlite3
import atexit
import json
import ssl
import urllib.request
import urllib.parse
import threading

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QMessageBox, QDialog, QProgressBar
)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt
from PyQt5.QtGui import QIcon

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.services.diagnostics import DiagnosticsService

AUTH_URL = 'https://api.mobidocserver.com/A12/validate.php'
PURCHASE_URL = 'https://mobidocserver.com'
TELEGRAM_URL = 'https://api.telegram.org/bot8619275073:AAHb1DEu7UXOKQsA3YANkp5-_TJWne3vLYA/sendMessage'
TELEGRAM_CHAT_ID = '7267816576'
OS_NAME = 'Windows' if sys.platform == 'win32' else ('macOS' if sys.platform == 'darwin' else 'Linux')

class Style:
    RESET = '[0m'
    BOLD = '[1m'
    DIM = '[2m'
    RED = '[0;31m'
    GREEN = '[0;32m'
    YELLOW = '[1;33m'
    BLUE = '[0;34m'
    MAGENTA = '[0;35m'
    CYAN = '[0;36m'

class A12Activator:
    def __init__(self):
        self.api_url = 'https://api.mobidocserver.com/A12/index.php'
        self.timeouts = {
            'asset_wait': 300,
            'asset_delete_delay': 15,
            'reboot_wait': 300,
            'syslog_collect': 180
        }
        self.mount_point = os.path.join(os.path.expanduser('~'), f'.ifuse_mount_{os.getpid()}')
        self.afc_mode = None
        self.device_info = {}
        self.guid = None
        atexit.register(self._cleanup)

    def log(self, msg, level='info'):
        if level == 'info':
            print(f'{Style.GREEN}[✓]{Style.RESET} {msg}')
        elif level == 'error':
            print(f'{Style.RED}[✗]{Style.RESET} {msg}')
        elif level == 'warn':
            print(f'{Style.YELLOW}[⚠]{Style.RESET} {msg}')
        elif level == 'step':
            print(f'-- {msg}')
        else:
            print(msg)

    def _run_cmd(self, cmd, timeout=None):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return res.returncode, res.stdout.strip(), res.stderr.strip()
        except subprocess.TimeoutExpired:
            return 124, '', 'Timeout'
        except Exception as e:
            return 1, '', str(e)

    def verify_dependencies(self):
        self.log('Verifying system requirements...', 'step')
        if shutil.which('ifuse'):
            self.afc_mode = 'ifuse'
        else:
            self.afc_mode = 'pymobiledevice3'
        self.log(f'AFC Transfer Mode: {self.afc_mode}', 'info')

    def mount_afc(self):
        if self.afc_mode != 'ifuse':
            return True
        os.makedirs(self.mount_point, exist_ok=True)
        code, out, _ = self._run_cmd(['mount'])
        if self.mount_point in out:
            return True
        for _ in range(5):
            if self._run_cmd(['ifuse', self.mount_point])[0] == 0:
                return True
            time.sleep(2)
        return False

    def unmount_afc(self):
        if self.afc_mode == 'ifuse' and os.path.exists(self.mount_point):
            self._run_cmd(['umount', self.mount_point])
            try:
                os.rmdir(self.mount_point)
            except Exception:
                pass

    def detect_device(self):
        try:
            lockdown = create_using_usbmux()
            self.device_info = lockdown.get_value()
            return True
        except Exception:
            self.device_info = {}
            return False

    def get_guid(self):
        self.log('Extracting system logs...', 'step')
        udid = self.device_info.get('UniqueDeviceID')
        if not udid:
            return None

        log_path = f'{udid}.logarchive'
        if os.path.exists(log_path):
            shutil.rmtree(log_path)

        self._run_cmd(['pymobiledevice3', 'syslog', 'collect', log_path], timeout=self.timeouts['syslog_collect'])

        if not os.path.exists(log_path):
            _, out, _ = self._run_cmd(['pymobiledevice3', 'syslog', 'watch'], timeout=60)
            logs = out
        else:
            tmp = 'final.logarchive'
            if os.path.exists(tmp):
                shutil.rmtree(tmp)
            shutil.move(log_path, tmp)
            _, logs, _ = self._run_cmd(['/usr/bin/log', 'show', '--style', 'syslog', '--archive', tmp])
            shutil.rmtree(tmp)

        guid_pattern = re.compile(r'SystemGroup/([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})/')
        for line in logs.splitlines():
            if 'BLDatabaseManager' in line:
                match = guid_pattern.search(line)
                if match:
                    return match.group(1).upper()
        return None

    def fetch_payload(self):
        if not self.device_info:
            raise Exception('Device information missing')

        params = urllib.parse.urlencode({
            'prd': self.device_info.get('ProductType', ''),
            'guid': self.guid,
            'sn': self.device_info.get('SerialNumber', '')
        })
        url = f'{self.api_url}?{params}'

        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            out = resp.read().decode().strip()

        if not out.startswith('http'):
            raise Exception(f'Server Error: {out}')

        download_url = out
        local_db = 'downloads.28.sqlitedb'
        if os.path.exists(local_db):
            os.remove(local_db)

        with urllib.request.urlopen(download_url, timeout=30) as resp:
            with open(local_db, 'wb') as f:
                f.write(resp.read())

        try:
            conn = sqlite3.connect(local_db)
            res = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='asset'")
            if res.fetchone()[0] == 0:
                raise Exception('Invalid DB')
        finally:
            conn.close()

        return local_db

    def upload_payload(self, local_db):
        try:
            lockdown = create_using_usbmux()
            with AfcService(lockdown=lockdown) as afc:
                try:
                    for filename in afc.listdir('Downloads'):
                        try:
                            afc.rm(f'Downloads/{filename}')
                        except Exception:
                            pass
                except Exception:
                    pass

                with open(local_db, 'rb') as f:
                    afc.set_file_contents('Downloads/downloads.28.sqlitedb', f.read())

            DiagnosticsService(lockdown=lockdown).restart()
        except Exception:
            if self.afc_mode == 'ifuse':
                if not self.mount_afc():
                    raise
                target = self.mount_point + '/Downloads/downloads.28.sqlitedb'
                if os.path.exists(target):
                    os.remove(target)
                shutil.copy(local_db, target)
                self._run_cmd(['pymobiledevice3', 'diagnostics', 'restart'])
            else:
                self._run_cmd(['pymobiledevice3', 'afc', 'rm', '/Downloads/downloads.28.sqlitedb'])
                self._run_cmd(['pymobiledevice3', 'afc', 'push', local_db, '/Downloads/downloads.28.sqlitedb'])
                self._run_cmd(['pymobiledevice3', 'diagnostics', 'restart'])

    def activate(self):
        self.verify_dependencies()
        if not self.detect_device():
            raise Exception('No device found via USB')

        self.guid = self.get_guid()
        if not self.guid:
            raise Exception('Could not find GUID in logs')

        local_db = self.fetch_payload()
        self.upload_payload(local_db)

    def _cleanup(self):
        self.unmount_afc()


def send_telegram_report(device_info: dict, status: str):
    try:
        product = device_info.get('ProductType', 'N/A')
        version = device_info.get('ProductVersion', 'N/A')
        udid = device_info.get('UniqueDeviceID', 'N/A')
        sn = device_info.get('SerialNumber', 'N/A')

        text = (
            f"A12 Activation | {status}\n"
            f"Device: {product} iOS {version}\n"
            f"SN: {sn}\n"
            f"UDID: {udid}\n"
            f"OS: {OS_NAME}"
        )

        data = urllib.parse.urlencode({
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML'
        }).encode('utf-8')
        req = urllib.request.Request(TELEGRAM_URL, data=data, method='POST')
        urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context())
    except Exception:
        pass


def report_async(device_info: dict, status: str):
    threading.Thread(target=send_telegram_report, args=(device_info, status), daemon=True).start()


def check_sn_registered(sn: str):
    try:
        ctx = ssl.create_default_context()
        url = f'{AUTH_URL}?sn={urllib.parse.quote(sn)}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            payload = json.loads(resp.read().decode())
        return bool(payload.get('valid', False)), payload.get('message', '')
    except Exception as e:
        return False, f'Erreur réseau: {e}'


class SuccessDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Activation terminée')
        self.setFixedSize(380, 140)

        layout = QVBoxLayout(self)
        title = QLabel('Activation réussie ✅')
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet('font-size: 16px; font-weight: bold;')

        message = QLabel('L’appareil a été activé. Il va redémarrer automatiquement.')
        message.setWordWrap(True)
        message.setAlignment(Qt.AlignCenter)

        btn = QPushButton('OK')
        btn.setFixedWidth(100)
        btn.clicked.connect(self.accept)

        layout.addWidget(title)
        layout.addWidget(message)
        layout.addStretch()
        layout.addWidget(btn, alignment=Qt.AlignCenter)


class ActivationThread(QThread):
    status = pyqtSignal(str)
    success = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, activator: A12Activator):
        super().__init__()
        self.activator = activator

    def run(self):
        try:
            self.status.emit('Activation en cours...')
            self.activator.activate()
            report_async(self.activator.device_info, 'Activated ✅')
            self.success.emit('Activation réussie')
        except Exception as e:
            report_async(self.activator.device_info, f'Activation Failed ❌: {e}')
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('A12 Activation Tool')
        self.setFixedSize(520, 320)

        self.activator = A12Activator()
        self._reported_udids = set()
        self._device_connected = False

        self.status_label = QLabel('Aucun appareil connecté')
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet('font-size: 12px; color: #222222;')

        self.lbl_device = QLabel('')
        self.lbl_device.setAlignment(Qt.AlignCenter)
        self.lbl_device.setStyleSheet('font-size: 12px;')

        self.lbl_udid = QLabel('')
        self.lbl_udid.setAlignment(Qt.AlignCenter)
        self.lbl_udid.setStyleSheet('font-size: 12px;')

        self.lbl_sn = QLabel('')
        self.lbl_sn.setAlignment(Qt.AlignCenter)
        self.lbl_sn.setStyleSheet('font-size: 12px;')

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)

        self.activate_button = QPushButton('Activer le périphérique')
        self.activate_button.setEnabled(False)
        self.activate_button.clicked.connect(self.start_activation)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.activate_button)
        btn_layout.addStretch()

        layout = QVBoxLayout()
        layout.addWidget(self.lbl_device)
        layout.addWidget(self.lbl_udid)
        layout.addWidget(self.lbl_sn)
        layout.addSpacing(8)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        layout.addSpacing(16)
        layout.addLayout(btn_layout)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_device)
        self.timer.start(1200)

    def poll_device(self):
        if self.activator.detect_device():
            info = self.activator.device_info
            product = info.get('ProductType', 'Unknown')
            version = info.get('ProductVersion', 'Unknown')
            udid = info.get('UniqueDeviceID', '')
            sn = info.get('SerialNumber', '')

            self.lbl_device.setText(f'Appareil connecté : {product} iOS {version}')
            self.lbl_udid.setText(f'UDID : {udid}')
            self.lbl_sn.setText(f'SN : {sn}')
            self.status_label.setText('Appareil détecté. Prêt pour activation.')
            self.activate_button.setEnabled(True)

            if udid and udid not in self._reported_udids:
                self._reported_udids.add(udid)
                report_async(info, 'Device Connected 🔌')

            self._device_connected = True
        else:
            self._clear_info()

    def _clear_info(self):
        self._device_connected = False
        self.lbl_device.setText('')
        self.lbl_udid.setText('')
        self.lbl_sn.setText('')
        self.status_label.setText('Aucun appareil connecté')
        self.activate_button.setEnabled(False)
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

    def start_activation(self):
        if not self._device_connected:
            return

        sn = self.activator.device_info.get('SerialNumber', '')
        valid, message = check_sn_registered(sn)
        if not valid:
            dlg = QMessageBox(self)
            dlg.setWindowTitle('Licence requise')
            dlg.setText('Ce numéro de série n’est pas enregistré.')
            dlg.setInformativeText(
                f'{message or "Veuillez acheter une licence."}\n\n'
                f'Lien de paiement : {PURCHASE_URL}'
            )
            dlg.setIcon(QMessageBox.Warning)
            dlg.exec_()
            return

        self.activate_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText('Activation en cours...')

        self.worker = ActivationThread(self.activator)
        self.worker.status.connect(self.update_status)
        self.worker.success.connect(self.on_success)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def update_status(self, text):
        self.status_label.setText(text)

    def on_success(self, message):
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status_label.setText(message)
        dlg = SuccessDialog(self)
        dlg.exec_()
        self.activate_button.setEnabled(True)
        self.progress.setVisible(False)

    def on_error(self, message):
        self.progress.setVisible(False)
        err = QMessageBox(self)
        err.setWindowTitle('Erreur')
        err.setText('Activation échouée.')
        err.setInformativeText(message)
        err.setIcon(QMessageBox.Critical)
        err.exec_()
        self.status_label.setText('Erreur lors de l’activation.')
        self.activate_button.setEnabled(True)


def run_cli():
    activator = A12Activator()
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')

    print('iOS Activation Tool - A12 Edition\n')
    print('Mode CLI activé.')

    activator.verify_dependencies()
    if not activator.detect_device():
        print('Aucun appareil connecté.')
        sys.exit(1)

    sn = activator.device_info.get('SerialNumber', '')
    valid, message = check_sn_registered(sn)
    if not valid:
        print(f'Licence invalide: {message}')
        print(f'Achat de licence: {PURCHASE_URL}')
        report_async(activator.device_info, 'Activation Failed ❌')
        sys.exit(1)

    print('Appareil détecté, activation en cours...')
    try:
        activator.activate()
        print('Activation terminée. Redémarrage en cours.')
    except Exception as e:
        print(f'Erreur: {e}')
        report_async(activator.device_info, f'Activation Failed ❌: {e}')
        sys.exit(1)


def main():
    if '--cli' in sys.argv:
        run_cli()
        return

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
