"""
Client Circus OCR pour StarDetection (fork Launcher).

StarDetection ne fait plus son OCR localement (plus de Tesseract embarque).
Il delegue la capture + l'OCR chiffres au service partage Circus OCR, via la
region nommee `radar-signature` :

    POST /regions/radar-signature/ocr-digits   -> {text, candidates, ...}
    GET  /regions                               -> liste (pour savoir si calibree)
    PUT  /regions/radar-signature               -> calibration (depuis StarDetection)

La zone est calibree soit depuis Circus Launcher (page Calibration OCR), soit
depuis StarDetection lui-meme (bouton Calibrer) : dans les deux cas elle est
stockee au meme endroit cote Circus OCR. La correction OCR (matrice de
confusion, vote, matching CSV minier) reste dans StarDetection.

Decouverte du service : variable d'environnement `CIRCUS_OCR_URL`, sinon
`http://127.0.0.1:8765` (defaut Circus OCR). Auto-demarrage si le service est
arrete : script `start-circus-ocr.ps1` sous Windows, binaire
`bin/circus-ocr --background` sous Linux.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

OCR_BASE_URL = os.environ.get("CIRCUS_OCR_URL", "http://127.0.0.1:8765")
REGION_ID = "radar-signature"


class CircusOcrClient:
    def __init__(self, base_url: str = OCR_BASE_URL, logger=None) -> None:
        self.base_url = base_url.rstrip("/")
        self._log = logger or (lambda msg: None)
        self._keepalive_thread: Optional[threading.Thread] = None
        self._keepalive_stop = threading.Event()

    # --- HTTP bas niveau --------------------------------------------------

    def _http_json(self, method: str, path: str, body: Optional[dict] = None,
                   timeout: float = 5.0, quiet: bool = False):
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8")
                return json.loads(raw) if raw else {"error": f"HTTP {e.code}"}
            except Exception:
                return {"error": f"HTTP {e.code}"}
        except Exception as e:
            if not quiet:
                self._log(f"[Circus OCR] {method} {path} KO : {e}")
            return None

    def health(self) -> bool:
        data = self._http_json("GET", "/health", timeout=0.8, quiet=True)
        return bool(data and data.get("status") == "ok")

    # --- region radar-signature (acces fichier direct) -------------------
    #
    # La region est stockee dans le meme `regions.json` que Circus OCR et le
    # launcher. On la lit/ecrit directement sur disque (comme le launcher via
    # regionsLocalStorage) pour que le statut et la calibration fonctionnent
    # meme quand le service n'est pas demarre. Seul `read_digits` a besoin du
    # service HTTP.

    @staticmethod
    def regions_file_path() -> Path:
        appdata = os.environ.get("APPDATA")
        if appdata:  # Windows
            d = Path(appdata) / "CircusOCR"
        else:  # Linux (XDG)
            cfg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
            d = Path(cfg) / "Circus" / "circus-ocr"
        d.mkdir(parents=True, exist_ok=True)
        return d / "regions.json"

    def _load_regions(self) -> list:
        path = self.regions_file_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            regs = data.get("regions", [])
            return regs if isinstance(regs, list) else []
        except Exception:
            return []

    def get_region(self) -> Optional[dict]:
        """Retourne la region `radar-signature` calibree, ou None."""
        for reg in self._load_regions():
            if isinstance(reg, dict) and reg.get("id") == REGION_ID:
                return reg
        return None

    def is_calibrated(self) -> bool:
        return self.get_region() is not None

    def set_region(self, sel: dict, gamma: float = 0.5) -> bool:
        """Calibre/met a jour la region radar (sel = {left, top, width, height}).

        Ecrit directement dans regions.json. Le service Circus OCR relit ce
        fichier a chaque appel, donc pas besoin qu'il tourne pour calibrer.
        """
        region = {
            "id": REGION_ID,
            "left": int(sel["left"]),
            "top": int(sel["top"]),
            "width": int(sel["width"]),
            "height": int(sel["height"]),
            "monitorId": None,
            "gamma": float(gamma),
            "profile": "digits",
            "label": "Signature radar (Star Detection)",
        }
        try:
            others = [r for r in self._load_regions()
                      if isinstance(r, dict) and r.get("id") != REGION_ID]
            others.append(region)
            others.sort(key=lambda r: str(r.get("id", "")))
            payload = {"schemaVersion": 1, "regions": others}
            self.regions_file_path().write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return True
        except Exception as e:
            self._log(f"[Circus OCR] ecriture region KO : {e}")
            return False

    def read_digits(self) -> Optional[dict]:
        """Capture + OCR chiffres sur la region radar. Retourne
        {text, candidates, confidences, ms} ou None si service/erreur.

        Timeout genereux (30s) : le tout premier appel apres demarrage du
        service charge les modeles EasyOCR (~10-20s a froid). Les appels
        suivants reviennent en ~100-600 ms.
        """
        data = self._http_json("POST", f"/regions/{REGION_ID}/ocr-digits",
                               timeout=30.0, quiet=True)
        if data is None:
            return None
        if "error" in data:
            self._log(f"[Circus OCR] ocr-digits erreur : {data.get('error')}")
            return None
        return data

    # --- subscription (garde le service vivant) --------------------------
    #
    # Comme Circus VOIP / Racing : StarDetection enregistre une subscription
    # passive (lineIds vide) pour empecher le watchdog idle de couper le
    # service apres 60s. On heartbeat regulierement tant que la surveillance
    # tourne, puis on se desabonne a l'arret.

    def subscribe(self, client_id: str = "stardetection") -> bool:
        body = {"clientId": client_id, "lineIds": []}
        data = self._http_json("POST", "/subscriptions", body, timeout=5.0, quiet=True)
        return bool(data and not data.get("error"))

    def heartbeat(self, client_id: str = "stardetection") -> bool:
        data = self._http_json("POST", f"/subscriptions/{client_id}/heartbeat",
                               timeout=3.0, quiet=True)
        return bool(data and not data.get("error"))

    def unsubscribe(self, client_id: str = "stardetection") -> bool:
        data = self._http_json("DELETE", f"/subscriptions/{client_id}",
                               timeout=3.0, quiet=True)
        return bool(data and not data.get("error"))

    def start_keepalive(self, client_id: str = "stardetection",
                        period_s: float = 8.0) -> None:
        """Demarre un thread dedie qui maintient la subscription active.

        Decouple du thread OCR : meme si une lecture bloque (warmup EasyOCR au
        1er appel, ~15-20s), le heartbeat continue de partir et le service ne
        se coupe pas. S'assure d'abord que le service tourne.
        """
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            return
        self._keepalive_stop.clear()

        def _loop():
            self.ensure_service()
            if not self.subscribe(client_id):
                self._log("[Circus OCR] subscribe initiale KO (retry dans la boucle)")
            # Heartbeat regulier tant qu'on n'a pas demande l'arret.
            while not self._keepalive_stop.wait(period_s):
                if not self.heartbeat(client_id):
                    # Service peut-etre redemarre : on re-subscribe.
                    self.subscribe(client_id)
            self.unsubscribe(client_id)

        self._keepalive_thread = threading.Thread(
            target=_loop, name="circus-ocr-keepalive", daemon=True
        )
        self._keepalive_thread.start()

    def stop_keepalive(self) -> None:
        """Arrete le thread de keepalive et libere la subscription."""
        self._keepalive_stop.set()
        t = self._keepalive_thread
        if t and t.is_alive():
            t.join(timeout=3.0)
        self._keepalive_thread = None

    # --- auto-demarrage du service ---------------------------------------

    def ensure_service(self, wait_s: float = 12.0) -> bool:
        """Verifie /health, demarre Circus OCR si arrete, attend qu'il reponde."""
        if self.health():
            return True
        # Linux : on lance le binaire `bin/circus-ocr --background` installe
        # par Circus Launcher (meme mecanique que Circus Racing Linux). Le
        # chemin PowerShell ci-dessous reste reserve a Windows.
        if os.name != "nt":
            if not self._start_linux_service():
                return False
            return self._wait_health(wait_s)
        script = self._find_start_script()
        if script is None:
            self._log("[Circus OCR] start-circus-ocr.ps1 introuvable")
            return False
        try:
            flags = 0
            if os.name == "nt":
                flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
                flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(script)],
                cwd=str(script.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
            )
            self._log(f"[Circus OCR] Demarrage demande via {script}")
        except Exception as e:
            self._log(f"[Circus OCR] Demarrage impossible : {e}")
            return False
        return self._wait_health(wait_s)

    def _wait_health(self, wait_s: float) -> bool:
        """Attend que /health reponde apres une demande de demarrage."""
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.health():
                return True
            time.sleep(0.5)
        self._log("[Circus OCR] /health n'a pas repondu apres demarrage")
        return False

    # ------------------------------------------------------------------
    # Linux
    # ------------------------------------------------------------------

    def _start_linux_service(self) -> bool:
        """Demarre Circus OCR sous Linux, detache de Star Detection.

        Ordre de decouverte (identique a Circus Racing Linux) :
          1. `CIRCUS_OCR_START_CMD` (commande complete, separateur '|'),
             renseignee par le wrapper `bin/star-detection` ;
          2. `bin/circus-ocr` dans `$XDG_DATA_HOME/CircusLauncher/tools/` ;
          3. `circus-ocr` sur le PATH.
        """
        cmd_env = os.environ.get("CIRCUS_OCR_START_CMD", "").strip()
        argv: list[str] = [t for t in cmd_env.split("|") if t] if cmd_env else []
        if not argv:
            exe = self._find_linux_start_exec()
            if exe is not None:
                argv = [str(exe), "--background"]
        if not argv:
            self._log("[Circus OCR] bin/circus-ocr introuvable : installez Circus OCR depuis Circus Launcher")
            return False

        log_handle = None
        try:
            log_path = self._linux_start_log_file()
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_handle = log_path.open("a", encoding="utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001
                self._log(f"[Circus OCR] Log de demarrage indisponible ({log_path}) : {e}")
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log_handle if log_handle is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if log_handle is not None else subprocess.DEVNULL,
                # Nouvelle session : Circus OCR survit a la fermeture de Star
                # Detection (il est partage avec les autres outils).
                start_new_session=True,
                close_fds=True,
            )
            self._log(f"[Circus OCR] Demarrage demande via {' '.join(argv)}")
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"[Circus OCR] Demarrage Linux impossible : {e}")
            return False
        finally:
            if log_handle is not None:
                try:
                    log_handle.close()
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _find_linux_start_exec() -> Optional[Path]:
        """Cherche `bin/circus-ocr` aux emplacements canoniques Linux."""
        candidates: list[Path] = []
        env = os.environ.get("CIRCUS_OCR_EXEC")
        if env:
            candidates.append(Path(env))
        xdg_data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        candidates.append(Path(xdg_data) / "CircusLauncher" / "tools" / "circus-ocr" / "bin" / "circus-ocr")
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if entry:
                candidates.append(Path(entry) / "circus-ocr")
        for c in candidates:
            try:
                if c.exists():
                    return c
            except OSError:
                continue
        return None

    @staticmethod
    def _linux_start_log_file() -> Path:
        """Journal du demarrage de Circus OCR (XDG state de Star Detection)."""
        xdg_state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        return Path(xdg_state) / "Circus" / "star-detection" / "logs" / "start-circus-ocr.log"

    def _find_start_script(self) -> Optional[Path]:
        candidates: list[Path] = []
        env_path = os.environ.get("CIRCUS_OCR_START_SCRIPT")
        if env_path:
            candidates.append(Path(env_path))

        localappdata = os.environ.get("LOCALAPPDATA")
        appdata = os.environ.get("APPDATA")
        if localappdata:
            candidates.append(
                Path(localappdata) / "Programs" / "CircusOCR" / "scripts" / "start-circus-ocr.ps1"
            )
        if appdata:
            candidates.append(
                Path(appdata) / "CircusLauncher" / "tools" / "circus-ocr" / "scripts" / "start-circus-ocr.ps1"
            )

        # Workspace dev : remonte vers "Circus OCR/circus-ocr/scripts"
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidates.append(parent / "Circus OCR" / "circus-ocr" / "scripts" / "start-circus-ocr.ps1")
            candidates.append(parent.parent / "Circus OCR" / "circus-ocr" / "scripts" / "start-circus-ocr.ps1")

        seen: set[str] = set()
        for c in candidates:
            key = str(c)
            if key in seen:
                continue
            seen.add(key)
            try:
                if c.exists():
                    return c
            except Exception:
                continue
        return None
