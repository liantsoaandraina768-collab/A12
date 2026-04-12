import sys
import os
import time
import sqlite3
import tempfile
import json
import ssl
import certifi
import urllib.request
import urllib.parse
import threading
import platform

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QMessageBox, QDialog, QProgressBar
)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt
from PyQt5.QtGui import QPixmap, QIcon, QPainter, QColor, QFont, QPainterPath

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.services.diagnostics import DiagnosticsService

BACKEND_URL        = 'http://api.mobidocserver.com/A5/server.php'
VALIDATE_URL       = 'https://api.mobidocserver.com/A5/validate.php'
TELEGRAM_URL       = 'https://api.mobidocserver.com/A5/telegramreport.php'
TELEGRAM_BOT_TOKEN = '8619275073:AAHb1DEu7UXOKQsA3YANkp5-_TJWne3vLYA'
TELEGRAM_CHAT_ID   = '7267816576'

OS_NAME = 'Windows' if sys.platform == 'win32' else ('macOS' if sys.platform == 'darwin' else 'Linux')

SUPPORTED = {
    'iPhone4,1': {'9.3.5', '9.3.6'},
    'iPad2,1':   {'8.4.1', '9.3.5'},
    'iPad2,2':   {'9.3.5', '9.3.6'},
    'iPad2,3':   {'9.3.5', '9.3.6'},
    'iPad2,4':   {'8.4.1', '9.3.5'},
    'iPad2,5':   {'8.4.1', '9.3.5'},
    'iPad2,6':   {'9.3.5', '9.3.6'},
    'iPad2,7':   {'9.3.5', '9.3.6'},
    'iPad3,1':   {'8.4.1', '9.3.5'},
    'iPad3,2':   {'9.3.5', '9.3.6'},
    'iPad3,3':   {'9.3.5', '9.3.6'},
    'iPod5,1':   {'8.4.1', '9.3.5'},
    'iPhone5,1': {'10.3.3', '10.3.4'},
    'iPhone5,2': {'10.3.3', '10.3.4'},
    'iPhone5,3': {'10.3.3', '10.3.4'},
    'iPhone5,4': {'10.3.3', '10.3.4'},
    'iPad3,4':   {'10.3.3', '10.3.4'},
    'iPad3,5':   {'10.3.3', '10.3.4'},
    'iPad3,6':   {'10.3.3', '10.3.4'},
}


# ─────────────────────────────────────────────
#  Masquer les derniers caractères
# ─────────────────────────────────────────────

def mask(value: str, visible: int = 4) -> str:
    if not value or len(value) <= visible:
        return value
    return value[:visible] + '****'


# ─────────────────────────────────────────────
#  Telegram Report
# ─────────────────────────────────────────────

def send_telegram_report(device_info: dict, status: str):
    try:
        product = device_info.get('product', 'N/A')
        version = device_info.get('version', 'N/A')
        udid    = device_info.get('udid',    'N/A')
        imei    = device_info.get('imei',    'N/A')
        sn      = device_info.get('sn',      'N/A')

        ctx = ssl.create_default_context(cafile=certifi.where())

        # Géolocalisation via IP
        try:
            geo_req = urllib.request.urlopen('http://ip-api.com/json/', timeout=5)
            geo     = json.loads(geo_req.read().decode())
            country = geo.get('country', 'Unknown')
            city    = geo.get('city', '')
            location = f'{city}, {country}' if city else country
        except Exception:
            location = 'Unknown'

        data = urllib.parse.urlencode({
            'status':   status,
            'product':  product,
            'sn':       sn,
            'imei':     imei,
            'version':  version,
            'udid':     udid,
            'os':       OS_NAME,
            'location': location,
        }).encode()

        urllib.request.urlopen(
            urllib.request.Request(TELEGRAM_URL, data=data, method='POST'),
            timeout=10,
            context=ctx
        )
    except Exception:
        pass


def report_async(device_info: dict, status: str):
    threading.Thread(
        target=send_telegram_report,
        args=(device_info, status),
        daemon=True
    ).start()


# ─────────────────────────────────────────────
#  Utilitaires
# ─────────────────────────────────────────────

def resource_path(name):
    base = getattr(sys, '_MEIPASS', os.path.abspath('.'))
    return os.path.join(base, name)


def build_db_from_sql(sql_path, backend_url, target_path):
    with open(sql_path, 'r', encoding='utf-8') as f:
        sql = f.read()
    sql = sql.replace('BACKEND_URL', backend_url).replace('TARGET_PATH', target_path)
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    try:
        con = sqlite3.connect(tmp.name)
        con.executescript(sql)
        con.commit()
        con.close()
        with open(tmp.name, 'rb') as f:
            return f.read()
    finally:
        os.unlink(tmp.name)


def check_sn_registered(sn):
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        url = f'{VALIDATE_URL}?sn={sn}'
        req = urllib.request.urlopen(url, timeout=10, context=ctx)
        data = json.loads(req.read().decode())
        return data.get('valid', False)
    except Exception as e:
        print(f"[DEBUG] check_sn error: {e}")
        return False


# ─────────────────────────────────────────────
#  Label cliquable pour SN
# ─────────────────────────────────────────────

class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ─────────────────────────────────────────────
#  Popup de succès style macOS
# ─────────────────────────────────────────────

class SuccessDialog(QDialog):
    def __init__(self, parent=None, device_info=None):
        super().__init__(parent)
        self.device_info = device_info or {}
        self.setWindowTitle('Mobidoc')
        self.setFixedSize(400, 150)
        self.setStyleSheet("""
            QDialog { background-color: #000000; border-radius: 12px; }
            QLabel  { color: white; border: none; background: transparent; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(64, 64)
        icon_lbl.setStyleSheet('border: none; background: transparent;')
        logo_path = resource_path('logo.png')
        if os.path.exists(logo_path):
            src = QPixmap(logo_path).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pix = QPixmap(64, 64)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(0, 0, 64, 64, 14, 14)
            p.setClipPath(path)
            p.drawPixmap(0, 0, src)
            p.end()
        else:
            pix = QPixmap(64, 64)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addEllipse(0, 0, 64, 64)
            p.setClipPath(path)
            p.fillRect(0, 0, 64, 64, QColor('#2196F3'))
            p.setPen(QColor('white'))
            p.setFont(QFont('Arial', 18, QFont.Bold))
            p.drawText(pix.rect(), Qt.AlignCenter, 'H8')
            p.end()
        icon_lbl.setPixmap(pix)
        layout.addWidget(icon_lbl)

        right = QVBoxLayout()
        right.setSpacing(6)

        product = self.device_info.get('product', '')
        version = self.device_info.get('version', '')

        title = QLabel('Mobidoc A5/A6 v1.1.0')
        title.setStyleSheet(
            'font-size: 14px; font-weight: bold; color: white; '
            'border: none; background: transparent;'
        )

        msg = QLabel(f'Your Device {product} iOS {version}\nhas been Activated Successfully! 🎉')
        msg.setStyleSheet(
            'font-size: 12px; color: #cccccc; '
            'border: none; background: transparent;'
        )
        msg.setWordWrap(True)

        ok_btn = QPushButton('Ok')
        ok_btn.setFixedWidth(70)
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 5px;
                font-weight: bold;
            }
            QPushButton:hover   { background-color: #1976D2; }
            QPushButton:pressed { background-color: #0D47A1; }
        """)
        ok_btn.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)

        right.addWidget(title)
        right.addWidget(msg)
        right.addLayout(btn_row)
        layout.addLayout(right)


# ─────────────────────────────────────────────
#  Thread d'activation
# ─────────────────────────────────────────────

class ActivationThread(QThread):
    status  = pyqtSignal(str)
    success = pyqtSignal(str)
    error   = pyqtSignal(str)
    waiting = pyqtSignal(bool)

    def __init__(self, device_info=None):
        super().__init__()
        self._device_info = device_info or {}

    def wait_for_device(self, timeout=160):
        deadline = time.monotonic() + timeout
        first    = True
        while time.monotonic() < deadline:
            try:
                lockdown = create_using_usbmux()
                DiagnosticsService(lockdown=lockdown).mobilegestalt(keys=['ProductType'])
                if not first:
                    self.waiting.emit(False)
                    self.status.emit('Device reconnected ✓')
                return lockdown
            except Exception:
                if first:
                    self.waiting.emit(True)
                    self.status.emit('Waiting for device reconnection...')
                    first = False
                time.sleep(2)
        raise TimeoutError()

    def push_payload(self, lockdown, payload_db):
        with AfcService(lockdown=lockdown) as afc:
            # ── Fix AFC : ignorer les erreurs de suppression ──
            try:
                for filename in afc.listdir('Downloads'):
                    try:
                        afc.rm('Downloads/' + filename)
                    except Exception:
                        pass  # ignorer fichiers/sous-dossiers introuvables
            except Exception:
                pass  # ignorer si Downloads vide ou inaccessible
            time.sleep(3)
            afc.set_file_contents('Downloads/downloads.28.sqlitedb', payload_db)
        DiagnosticsService(lockdown=lockdown).restart()
        return self.wait_for_device()

    def should_hactivate(self, lockdown):
        return DiagnosticsService(lockdown=lockdown).mobilegestalt(
            keys=['ShouldHactivate']
        ).get('ShouldHactivate')

    def run(self):
        try:
            lockdown = create_using_usbmux()
            values   = lockdown.get_value()

            if values.get('ActivationState') == 'Activated':
                self.success.emit('Device is already activated')
                return

            sql_path = resource_path('payload.sql')
            if tuple(int(x) for x in values.get('ProductVersion').split('.')) >= (10, 3):
                payload_db = build_db_from_sql(
                    sql_path, BACKEND_URL,
                    '/private/var/containers/Shared/SystemGroup/'
                    'systemgroup.com.apple.mobilegestaltcache/Library/Caches/'
                    'com.apple.MobileGestalt.plist'
                )
            else:
                payload_db = build_db_from_sql(
                    sql_path, BACKEND_URL,
                    '/private/var/mobile/Library/Caches/com.apple.MobileGestalt.plist'
                )

            self.status.emit('Activating device...')

            for attempt in range(5):
                lockdown = self.push_payload(lockdown, payload_db)
                delay = 15 + attempt * 5
                time.sleep(delay)

                if self.should_hactivate(lockdown):
                    DiagnosticsService(lockdown=lockdown).restart()
                    report_async(self._device_info, 'Activated ✅')
                    self.success.emit('Done!')
                    return

                self.status.emit(f'Retrying activation — Attempt {attempt + 1}/5')
                time.sleep(5)

            report_async(self._device_info, 'Activation Failed ❌')
            self.error.emit(
                'Activation failed after multiple attempts. '
                'Make sure the device is connected to the Wi-Fi.'
            )

        except TimeoutError:
            report_async(self._device_info, 'Timeout Error ⏱️')
            self.error.emit(
                'Device did not reconnect in time. '
                'Please ensure it is connected and try again.'
            )
        except Exception as e:
            report_async(self._device_info, f'Exception ❌: {repr(e)}')
            self.error.emit(repr(e))


# ─────────────────────────────────────────────
#  Fenêtre principale
# ─────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Mobidoc A5/A6 v1.1.0')
        self.setFixedSize(500, 280)

        logo_path = resource_path('logo.png')
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        self._device_info    = {}
        self._current_sn     = ''
        self._reported_udids = set()

        self.status = QLabel('No device connected')
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet('color: #000000; font-size: 11px;')

        self.lbl_uuid   = QLabel('')
        self.lbl_device = QLabel('')
        self.lbl_udid   = QLabel('')

        self.lbl_imei_sn = ClickableLabel('')
        self.lbl_imei_sn.clicked.connect(self._copy_sn)
        self.lbl_imei_sn.setToolTip('Click to copy Serial Number')

        for lbl in (self.lbl_uuid, self.lbl_device, self.lbl_udid, self.lbl_imei_sn):
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet('color: #000000; font-size: 11px;')

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 5px;
                background-color: #1e1e1e;
                height: 14px;
                text-align: center;
                color: white;
                font-size: 10px;
            }
            QProgressBar::chunk {
                background-color: #2196F3;
                border-radius: 5px;
            }
        """)

        self.activate = QPushButton('Activate Device')
        self.activate.setEnabled(False)

        layout = QVBoxLayout()
        layout.addWidget(self.lbl_uuid)
        layout.addWidget(self.lbl_device)
        layout.addWidget(self.lbl_udid)
        layout.addWidget(self.lbl_imei_sn)
        layout.addSpacing(8)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.activate)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.activate.clicked.connect(self.start_activation)

        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._tick_progress)
        self._progress_val = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_device)
        self.timer.start(1000)

    def _copy_sn(self):
        if self._current_sn:
            QApplication.clipboard().setText(self._current_sn)
            self.lbl_imei_sn.setStyleSheet('color: #2196F3; font-size: 11px;')
            QTimer.singleShot(
                1000,
                lambda: self.lbl_imei_sn.setStyleSheet('color: #000000; font-size: 11px;')
            )

    def poll_device(self):
        try:
            lockdown = create_using_usbmux()
            values   = lockdown.get_value()

            product = values.get('ProductType', '')
            version = values.get('ProductVersion', '')
            udid    = lockdown.udid or ''
            imei    = values.get('InternationalMobileEquipmentIdentity', '')
            sn      = values.get('SerialNumber', '')

            # ── APP_UUID ──
            try:
                diag     = DiagnosticsService(lockdown=lockdown)
                mg       = diag.mobilegestalt(keys=['UniqueDeviceID'])
                app_uuid = mg.get('UniqueDeviceID', '') or udid
            except Exception:
                app_uuid = udid

            # ── ECID en hex ──
            try:
                chip_id = lockdown.get_value(key='UniqueChipID')
                if isinstance(chip_id, int):
                    ecid = hex(chip_id).upper().replace('0X', '')
                else:
                    ecid = str(chip_id)
            except Exception:
                ecid = udid

            is_supported = SUPPORTED.get(product)
            if not is_supported:
                self._clear_info()
                self._set_state(f'Unsupported Device: {product}', False)
                return

            if version not in is_supported:
                self._clear_info()
                self._set_state(f'Unsupported {product} iOS version: {version}', False)
                return

            self._device_info = {
                'product': product,
                'version': version,
                'udid':    udid,
                'imei':    imei,
                'sn':      sn,
                'ecid':    ecid,
            }
            self._current_sn = sn

            if udid and udid not in self._reported_udids:
                self._reported_udids.add(udid)
                report_async(self._device_info, 'Device Connected 🔌')

            self.lbl_uuid.setText(f'APP_UUID: {app_uuid}')
            self.lbl_device.setText(f'Connected Device: {product}  iOS {version}')
            self.lbl_udid.setText(f'ECID: {ecid}')
            self.lbl_imei_sn.setText(f'IMEI {imei}  SN: {sn} 📋')
            self.status.setVisible(False)
            self.activate.setEnabled(True)

        except Exception:
            self._clear_info()
            self._set_state('No device connected', False)

    def _clear_info(self):
        self._device_info = {}
        self._current_sn  = ''
        self.lbl_uuid.setText('')
        self.lbl_device.setText('')
        self.lbl_udid.setText('')
        self.lbl_imei_sn.setText('')

    def _set_state(self, text, enabled):
        self.status.setText(text)
        self.status.setVisible(True)
        self.activate.setEnabled(enabled)

    def _tick_progress(self):
        if self._progress_val < 90:
            self._progress_val += 2
            self.progress.setValue(self._progress_val)

    def _on_activation_status(self, msg):
        self.status.setText(msg)

    def _on_waiting(self, waiting: bool):
        if waiting:
            self._progress_timer.stop()
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(self._progress_val)
            self._progress_timer.start(600)

    def start_activation(self):
        product = self._device_info.get('product', '')
        version = self._device_info.get('version', '')

        # ── Étape 1 : Vérifier si supporté ──
        if product not in SUPPORTED or version not in SUPPORTED.get(product, set()):
            msg = QMessageBox(self)
            msg.setWindowTitle('Not Supported')
            msg.setText(f'Device {product} iOS {version} is not supported.')
            msg.setIcon(QMessageBox.Critical)
            msg.exec_()
            return

        # ── Étape 2 : Vérifier SN ──
        self.status.setText('Checking SN...')
        self.status.setVisible(True)
        QApplication.processEvents()

        if not check_sn_registered(self._current_sn):
            dlg = QDialog(self)
            dlg.setWindowTitle('Device Supported')
            dlg.setFixedWidth(380)
            dlg_layout = QVBoxLayout(dlg)
            dlg_layout.setContentsMargins(24, 24, 24, 20)
            dlg_layout.setSpacing(10)

            lbl_title = QLabel(f'✅ Device {product} iOS {version} is supported!')
            lbl_title.setStyleSheet('font-size: 13px; font-weight: bold;')
            lbl_title.setWordWrap(True)

            lbl_sn = QLabel(f'Serial Number: <b>{self._current_sn}</b>')
            lbl_sn.setStyleSheet('font-size: 12px;')

            lbl_msg = QLabel('Please register your Serial Number at:')
            lbl_msg.setStyleSheet('font-size: 12px;')

            lbl_link = QLabel('<a href="https://mobidocserver.com">mobidocserver.com</a>')
            lbl_link.setOpenExternalLinks(True)
            lbl_link.setStyleSheet('font-size: 12px;')

            btn_ok = QPushButton('OK')
            btn_ok.setFixedWidth(80)
            btn_ok.setStyleSheet("""
                QPushButton {
                    background-color: #2196F3;
                    color: white;
                    border: none;
                    border-radius: 5px;
                    padding: 6px;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #1976D2; }
            """)
            btn_ok.clicked.connect(dlg.accept)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            btn_row.addWidget(btn_ok)

            dlg_layout.addWidget(lbl_title)
            dlg_layout.addWidget(lbl_sn)
            dlg_layout.addWidget(lbl_msg)
            dlg_layout.addWidget(lbl_link)
            dlg_layout.addSpacing(6)
            dlg_layout.addLayout(btn_row)
            dlg.exec_()
            self.status.setVisible(False)
            return

        # ── Étape 3 : Lancer activation ──
        self.timer.stop()
        self.activate.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setVisible(True)
        self.status.setText('Starting activation...')

        self._progress_val = 0
        self._progress_timer.start(600)

        self.worker = ActivationThread(device_info=self._device_info)
        self.worker.status.connect(self._on_activation_status)
        self.worker.waiting.connect(self._on_waiting)
        self.worker.success.connect(self.on_success)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_success(self, msg):
        self._progress_timer.stop()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText('Activated Successfully!')
        dlg = SuccessDialog(self, device_info=self._device_info)
        dlg.exec_()
        self.progress.setVisible(False)
        self.status.setVisible(False)
        self.activate.setEnabled(True)
        self.timer.start(1000)

    def on_error(self, msg):
        self._progress_timer.stop()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        err = QMessageBox(self)
        err.setWindowTitle('Error')
        err.setText('Activation failed.')
        err.setInformativeText(msg)
        err.setIcon(QMessageBox.Critical)
        err.exec_()
        self.status.setText('Error occurred')
        self.status.setVisible(True)
        self.timer.start(1000)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
