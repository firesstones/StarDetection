"""Contrat CLI JSON de Star Detection pour Circus Launcher (Linux).

    bin/star-detection --launcher-json <commande>

Commandes : `version`, `status`, `paths`, `ping`.

Volontairement SANS tkinter, numpy ni mss : ce module doit repondre vite, sans
ecran et sans avoir besoin du venv (le wrapper l'appelle avec le Python du
systeme). stdout ne contient QUE du JSON ; code 0 si la reponse est fiable.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

TOOL_ID = "star-detection"
HERE = Path(__file__).resolve().parent


def _xdg(env_name: str, *fallback: str) -> Path:
    base = os.environ.get(env_name) or str(Path.home().joinpath(*fallback))
    return Path(base) / "Circus" / TOOL_ID


def _version() -> dict:
    try:
        return json.loads((HERE / "star_detection_version.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"toolId": TOOL_ID, "version": "0.0.0"}


def _paths() -> dict:
    return {
        "install": str(HERE.parent),
        "config": str(_xdg("XDG_CONFIG_HOME", ".config")),
        "logs": str(_xdg("XDG_STATE_HOME", ".local", "state") / "logs"),
        "cache": str(_xdg("XDG_CACHE_HOME", ".cache")),
        # Region radar partagee avec Circus OCR (calibration commune).
        "regions": str(
            Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
            / "Circus" / "circus-ocr" / "regions.json"
        ),
    }


def _ocr_online() -> bool:
    url = os.environ.get("CIRCUS_OCR_URL", "http://127.0.0.1:8765").rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=0.8) as resp:  # noqa: S310 (localhost)
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return isinstance(data, dict) and data.get("status") == "ok"
    except Exception:  # noqa: BLE001
        return False


def _radar_calibrated(regions_path: str) -> bool:
    try:
        data = json.loads(Path(regions_path).read_text(encoding="utf-8"))
        return any(r.get("id") == "radar-signature" for r in data.get("regions", []))
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "ping":
        out = {"ok": True, "status": {"pong": True}}
    elif cmd == "version":
        out = {"ok": True, "status": _version()}
    elif cmd == "paths":
        out = {"ok": True, "status": _paths()}
    elif cmd == "status":
        paths = _paths()
        out = {
            "ok": True,
            "status": {
                **_version(),
                "circusOcrOnline": _ocr_online(),
                "radarCalibrated": _radar_calibrated(paths["regions"]),
                "paths": paths,
            },
        }
    else:
        out = {"ok": False, "error": f"Commande inconnue : {cmd}"}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        return 2
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--launcher-json":
        args = args[1:]
    raise SystemExit(main(args))
