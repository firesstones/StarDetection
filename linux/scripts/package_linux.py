"""Produit le tar.gz Linux x64 de Star Detection.

Sortie (dans linux/dist/) :
  star-detection-v<version>-linux-x64.tar.gz
  star-detection-v<version>-linux-x64.manifest-fragment.json
  linux-manifest.json      (nom STABLE, lu par le launcher Linux)

Layout de l'archive :
  star-detection/
    bin/star-detection                 (mode 0755)
    lib/
      screen_monitor.py                <- app/ (source UNIQUE Windows + Linux)
      circus_ocr_client.py             <- app/
      liste.csv                        <- app/
      stardetection_launcher_cli.py    <- linux/scripts/
      star_detection_version.json      <- linux/scripts/
    requirements-linux.txt
    README_LINUX.md

Contrairement a Racing/VOIP, il n'y a PAS de copie Linux du code : le code
applicatif est pris tel quel dans `app/` (les branches Linux y sont inertes
sous Windows). Les deux plateformes ne peuvent donc pas diverger.

Verifications avant d'ecrire quoi que ce soit :
  - version/build identiques a l'installeur Windows (installer/StarDetection.iss) ;
  - tous les .py compilent ;
  - le wrapper bash est en ASCII.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import py_compile
import re
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LINUX_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = LINUX_DIR.parent
APP_DIR = PROJECT_ROOT / "app"
DIST_DIR = LINUX_DIR / "dist"
ISS_FILE = PROJECT_ROOT / "installer" / "StarDetection.iss"

TOOL_ID = "star-detection"
ROOT_DIR_NAME = "star-detection"
WRAPPER_NAME = "star-detection"
VERSION_FILE = SCRIPT_DIR / "star_detection_version.json"

# (source, nom dans lib/)
LIB_FILES = (
    (APP_DIR / "screen_monitor.py", "screen_monitor.py"),
    (APP_DIR / "circus_ocr_client.py", "circus_ocr_client.py"),
    (APP_DIR / "liste.csv", "liste.csv"),
    (SCRIPT_DIR / "stardetection_launcher_cli.py", "stardetection_launcher_cli.py"),
    (VERSION_FILE, "star_detection_version.json"),
)


def fail(msg: str) -> int:
    sys.stderr.write(f"[package_linux] ERREUR : {msg}\n")
    return 1


def read_iss_version() -> tuple[str | None, str | None]:
    text = ISS_FILE.read_text(encoding="utf-8", errors="replace")
    ver = re.search(r'^#define\s+AppVersion\s+"([^"]+)"', text, re.M)
    build = re.search(r'^#define\s+AppBuild\s+"([^"]+)"', text, re.M)
    return (ver.group(1) if ver else None, build.group(1) if build else None)


def add_file(tar: tarfile.TarFile, arcname: str, data: bytes, mode: int, mtime: int) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = mtime
    info.type = tarfile.REGTYPE
    tar.addfile(info, io.BytesIO(data))


def add_dir(tar: tarfile.TarFile, arcname: str, mtime: int) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = mtime
    tar.addfile(info)


def make_readme(version: str, build) -> bytes:
    return (
        f"# Star Detection {version} build {build} - Linux x64\n"
        "\n"
        "Port Linux de Star Detection (fork Circus Launcher) : lit la signature\n"
        "radar miniere de Star Citizen et la fait correspondre a un minerai.\n"
        "La capture et l'OCR sont delegues au service Circus OCR (HTTP local).\n"
        "\n"
        "## Pre-requis\n"
        "\n"
        "- Circus OCR installe (via Circus Launcher). Star Detection le demarre\n"
        "  tout seul s'il ne tourne pas.\n"
        "- python3 >= 3.10, python3-venv et tkinter (paquet systeme) :\n"
        "    Ubuntu/Debian : sudo apt install python3 python3-venv python3-tk\n"
        "    Fedora        : sudo dnf install python3 python3-tkinter\n"
        "    Arch          : sudo pacman -S python tk\n"
        "\n"
        "Au premier lancement, le wrapper cree un venv dans\n"
        "`$XDG_CACHE_HOME/Circus/star-detection/venv` et y installe numpy + mss.\n"
        "Ce venv survit aux mises a jour de l'outil.\n"
        "\n"
        "## Lancement\n"
        "\n"
        "    ./bin/star-detection                             # interface\n"
        "    ./bin/star-detection --launcher-json status      # contrat launcher\n"
        "    ./bin/star-detection --launcher-json paths\n"
        "    ./bin/star-detection --launcher-json version\n"
        "    ./bin/star-detection --launcher-json ping\n"
        "\n"
        "## Chemins XDG\n"
        "\n"
        "- config : `$XDG_CONFIG_HOME/Circus/star-detection/`\n"
        "  - `preferences.json` (langue, minerais, reglages)\n"
        "- logs   : `$XDG_STATE_HOME/Circus/star-detection/logs/`\n"
        "  - `debug.log`, `star-detection-wrapper.log`, `start-circus-ocr.log`\n"
        "- cache  : `$XDG_CACHE_HOME/Circus/star-detection/` (venv)\n"
        "- zone radar : partagee avec Circus OCR,\n"
        "  `$XDG_CONFIG_HOME/Circus/circus-ocr/regions.json` (region `radar-signature`)\n"
        "\n"
        "## Lien Circus OCR\n"
        "\n"
        "Endpoint par defaut : http://127.0.0.1:8765 (override : CIRCUS_OCR_URL).\n"
        "Le wrapper cherche `bin/circus-ocr` dans :\n"
        "  - `$CIRCUS_OCR_EXEC`\n"
        "  - `$XDG_DATA_HOME/CircusLauncher/tools/circus-ocr/bin/circus-ocr`\n"
        "  - `circus-ocr` dans le PATH\n"
        "\n"
        "## Limites connues\n"
        "\n"
        "- Sous Wayland pur, la capture d'ecran de Circus OCR peut etre noire :\n"
        "  utilisez une session X11 (ou XWayland pour le jeu).\n"
        "- La fenetre de calibration couvre tous les ecrans avec un voile\n"
        "  semi-transparent : sans compositeur, le voile est opaque. La\n"
        "  calibration peut aussi se faire depuis Circus Launcher.\n"
    ).encode("utf-8")


def numeric_build(raw) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    if isinstance(raw, int):
        return raw
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return int(digits) if digits else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mtime", type=int, default=None)
    args = parser.parse_args()

    payload = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    version = str(payload.get("version", "")).strip()
    channel = str(payload.get("channel", "beta")).strip()
    build = payload.get("build")
    if payload.get("toolId") != TOOL_ID or not version:
        return fail(f"{VERSION_FILE.name} invalide (toolId/version)")

    # --- Verification 1 : meme version que l'installeur Windows ------------
    iss_version, iss_build = read_iss_version()
    if iss_version != version or str(iss_build) != str(build):
        return fail(
            f"version Linux {version} build {build} != installeur Windows "
            f"{iss_version} build {iss_build} ({ISS_FILE})"
        )

    # --- Verification 2 : sources presentes + compilation ------------------
    wrapper = SCRIPT_DIR / "bin" / WRAPPER_NAME
    requirements = SCRIPT_DIR / "requirements-linux.txt"
    for path in [wrapper, requirements, *(src for src, _ in LIB_FILES)]:
        if not path.is_file():
            return fail(f"fichier manquant : {path}")
    with tempfile.TemporaryDirectory() as tmp:
        for src, name in LIB_FILES:
            if src.suffix == ".py":
                try:
                    py_compile.compile(str(src), cfile=str(Path(tmp) / (name + "c")), doraise=True)
                except py_compile.PyCompileError as exc:
                    return fail(f"{src.name} ne compile pas : {exc.msg}")

    # --- Verification 3 : wrapper bash en ASCII, fins de ligne Unix -------
    wrapper_bytes = wrapper.read_bytes().replace(b"\r\n", b"\n")
    try:
        wrapper_bytes.decode("ascii")
    except UnicodeDecodeError:
        return fail(f"{wrapper} contient des caracteres non ASCII")
    if not wrapper_bytes.startswith(b"#!/usr/bin/env bash\n"):
        return fail(f"{wrapper} : shebang bash absent")

    arch = "linux-x64"
    pkg_name = f"{TOOL_ID}-v{version}-{arch}"
    archive = DIST_DIR / f"{pkg_name}.tar.gz"
    fragment_path = DIST_DIR / f"{pkg_name}.manifest-fragment.json"
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive.unlink(missing_ok=True)
    fragment_path.unlink(missing_ok=True)

    mtime = args.mtime if args.mtime is not None else int(datetime.now(tz=timezone.utc).timestamp())

    print(f"[package_linux] version : {version}")
    print(f"[package_linux] build   : {build}")
    print(f"[package_linux] channel : {channel}")
    print(f"[package_linux] arch    : {arch}")
    print(f"[package_linux] dist    : {archive}")

    with tarfile.open(archive, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        add_dir(tar, ROOT_DIR_NAME, mtime)
        add_dir(tar, f"{ROOT_DIR_NAME}/bin", mtime)
        add_dir(tar, f"{ROOT_DIR_NAME}/lib", mtime)
        add_file(tar, f"{ROOT_DIR_NAME}/bin/{WRAPPER_NAME}", wrapper_bytes, 0o755, mtime)
        add_file(
            tar,
            f"{ROOT_DIR_NAME}/requirements-linux.txt",
            requirements.read_bytes().replace(b"\r\n", b"\n"),
            0o644,
            mtime,
        )
        add_file(tar, f"{ROOT_DIR_NAME}/README_LINUX.md", make_readme(version, build), 0o644, mtime)
        for src, name in LIB_FILES:
            data = src.read_bytes()
            if src.suffix in (".py", ".json"):
                data = data.replace(b"\r\n", b"\n")
            add_file(tar, f"{ROOT_DIR_NAME}/lib/{name}", data, 0o644, mtime)

    sha = hashlib.sha256()
    size = 0
    with archive.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
            size += len(chunk)
    sha256 = sha.hexdigest()

    print("")
    print("[package_linux] === SUCCES ===")
    print(f"  archive : {archive}")
    print(f"  taille  : {size} octets")
    print(f"  sha256  : {sha256}")

    platform_entry = {
        "kind": "tar-gz",
        "assetName": archive.name,
        "assetNamePattern": "^star-detection-v.*-linux-x64\\.tar\\.gz$",
        "sha256": sha256,
        "size": size,
        "launch": f"bin/{WRAPPER_NAME}",
        "launcherJson": f"bin/{WRAPPER_NAME}",
    }
    manifest_obj = {
        "schemaVersion": 1,
        "toolId": TOOL_ID,
        "version": version,
        "channel": channel,
    }
    build_num = numeric_build(build)
    if build_num is not None:
        manifest_obj["build"] = build_num
    manifest_obj["platforms"] = {"linux-x64": platform_entry}

    text = json.dumps(manifest_obj, indent=2, ensure_ascii=False) + "\n"
    fragment_path.write_text(text, encoding="utf-8")
    print(f"[package_linux] manifest fragment : {fragment_path}")
    # Nom STABLE lu par le launcher Linux sur la derniere release.
    linux_manifest_path = DIST_DIR / "linux-manifest.json"
    linux_manifest_path.write_text(text, encoding="utf-8")
    print(f"[package_linux] linux-manifest.json : {linux_manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
