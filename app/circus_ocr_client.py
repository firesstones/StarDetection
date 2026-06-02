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
`http://127.0.0.1:8765` (defaut Circus OCR). Auto-demarrage via le script
`start-circus-ocr.ps1` de l'installation Circus OCR si le service est arrete.
"""

from __future__ import annotations

import json
import os
import subprocess
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

    # --- auto-demarrage du service ---------------------------------------

    def ensure_service(self, wait_s: float = 12.0) -> bool:
        """Verifie /health, demarre Circus OCR si arrete, attend qu'il reponde."""
        if self.health():
            return True
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
        # Attente que /health reponde
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.health():
                return True
            time.sleep(0.5)
        self._log("[Circus OCR] /health n'a pas repondu apres demarrage")
        return False

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
