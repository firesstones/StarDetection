"""
Star Detection v0.3
====================
- Menu principal : Calibrer / Préférences / Lancer
- Calibrer : sélection visuelle de la zone (multi-écrans)
- Préférences : ✓/—/✕ par minerai + rareté + filtre par rareté
- Lancer : surveillance + correspondance radar avec couleurs
- ✓/✕ sur chaque item du contenu selon ses propres préférences
- ✓/✕ sur le nom du rocher si lui-même est dans les préférences
- Rareté affichée en étoiles (☆☆☆ à ★★★)
"""

import csv
import json
import re
import threading
import time
from pathlib import Path

import mss
import numpy as np
import tkinter as tk

import circus_ocr_client

# Fork Launcher : l'OCR passe desormais par le service partage Circus OCR
# (plus de Tesseract embarque). cv2 / pytesseract ne sont plus requis ; on les
# importe de maniere optionnelle pour que les anciennes fonctions OCR locales
# (conservees mais non appelees) ne cassent pas le chargement du module si les
# dependances ne sont plus packagees.
try:
    import cv2
except Exception:
    cv2 = None
try:
    import pytesseract
except Exception:
    pytesseract = None

# Detecte automatiquement le chemin de Tesseract
def _find_tesseract():
    import sys, os, shutil
    base = getattr(sys, "_MEIPASS", None)
    if base:
        bundled = os.path.join(base, "tesseract", "tesseract.exe")
        if os.path.exists(bundled):
            tess_dir = os.path.join(base, "tesseract")
            os.environ["TESSDATA_PREFIX"] = os.path.join(tess_dir, "tessdata")
            os.environ["PATH"] = tess_dir + os.pathsep + os.environ.get("PATH", "")
            return bundled
    t = shutil.which("tesseract")
    if t:
        return t
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"D:\Tesseract-OCR\tesseract.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tesseract-OCR")
        path, _ = winreg.QueryValueEx(key, "InstallDir")
        return str(Path(path) / "tesseract.exe")
    except Exception:
        pass
    return "tesseract"

# Fork Launcher : Tesseract n'est plus requis (OCR via Circus OCR). On ne
# configure son chemin que s'il est encore present (compat anciens builds).
if pytesseract is not None:
    pytesseract.pytesseract.tesseract_cmd = _find_tesseract()

try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

_SCALING = None

# Chemin du dossier du .exe (ou du script en dev)
import sys as _sys, os as _os
if getattr(_sys, "_MEIPASS", None):
    _BASE_DIR = Path(_sys.executable).parent
else:
    _BASE_DIR = Path(_os.path.dirname(_os.path.abspath(__file__)))

CONFIG_FILE = _BASE_DIR / "config.json"
CSV_FILE    = _BASE_DIR / "liste.csv"
PREFS_FILE  = _BASE_DIR / "preferences.json"

# Fork Launcher : client du service partage Circus OCR. La capture + l'OCR
# chiffres y sont delegues ; la zone radar est stockee dans regions.json.
# Le logger est defere (lambda) car _debug_log est defini plus bas.
_ocr = circus_ocr_client.CircusOcrClient(logger=lambda m: _debug_log(m))

INTERVAL      = 0.1
HISTORY_SIZE  = 20   # fenêtre de vote (nombre de frames)
VOTE_THRESHOLD = 4   # votes minimum pour confirmer une signature

# Fork Launcher : via Circus OCR, l'OCR tourne plus lentement (appel HTTP) et
# rate parfois une frame. On garde la valeur confirmee affichee tant qu'elle a
# ete revue il y a moins de CLEAR_TIMEOUT_S secondes (au lieu d'effacer des 3
# ratés consécutifs). La valeur ne disparait donc que si la signature radar a
# vraiment disparu de l'ecran un certain temps.
CLEAR_TIMEOUT_S = 5.0
# Periode de heartbeat de la subscription Circus OCR (garde le service vivant).
OCR_HEARTBEAT_S = 10.0

BG         = "#0a0a0f"
BG_ROW     = "#12121a"
BG_ROW_ALT = "#0e0e18"
ACCENT     = "#00e5ff"
TEXT       = "#e8e8f0"
MUTED      = "#6b6b80"
BORDER     = "#1e1e2e"
GREEN      = "#a8ff3e"
RED        = "#ff4444"
GOLD       = "#ffd700"

STAR_FULL  = "★"
STAR_EMPTY = "★"   # même caractère, couleur différente
STAR_COLOR_EMPTY = "#3a3a4a"  # gris foncé, légèrement plus clair que le fond

# ─────────────────────────────────────────────
#  Traduction FR / EN
# ─────────────────────────────────────────────

LANG = "FR"  # sera mis à jour après chargement des prefs

TRANSLATIONS = {
    # Menu principal
    "subtitle":         {"FR": "Détection de signatures radar",  "EN": "Radar signature detection"},
    "btn_calibrate":    {"FR": "⊙  Calibrer la zone",            "EN": "⊙  Calibrate zone"},
    "btn_prefs":        {"FR": "★  Préférences",                 "EN": "★  Preferences"},
    "btn_start":        {"FR": "▶  Lancer",                      "EN": "▶  Start"},
    "status_ok":        {"FR": "✔ Zone calibrée",                "EN": "✔ Zone calibrated"},
    "status_warn":      {"FR": "⚠ Aucune zone calibrée",         "EN": "⚠ No zone calibrated"},
    "status_no_csv":    {"FR": "⚠ liste.csv introuvable !",      "EN": "⚠ liste.csv not found!"},
    "status_calibrate": {"FR": "⚠ Calibrez d'abord la zone !",  "EN": "⚠ Calibrate the zone first!"},
    "credit":           {"FR": "Par Kainan & Claude AI",         "EN": "By Kainan & Claude AI"},
    # Message calibrage
    "calib_title":      {"FR": "Calibrage",                      "EN": "Calibration"},
    "calib_msg":        {
        "FR": (
            "Avant de calibrer :\n\n"
            "1. Dans le jeu, faites un ping\n"
            "   pour faire apparaitre la signature radar\n"
            "2. Cliquez OK et dessinez un cadre\n"
            "   autour du nombre affiche\n\n"
            "⚠ Ne pas inclure le logo devant le nombre\n"
            "   Selectionner uniquement les chiffres"
        ),
        "EN": (
            "Before calibrating:\n\n"
            "1. In game, do a ping\n"
            "   to display the radar signature\n"
            "2. Click OK and draw a frame\n"
            "   around the displayed number\n\n"
            "⚠ Do not include the logo before the number\n"
            "   Select only the digits"
        ),
    },
    # Fenêtre détection
    "value_label":      {"FR": "VALEUR",                         "EN": "VALUE"},
    "btn_menu":         {"FR": "← Menu",                         "EN": "← Menu"},
    "waiting":          {"FR": "En attente de détection...",     "EN": "Waiting for detection..."},
    "no_match":         {"FR": "Aucune correspondance",          "EN": "No match found"},
    "multi_sig":        {"FR": "Multi-signature radar",          "EN": "Multi-signature radar"},
    "multi_sig_hint":   {"FR": "Combinaison de plusieurs signatures : identification impossible.",
                         "EN": "Combination of several signatures: cannot identify."},
    # Fenêtre préférences
    "prefs_title":      {"FR": "Préférences",                    "EN": "Preferences"},
    "prefs_heading":    {"FR": "Préférences des minerais",       "EN": "Mineral preferences"},
    "prefs_legend":     {"FR": "—  neutre     ✓  préféré     ✕  exclu",
                         "EN": "—  neutral     ✓  preferred     ✕  excluded"},
    "prefs_all":        {"FR": "Tous",                           "EN": "All"},
    "prefs_search":     {"FR": "Rechercher...",                  "EN": "Search..."},
    "btn_reset":        {"FR": "Tout réinitialiser",             "EN": "Reset all"},
    "btn_save":         {"FR": "Sauvegarder",                    "EN": "Save"},
}

def T(key):
    """Retourne la traduction de la clé dans la langue courante."""
    return TRANSLATIONS.get(key, {}).get(LANG, key)

def _stars(rarete):
    """Retourne (nb_pleines, nb_vides) ou None si rareté invalide."""
    try:
        n = int(rarete)
        if 0 <= n <= 3:
            return (n, 3 - n)
    except Exception:
        pass
    return None

def load_prefs() -> dict:
    if PREFS_FILE.exists():
        return json.loads(PREFS_FILE.read_text())
    return {}

def save_prefs(prefs: dict):
    PREFS_FILE.write_text(json.dumps(prefs, indent=2))

def load_lang() -> str:
    prefs = load_prefs()
    return prefs.get("__lang__", "FR")

def save_lang(lang: str):
    prefs = load_prefs()
    prefs["__lang__"] = lang
    save_prefs(prefs)

def _load_icon(root):
    import sys, os
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    ico_path = os.path.join(base, "star_detection.ico")
    try:
        root.iconbitmap(ico_path)
    except Exception:
        pass

def _load_logo(size=24):
    import sys, os
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "logo_small.png")
    try:
        from PIL import Image, ImageTk
        img = Image.open(path).resize((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


# ─────────────────────────────────────────────
#  Chargement CSV
# ─────────────────────────────────────────────

def load_csv(path: Path) -> dict:
    """Retourne {sig: (nom, contenu, rarete)} — rarete est "" si absent."""
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    mapping = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(1024); f.seek(0)
        sep = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(f, delimiter=sep)
        for row in reader:
            code = row.get("signature_radar", "").strip()
            nom  = row.get("nom", "").strip()
            contenu = row.get("contenu", "").strip()
            rarete  = row.get("rarete", "").strip()
            if code.isdigit():
                mapping[int(code)] = (nom, contenu, rarete)
    return mapping


# ─────────────────────────────────────────────
#  Capture + OCR
# ─────────────────────────────────────────────

def capture_region(region):
    with mss.mss() as sct:
        raw = sct.grab(region)
        img = np.frombuffer(raw.bgra, dtype=np.uint8).reshape(raw.height, raw.width, 4)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

def preprocess(img):
    h, w = img.shape[:2]
    scale = max(1, 60 // max(h, 1))
    if scale > 1:
        img = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    kernel_dilate = np.ones((2, 2), np.uint8)
    binary = cv2.dilate(binary, kernel_dilate, iterations=1)
    kernel = np.array([[0,-1,0],[-1,5,-1],[0,-1,0]])
    return cv2.filter2D(binary, -1, kernel)

def preprocess_color(img):
    """Extrait les pixels de texte HUD en isolant blanc ET cyan/bleu-vert en HSV.
    Dans Star Citizen, le premier chiffre coloré a une teinte cyan (H≈85-120 OpenCV)."""
    h, w = img.shape[:2]
    scale = max(1, 60 // max(h, 1))
    if scale > 1:
        img = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # Masque blanc/gris clair (texte principal)
    mask_white = cv2.inRange(hsv, np.array([0, 0, 160]), np.array([180, 60, 255]))
    # Masque cyan/bleu-vert (premier chiffre coloré, H=85-120 en OpenCV HSV)
    mask_cyan = cv2.inRange(hsv, np.array([85, 80, 100]), np.array([120, 255, 255]))
    # Masque orange/jaune en complément (H=10-35) au cas où
    mask_orange = cv2.inRange(hsv, np.array([10, 100, 150]), np.array([35, 255, 255]))
    mask = cv2.bitwise_or(mask_white, cv2.bitwise_or(mask_cyan, mask_orange))
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)
    return mask

def preprocess_night(img):
    """Prétraitement pour le mode nocturne (écran monochrome orange/rouge).
    Utilise Otsu sur le canal V (luminosité) pour séparer texte et fond
    même quand ils ont la même teinte."""
    h, w = img.shape[:2]
    scale = max(1, 60 // max(h, 1))
    if scale > 1:
        img = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # Prend uniquement le canal V (luminosité)
    v = hsv[:, :, 2]
    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4, 4))
    v = clahe.apply(v)
    _, binary = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.dilate(binary, kernel, iterations=1)
    return binary

MAX_MULT  = 50   # multiplicateur maximum autorisé par rocher

TESS_CONFIG_PSM7 = "--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789"
TESS_CONFIG_PSM6 = "--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789"
TESS_CONFIG = TESS_CONFIG_PSM7  # config par défaut

# MIN/MAX calculés dynamiquement après chargement du CSV
MIN_VALUE = 1000
MAX_VALUE = 4300 * MAX_MULT

def update_value_range(mapping):
    """Recalcule MIN_VALUE et MAX_VALUE depuis le mapping CSV chargé."""
    global MIN_VALUE, MAX_VALUE
    if mapping:
        MIN_VALUE = min(mapping.keys())
        MAX_VALUE = max(mapping.keys()) * MAX_MULT

def preprocess_contrast(img):
    """Grayscale + CLAHE + Otsu + dilatation pour améliorer la lisibilité des traits fins (ex: '1')."""
    h, w = img.shape[:2]
    scale = max(1, 60 // max(h, 1))
    if scale > 1:
        img = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Dilatation légère pour épaissir les traits fins (surtout le "1")
    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.dilate(binary, kernel, iterations=1)
    return binary

DEBUG_OCR = False  # Mettre à True pour sauvegarder les images dans debug_ocr/

def _debug_log(msg):
    """Écrit un message de debug dans le terminal et dans debug.log."""
    if not DEBUG_OCR:
        return
    import datetime
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line)
    try:
        log_file = _BASE_DIR / "debug.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def _save_debug(img, label, ocr_result=None):
    """Sauvegarde une image de debug dans le dossier debug_ocr/."""
    debug_dir = _BASE_DIR / "debug_ocr"
    debug_dir.mkdir(exist_ok=True)
    import datetime
    ts = datetime.datetime.now().strftime("%H%M%S_%f")[:-3]
    suffix = f"_{ocr_result}" if ocr_result else ""
    cv2.imwrite(str(debug_dir / f"{ts}_{label}{suffix}.png"), img)


def _crop_to_number(img):
    """Recadre sur la zone contenant les chiffres en détectant le bloc
    principal de pixels lumineux et en coupant au premier creux après."""
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Trouve les lignes "actives" (contenant des pixels lumineux)
        row_max = np.max(gray, axis=1)
        bright_threshold = max(180, np.percentile(row_max, 60))
        active_rows = np.where(row_max >= bright_threshold)[0]

        if len(active_rows) == 0:
            return img

        # Trouve le premier bloc contigu de lignes actives
        first_row = active_rows[0]
        last_row = active_rows[0]
        for r in active_rows[1:]:
            if r - last_row <= 3:  # tolérance de 3 lignes de gap
                last_row = r
            else:
                break  # on s'arrête au premier creux

        # Marge verticale
        pad_v = 3
        y_min = max(0, first_row - pad_v)
        y_max = min(h, last_row + pad_v + 1)

        # Horizontalement : bounding box des pixels lumineux dans ce bloc
        region = gray[y_min:y_max, :]
        col_max = np.max(region, axis=0)
        active_cols = np.where(col_max >= bright_threshold)[0]
        if len(active_cols) == 0:
            return img

        pad_h = 4
        x_min = max(0, active_cols[0] - pad_h)
        x_max = min(w, active_cols[-1] + pad_h + 1)

        cropped = img[y_min:y_max, x_min:x_max]
        if cropped.shape[0] >= 5 and cropped.shape[1] >= 10:
            return cropped
    except Exception:
        pass
    return img

def _correct_ocr_text(text, lookup=None, allow_two_step=False):
    """Applique la correction OCR (matrice de confusion, hamming, variantes,
    validation CSV) sur une chaine OCR brute. Retourne la valeur corrigee
    valide, ou None.

    `allow_two_step` : autorise la correction a 2 chiffres modifies. Desactive
    par defaut (EasyOCR est fiable) car trop agressif -> faux positifs sur les
    multi-signatures.

    Fork Launcher : c'est exactement la logique de correction historique de
    `read_number._extract`, mais extraite pour operer sur le texte renvoye par
    Circus OCR (au lieu de la sortie pytesseract locale). Le comportement de
    correction est identique a l'upstream.
    """
    if not text:
        return None
    match = re.search(r"\d{3,6}", text)
    if not match:
        return None

    CONFUSIONS_OCR = {
        "7": ["1", "2"], "1": ["7"], "2": ["7"],
        "8": ["6", "5", "3", "0"], "6": ["8", "5"],
        "5": ["8", "6", "3"], "3": ["8", "5"], "0": ["8"],
    }

    def _try(val):
        if not val or not val.isdigit() or len(val) < 3:
            return None
        if not (MIN_VALUE <= int(val) <= MAX_VALUE):
            return None
        if lookup and not lookup.get(val):
            return None
        return val

    val = match.group()
    n_chiffres = len(val)

    if r := _try(val):
        return r
    if n_chiffres <= 4:
        if r := _try(val[1:]):
            return r

    def _hamming(a, b):
        return sum(x != y for x, y in zip(a, b)) if len(a) == len(b) else 99

    candidates = []
    if r := _try("1" + val):
        candidates.append((1, 99, 0, r))
    for i in range(1, n_chiffres):
        if r := _try(val[:i] + "1" + val[i:]):
            candidates.append((1, 99, 0, r))

    corrected_l1 = []
    for i, c in enumerate(val):
        for replacement in CONFUSIONS_OCR.get(c, []):
            corrected = val[:i] + replacement + val[i+1:]
            corrected_l1.append((corrected, i))
            if r := _try(corrected):
                pos_pen = 0 if i > 0 else 1
                candidates.append((0, _hamming(val, r), pos_pen, r))
            if r := _try("1" + corrected):
                candidates.append((1, _hamming(val, r), 0, r))
    # Correction niveau 2 (deux chiffres modifies) : desactivee par defaut.
    # Avec EasyOCR (lecture fiable) elle est trop agressive et peut transformer
    # une vraie multi-signature (ex. 24400) en fausse valeur CSV (24400 -> 74400
    # -> 14400). On la garde optionnelle pour compat, mais le fork ne l'utilise
    # plus : une lecture confiante hors CSV doit rester une multi-signature.
    if allow_two_step:
        for c1, _ in corrected_l1:
            for i, c in enumerate(c1):
                for replacement in CONFUSIONS_OCR.get(c, []):
                    c2 = c1[:i] + replacement + c1[i+1:]
                    if c2 != val:
                        if r := _try(c2):
                            candidates.append((0, _hamming(val, r) + 10, 0, r))
                        if r := _try("1" + c2):
                            candidates.append((1, _hamming(val, r) + 10, 0, r))
    if candidates:
        best = min(candidates, key=lambda x: (x[0], x[1], x[2], int(x[3])))
        if DEBUG_OCR:
            _debug_log(f"[CORR] '{val}' -> choix: {best[3]}")
        return best[3]
    return None


def _value_known(value, mapping, lookup):
    """True si `value` correspond a une signature connue du CSV (directe ou
    via multiplicateur). Sert a distinguer une vraie multi-signature (nombre
    confiant mais inconnu) d'une simple erreur OCR."""
    try:
        return bool(find_matches(int(value), mapping, lookup))
    except (ValueError, TypeError):
        return False


def read_signature_via_circus_ocr(client, lookup=None):
    """Lit la signature radar via le service Circus OCR (region radar-signature).

    Retourne un tuple (csv_value, raw_value) :
      - csv_value : lecture corrigee ET validee contre le CSV (str) ou None.
        Pilote le vote / l'identification du minerai (cas single-signature).
      - raw_value : meilleure lecture brute plausible (3-6 chiffres dans la
        plage MIN..MAX) meme si absente du CSV (str) ou None. Sert a detecter
        les MULTI-SIGNATURES : quand plusieurs rochers se combinent, la
        signature globale (ex. 24400) est un nombre reel mais absent du CSV.
        Plutot que de forcer une valeur CSV proche (fausse info), on l'expose.

    La capture et l'OCR chiffres sont faits par Circus OCR (EasyOCR, allowlist
    chiffres) ; seule la correction metier reste ici.
    """
    data = client.read_digits()
    if not data:
        return (None, None)
    texts = []
    combined = data.get("text")
    if combined:
        texts.append(combined)
    for cand in data.get("candidates", []) or []:
        if cand not in texts:
            texts.append(cand)
    if DEBUG_OCR and texts:
        _debug_log(f"[CIRCUS-OCR] texts={texts}")

    # csv_value : 1er texte qui se corrige en valeur CSV valide.
    csv_value = None
    for t in texts:
        r = _correct_ocr_text(t, lookup)
        if r:
            csv_value = r
            break

    # raw_value : 1ere lecture brute plausible (3-6 chiffres dans la plage),
    # priorite a la lecture combinee.
    raw_value = None
    for t in texts:
        mt = re.search(r"\d{3,6}", t or "")
        if not mt:
            continue
        v = mt.group()
        if MIN_VALUE <= int(v) <= MAX_VALUE:
            raw_value = v
            break

    return (csv_value, raw_value)


def read_number(img, lookup=None):
    """Essaie plusieurs prétraitements et retourne le premier nombre valide.
    Si lookup est fourni, ne retourne que les valeurs qui ont un match CSV.

    Fork Launcher : fonction historique conservee pour reference mais PLUS
    APPELEE (l'OCR passe par `read_signature_via_circus_ocr`). Necessite cv2 +
    pytesseract si jamais reutilisee."""

    def _extract(processed_img):
        for config in (TESS_CONFIG_PSM7, TESS_CONFIG_PSM6):
            text = pytesseract.image_to_string(processed_img, config=config).strip()
            if DEBUG_OCR and text:
                _debug_log(f"[OCR] '{text}'")
            match = re.search(r"\d{3,6}", text)
            if not match:
                continue

            CONFUSIONS_OCR = {
                "7": ["1", "2"], "1": ["7"], "2": ["7"],
                "8": ["6", "5", "3", "0"], "6": ["8", "5"],
                "5": ["8", "6", "3"], "3": ["8", "5"], "0": ["8"],
            }

            def _try(val):
                if not val or not val.isdigit() or len(val) < 3:
                    return None
                if not (MIN_VALUE <= int(val) <= MAX_VALUE):
                    return None
                if lookup and not lookup.get(val):
                    return None
                return val

            val = match.group()
            n_chiffres = len(val)  # nombre de chiffres lus par l'OCR

            if r := _try(val): return r
            # Ne supprime le premier chiffre que si val a 4 chiffres max
            if n_chiffres <= 4:
                if r := _try(val[1:]): return r

            def _hamming(a, b):
                """Nombre de positions différentes entre deux chaînes de même longueur."""
                return sum(x != y for x, y in zip(a, b)) if len(a) == len(b) else 99

            # Essaie "1"+val et insertions intérieures comme candidats avec pénalité longueur
            candidates = []
            if r := _try("1" + val): candidates.append((1, 99, 0, r))
            for i in range(1, n_chiffres):
                if r := _try(val[:i] + "1" + val[i:]): candidates.append((1, 99, 0, r))

            # Collecte toutes les corrections niveaux 1 et 2
            corrected_l1 = []
            for i, c in enumerate(val):
                for replacement in CONFUSIONS_OCR.get(c, []):
                    corrected = val[:i] + replacement + val[i+1:]
                    corrected_l1.append((corrected, i))
                    if r := _try(corrected):
                        pos_pen = 0 if i > 0 else 1
                        candidates.append((0, _hamming(val, r), pos_pen, r))
                    if r := _try("1" + corrected):
                        candidates.append((1, _hamming(val, r), 0, r))
            for c1, _ in corrected_l1:
                for i, c in enumerate(c1):
                    for replacement in CONFUSIONS_OCR.get(c, []):
                        c2 = c1[:i] + replacement + c1[i+1:]
                        if c2 != val:
                            if r := _try(c2):
                                candidates.append((0, _hamming(val, r) + 10, 0, r))
                            if r := _try("1" + c2):
                                candidates.append((1, _hamming(val, r) + 10, 0, r))
            # Priorité : sans '1' > avec '1', dist min, corrections hors pos 0 préférées, valeur min
            if candidates:
                best = min(candidates, key=lambda x: (x[0], x[1], x[2], int(x[3])))
                if DEBUG_OCR:
                    cands_str = " | ".join(
                        f"{c[3]}(av1={c[0]},dist={c[1]},pos={c[2]})"
                        for c in sorted(candidates, key=lambda x: (x[0], x[1], x[2], int(x[3])))[:5]
                    )
                    _debug_log(f"[CORR] '{val}' → candidats: {cands_str} → choix: {best[3]}")
                return best[3]
        return None

    try:
        # Passe 0 : image originale upscalée AVANT crop (préserve les chiffres colorés en bord)
        h0, w0 = img.shape[:2]
        scale0 = max(2, 60 // max(h0, 1))
        img_raw = cv2.resize(img, (w0 * scale0, h0 * scale0), interpolation=cv2.INTER_CUBIC)
        result = _extract(img_raw)
        if result:
            if DEBUG_OCR: _save_debug(img, "0_orig", result)
            return result

        # Recadre sur la zone lumineuse pour les passes suivantes
        img = _crop_to_number(img)
        if DEBUG_OCR: _save_debug(img, "0_original")

        p_night = preprocess_night(img)
        result = _extract(p_night)
        if result:
            if DEBUG_OCR: _save_debug(img, "0_orig", result); _save_debug(p_night, "night", result)
            return result

        p0 = preprocess_color(img)
        result = _extract(p0)
        if result:
            if DEBUG_OCR: _save_debug(img, "0_orig", result); _save_debug(p0, "color", result)
            return result

        p1 = preprocess_contrast(img)
        result = _extract(p1)
        if result:
            if DEBUG_OCR: _save_debug(img, "0_orig", result); _save_debug(p1, "clahe", result)
            return result

        p2 = preprocess(img)
        result = _extract(p2)
        if result:
            if DEBUG_OCR: _save_debug(img, "0_orig", result); _save_debug(p2, "adaptive", result)
            return result

        p3 = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        result = _extract(p3)
        if result:
            if DEBUG_OCR: _save_debug(img, "0_orig", result); _save_debug(p3, "gray", result)
            return result

    except Exception:
        pass
    # Aucune passe n'a réussi — sauvegarde l'image originale pour debug
    if DEBUG_OCR:
        _save_debug(img, "0_orig", "NONE")
        _debug_log("[MISS] aucune lecture valide")
    return None

def _get_variantes_direct(candidate):
    """Génère les variantes directes (1 niveau) d'une valeur OCR."""
    variantes = set()
    variantes.add(candidate)
    variantes.add("1" + candidate)
    for i in range(1, len(candidate)):
        variantes.add(candidate[:i] + "1" + candidate[i:])
    for i, c in enumerate(candidate):
        if c == "1":
            variantes.add(candidate[:i] + "11" + candidate[i+1:])
    CONFUSIONS = {
        "8": ["6", "5", "3", "0"],
        "6": ["8", "5"],
        "5": ["8", "6", "3"],
        "3": ["8", "5"],
        "0": ["8"],
        "7": ["1", "2"],
        "1": ["7"],
        "2": ["7"],
    }
    for i, c in enumerate(candidate):
        for replacement in CONFUSIONS.get(c, []):
            variantes.add(candidate[:i] + replacement + candidate[i+1:])
    if len(candidate) > 4 and candidate[0] in ("1", "7"):
        variantes.add(candidate[1:])
    if len(candidate) > 4 and candidate[0] == "7" and candidate[1] == "1":
        variantes.add(candidate[0] + candidate[2:])
    for i in range(len(candidate) - 1):
        if candidate[i] == candidate[i+1]:
            variantes.add(candidate[:i] + candidate[i+1:])
    return variantes

def _get_variantes(candidate, mapping=None, lookup=None):
    """Génère les variantes (profondeur 2) d'une valeur confirmée."""
    if not candidate:
        return set()
    min_len = len(candidate) - 1
    seen = set(_get_variantes_direct(candidate))
    for v in list(seen):
        seen.update(_get_variantes_direct(v))
    result = {v for v in seen if v.isdigit() and len(v) >= 3 and len(v) >= min_len}
    if lookup:
        result = {v for v in result
                  if v == candidate
                  or not lookup.get(v)
                  or abs(len(v) - len(candidate)) < 2}
    return result

def build_lookup(mapping, max_mult=50):
    lookup = {}
    for sig, (nom, contenu, rarete) in mapping.items():
        for mult in range(1, max_mult + 1):
            val = str(sig * mult)
            if val not in lookup:
                lookup[val] = []
            lookup[val].append((sig, nom, contenu, rarete, mult))
    return lookup

def _sort_key(match):
    """Tri : sans rareté en premier (ROC/FPS/Debris),
    puis par rareté croissante, puis par multiplicateur croissant."""
    sig, nom, contenu, rarete, mult = match
    try:
        r = int(rarete)
        has_rarete = 1
    except (ValueError, TypeError):
        r = 0
        has_rarete = 0
    return (has_rarete, r, mult)

def find_matches(value, mapping, lookup=None):
    val_str = str(value)
    if lookup is not None:
        return sorted(lookup.get(val_str, []), key=_sort_key)
    results = []
    for sig, (nom, contenu, rarete) in mapping.items():
        if sig > 0 and value % sig == 0:
            results.append((sig, nom, contenu, rarete, value // sig))
    return sorted(results, key=_sort_key)


# ─────────────────────────────────────────────
#  Sélecteur de zone (calibration)
# ─────────────────────────────────────────────

class RegionSelector:
    def __init__(self, parent):
        self.result = None
        self._too_small = False  # FIX v0.3.1 : flag zone trop petite
        self._start_x = self._start_y = 0
        self._rect = None

        with mss.mss() as sct:
            monitor = sct.monitors[0]
            self._offset_x = monitor["left"]
            self._offset_y = monitor["top"]
            self._total_w  = monitor["width"]
            self._total_h  = monitor["height"]

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.25)
        self.win.configure(bg="#000010")
        self.win.geometry(f"{self._total_w}x{self._total_h}+{self._offset_x}+{self._offset_y}")

        self.canvas = tk.Canvas(self.win, width=self._total_w, height=self._total_h,
                                bg="#000010", highlightthickness=0, cursor="crosshair")
        self.canvas.pack()

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        self.canvas.bind("<Escape>", lambda e: self.win.destroy())

        self.win.grab_set()
        self.win.focus_force()
        self.canvas.focus_set()
        self.win.after(100, self.canvas.focus_force)

        parent.wait_window(self.win)

    def _on_press(self, e):
        self._start_x, self._start_y = e.x, e.y
        if self._rect: self.canvas.delete(self._rect)

    def _on_drag(self, e):
        if self._rect: self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(
            self._start_x, self._start_y, e.x, e.y,
            outline="#00e5ff", width=3, fill="", dash=(6, 3)
        )

    def _on_release(self, e):
        x1 = min(self._start_x, e.x) + self._offset_x
        y1 = min(self._start_y, e.y) + self._offset_y
        x2 = max(self._start_x, e.x) + self._offset_x
        y2 = max(self._start_y, e.y) + self._offset_y
        if (x2 - x1) > 10 and (y2 - y1) > 10:
            self.result = {"left": x1, "top": y1, "width": x2-x1, "height": y2-y1}
        else:
            self._too_small = True  # FIX v0.3.1 : zone trop petite, on signale
        self.win.destroy()


# ─────────────────────────────────────────────
#  Fenêtre Préférences
# ─────────────────────────────────────────────

class PrefsWindow:
    def __init__(self, parent, mapping):
        self.prefs  = load_prefs()
        self.mapping = mapping
        self.btns   = {}
        self._filtre_rarete = None  # None = tous

        win = tk.Toplevel(parent)
        win.title(T("prefs_title"))
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(True, True)
        win.geometry("485x580")
        win.minsize(360, 200)
        _load_icon(win)

        tk.Label(win, text=T("prefs_heading"),
                 bg=BG, fg=ACCENT, font=("Courier", 11, "bold")).pack(pady=(14, 4))

        legend_frame = tk.Frame(win, bg=BG)
        legend_frame.pack(pady=(0, 6))
        for symbol, key_fr, key_en in [
            (" ✓ ", "préféré", "preferred"),
            (" — ", "neutre",  "neutral"),
            (" ✕ ", "exclu",   "excluded"),
        ]:
            cell = tk.Frame(legend_frame, bg=BG)
            cell.pack(side="left", padx=8)
            tk.Label(cell, text=symbol, bg=BORDER, fg=MUTED,
                     font=("Courier", 9, "bold"), padx=4, pady=2).pack(side="left")
            lbl_text = key_fr if LANG == "FR" else key_en
            tk.Label(cell, text=f" {lbl_text}", bg=BG, fg=MUTED,
                     font=("Courier", 8)).pack(side="left")

        # Barre de recherche
        search_frame = tk.Frame(win, bg=BG)
        search_frame.pack(fill="x", padx=10, pady=(8, 16))
        tk.Label(search_frame, text="🔍", bg=BG, fg=MUTED,
                 font=("Courier", 10)).pack(side="left", padx=(0, 4))
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=self.search_var,
                                bg=BORDER, fg=MUTED, insertbackground=TEXT,
                                font=("Courier", 10), relief="flat", bd=4)
        search_entry.pack(fill="x", expand=True)
        search_entry.insert(0, T("prefs_search"))
        def _on_focus_in(e):
            if search_entry.get() == T("prefs_search"):
                search_entry.delete(0, "end")
                search_entry.config(fg=TEXT)
        def _on_focus_out(e):
            if not search_entry.get():
                search_entry.insert(0, T("prefs_search"))
                search_entry.config(fg=MUTED)
        search_entry.bind("<FocusIn>", _on_focus_in)
        search_entry.bind("<FocusOut>", _on_focus_out)
        self.search_var.trace_add("write", lambda *a: self._apply_filters())

        # Filtres rareté
        filter_frame = tk.Frame(win, bg=BG)
        filter_frame.pack(fill="x", padx=10, pady=(0, 4))
        self._filter_btns = {}
        filtres = [(T("prefs_all"), None), (0, 0), (1, 1), (2, 2), (3, 3)]
        for item, val in filtres:
            is_active = val is None
            btn_bg = ACCENT if is_active else BORDER

            btn = tk.Frame(filter_frame, bg=btn_bg, cursor="hand2",
                           padx=6, pady=2)
            btn.pack(side="left", padx=2)
            btn.bind("<Button-1>", lambda e, v=val: self._set_filtre(v))

            if val is None:
                lbl = tk.Label(btn, text=T("prefs_all"), bg=btn_bg, fg=BG if is_active else MUTED,
                         font=("Courier", 9), cursor="hand2")
                lbl.pack(side="left")
                lbl.bind("<Button-1>", lambda e, v=val: self._set_filtre(v))
            else:
                full, empty = val, 3 - val
                if full:
                    lf = tk.Label(btn, text=STAR_FULL * full, bg=btn_bg, fg=GOLD,
                                  font=("Courier", 9), cursor="hand2")
                    lf.pack(side="left")
                    lf.bind("<Button-1>", lambda e, v=val: self._set_filtre(v))
                if empty:
                    le = tk.Label(btn, text=STAR_EMPTY * empty, bg=btn_bg,
                                  fg=STAR_COLOR_EMPTY,
                                  font=("Courier", 9), cursor="hand2")
                    le.pack(side="left")
                    le.bind("<Button-1>", lambda e, v=val: self._set_filtre(v))

            self._filter_btns[val] = btn

        tk.Frame(win, bg=BORDER, height=1).pack(fill="x", padx=10)

        # Boutons fixes en bas
        tk.Frame(win, bg=BORDER, height=1).pack(side="bottom", fill="x", padx=10)
        btn_frame = tk.Frame(win, bg=BG)
        btn_frame.pack(side="bottom", fill="x", pady=8)
        tk.Button(btn_frame, text=T("btn_reset"),
                  bg=BG, fg=MUTED, font=("Courier", 9), relief="flat",
                  activebackground=BORDER, cursor="hand2",
                  command=self._reset).pack(side="left", padx=10)
        tk.Button(btn_frame, text=T("btn_save"),
                  bg=ACCENT, fg=BG, font=("Courier", 10, "bold"), relief="flat",
                  activebackground="#00b8cc", cursor="hand2",
                  command=lambda: [save_prefs(self.prefs), win.destroy()],
                  padx=14, pady=6).pack(side="right", padx=10)

        # Liste scrollable
        container = tk.Frame(win, bg=BG)
        container.pack(fill="both", expand=True, padx=10, pady=6)

        canvas = tk.Canvas(container, bg=BG, highlightthickness=0, height=300)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.scroll_frame = tk.Frame(canvas, bg=BG)

        self.scroll_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw", width=440)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _scroll(e):
            canvas.yview_scroll(-1*(e.delta//120), "units")

        canvas.bind("<MouseWheel>", _scroll)
        self.scroll_frame.bind("<MouseWheel>", _scroll)

        # Construit la liste triée alphabétiquement
        noms_data = {}
        for sig, (nom, contenu, rarete) in mapping.items():
            if nom not in noms_data:
                noms_data[nom] = rarete

        for nom in sorted(noms_data.keys()):
            rarete = noms_data[nom]
            etat   = self.prefs.get(nom, "neutre")
            row = tk.Frame(self.scroll_frame, bg=BG_ROW)
            row.pack(fill="x", pady=2, padx=4)
            row.bind("<MouseWheel>", _scroll)

            stars_data = _stars(rarete)
            star_frame = tk.Frame(row, bg=BG_ROW, width=50)
            star_frame.pack(side="left", padx=(6, 2))
            star_frame.bind("<MouseWheel>", _scroll)
            if stars_data:
                full, empty = stars_data
                if full:
                    lf = tk.Label(star_frame, text=STAR_FULL * full, bg=BG_ROW, fg=GOLD,
                                  font=("Courier", 9), anchor="w")
                    lf.pack(side="left")
                    lf.bind("<MouseWheel>", _scroll)
                if empty:
                    le = tk.Label(star_frame, text=STAR_EMPTY * empty, bg=BG_ROW, fg=STAR_COLOR_EMPTY,
                                  font=("Courier", 9), anchor="w")
                    le.pack(side="left")
                    le.bind("<MouseWheel>", _scroll)
            else:
                tk.Label(star_frame, text="   ", bg=BG_ROW,
                         font=("Courier", 9), anchor="w").pack(side="left")

            lbl = tk.Label(row, text=nom, bg=BG_ROW, fg=TEXT,
                           font=("Courier", 10), anchor="w", width=14)
            lbl.pack(side="left", padx=(0, 4))
            lbl.bind("<MouseWheel>", _scroll)

            btn_prefere = tk.Button(row, text=" ✓ ",
                bg=GREEN if etat=="prefere" else BORDER,
                fg=BG if etat=="prefere" else MUTED,
                font=("Courier", 10, "bold"), relief="flat",
                activebackground=GREEN, activeforeground=BG,
                cursor="hand2", padx=4, pady=2)
            btn_prefere.pack(side="left", padx=3, pady=4)
            btn_prefere.bind("<MouseWheel>", _scroll)

            btn_neutre = tk.Button(row, text=" — ",
                bg=MUTED if etat=="neutre" else BORDER,
                fg="white", font=("Courier", 10, "bold"), relief="flat",
                activebackground=MUTED, activeforeground="white",
                cursor="hand2", padx=4, pady=2)
            btn_neutre.pack(side="left", padx=3, pady=4)
            btn_neutre.bind("<MouseWheel>", _scroll)

            btn_exclu = tk.Button(row, text=" ✕ ",
                bg=RED if etat=="exclu" else BORDER,
                fg="white", font=("Courier", 10, "bold"), relief="flat",
                activebackground=RED, activeforeground="white",
                cursor="hand2", padx=4, pady=2)
            btn_exclu.pack(side="left", padx=3, pady=4)
            btn_exclu.bind("<MouseWheel>", _scroll)

            self.btns[nom] = (btn_neutre, btn_prefere, btn_exclu, row, rarete)
            btn_prefere.configure(command=lambda n=nom: self._set(n, "prefere"))
            btn_neutre.configure(command=lambda n=nom: self._set(n, "neutre"))
            btn_exclu.configure(command=lambda n=nom: self._set(n, "exclu"))

        win.grab_set()
        parent.wait_window(win)

    def _set_filtre(self, val):
        self._filtre_rarete = val
        for v, btn in self._filter_btns.items():
            is_active = v == val
            new_bg = ACCENT if is_active else BORDER
            btn.config(bg=new_bg)
            for child in btn.winfo_children():
                child.config(bg=new_bg)
                if isinstance(child, tk.Label) and child.cget("text") == T("prefs_all"):
                    child.config(fg=BG if is_active else MUTED)
        self._apply_filters()

    def _apply_filters(self):
        raw = self.search_var.get()
        query = "" if raw == T("prefs_search") else raw.lower()
        for nom, (btn_neutre, btn_prefere, btn_exclu, row, rarete) in self.btns.items():
            row.pack_forget()
        for nom in sorted(self.btns.keys()):
            btn_neutre, btn_prefere, btn_exclu, row, rarete = self.btns[nom]
            visible = True
            if query and query not in nom.lower():
                visible = False
            if self._filtre_rarete is not None:
                try:
                    if int(rarete) != self._filtre_rarete:
                        visible = False
                except Exception:
                    visible = False
            if visible:
                row.pack(fill="x", pady=2, padx=4)

    def _set(self, nom, etat):
        self.prefs[nom] = etat
        btn_neutre, btn_prefere, btn_exclu, row, rarete = self.btns[nom]
        btn_prefere.configure(bg=GREEN if etat=="prefere" else BORDER,
                              fg=BG if etat=="prefere" else MUTED)
        btn_neutre.configure(bg=MUTED if etat=="neutre" else BORDER, fg="white")
        btn_exclu.configure(bg=RED if etat=="exclu" else BORDER, fg="white")

    def _reset(self):
        self.prefs = {}
        for nom, (btn_neutre, btn_prefere, btn_exclu, row, rarete) in self.btns.items():
            btn_prefere.configure(bg=BORDER, fg=MUTED)
            btn_neutre.configure(bg=MUTED, fg="white")
            btn_exclu.configure(bg=BORDER, fg="white")


# ─────────────────────────────────────────────
#  Menu principal
# ─────────────────────────────────────────────

class Menu:
    def __init__(self):
        global _SCALING
        self.root = tk.Tk()
        _SCALING = self.root.tk.call("tk", "scaling")
        self.root.title("Star Detection v0.3")
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.geometry("520x580")
        _load_icon(self.root)

        tk.Label(self.root, text="STAR DETECTION",
                 bg=BG, fg=ACCENT, font=("Courier", 16, "bold")).pack(pady=(28, 4))
        self._lbl_subtitle = tk.Label(self.root, text=T("subtitle"),
                 bg=BG, fg=MUTED, font=("Courier", 9))
        self._lbl_subtitle.pack(pady=(0, 24))

        self._btn_calibrate = tk.Button(self.root, text=T("btn_calibrate"),
                  bg=BORDER, fg=TEXT, font=("Courier", 11), relief="flat",
                  activebackground=ACCENT, activeforeground=BG,
                  command=self._calibrer, padx=20, pady=10, cursor="hand2", width=22)
        self._btn_calibrate.pack(pady=6)

        self._btn_prefs = tk.Button(self.root, text=T("btn_prefs"),
                  bg=BORDER, fg=TEXT, font=("Courier", 11), relief="flat",
                  activebackground=ACCENT, activeforeground=BG,
                  command=self._preferences, padx=20, pady=10, cursor="hand2", width=22)
        self._btn_prefs.pack(pady=6)

        self._btn_start = tk.Button(self.root, text=T("btn_start"),
                  bg=ACCENT, fg=BG, font=("Courier", 11, "bold"), relief="flat",
                  activebackground="#00b8cc", activeforeground=BG,
                  command=self._lancer, padx=20, pady=10, cursor="hand2", width=22)
        self._btn_start.pack(pady=6)

        self.status_label = tk.Label(self.root, bg=BG, font=("Courier", 8))
        self.status_label.pack(pady=(6, 0))
        self._update_status()

        # Bouton debug discret
        self._btn_debug = tk.Button(self.root, text="DEBUG: OFF",
                  bg=BG, fg="#3a3a4a", font=("Courier", 7), relief="flat",
                  activebackground=BG, activeforeground=MUTED,
                  command=self._toggle_debug, cursor="hand2")
        self._btn_debug.pack(pady=(4, 0))

        credit_frame = tk.Frame(self.root, bg=BG)
        credit_frame.pack(side="bottom", pady=10)
        self._logo = _load_logo(32)
        if self._logo:
            tk.Label(credit_frame, image=self._logo, bg=BG).pack(side="left", padx=(0,4))
        self._lbl_credit = tk.Label(credit_frame, text=T("credit"),
                 bg=BG, fg=MUTED, font=("Courier", 8))
        self._lbl_credit.pack(side="left")

        # Bouton langue avec mini-drapeau Canvas
        lang_frame = tk.Frame(self.root, bg=BG)
        lang_frame.pack(side="bottom", pady=(0, 4))
        self._lang_canvas = tk.Canvas(lang_frame, bg=BORDER, relief="flat",
                                      highlightthickness=0, cursor="hand2",
                                      width=62, height=26)
        self._lang_canvas.pack()
        self._lang_canvas.bind("<Button-1>", lambda e: self._toggle_lang())
        self._draw_lang_btn()

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.root.mainloop()

    def _draw_flag_fr(self, canvas, x, y, w=22, h=14):
        """Mini drapeau français : bleu / blanc / rouge."""
        t = w // 3
        canvas.create_rectangle(x,     y, x+t,   y+h, fill="#0055A4", outline="")
        canvas.create_rectangle(x+t,   y, x+t*2, y+h, fill="#FFFFFF", outline="")
        canvas.create_rectangle(x+t*2, y, x+w,   y+h, fill="#EF4135", outline="")

    def _draw_flag_gb(self, canvas, x, y, w=22, h=14):
        """Mini Union Jack reconnaissable."""
        canvas.create_rectangle(x, y, x+w, y+h, fill="#012169", outline="")
        canvas.create_line(x, y, x+w, y+h, fill="#FFFFFF", width=4)
        canvas.create_line(x+w, y, x, y+h, fill="#FFFFFF", width=4)
        mx, my = x+w//2, y+h//2
        canvas.create_rectangle(mx-3, y,  mx+3, y+h, fill="#FFFFFF", outline="")
        canvas.create_rectangle(x, my-2, x+w, my+2, fill="#FFFFFF", outline="")
        canvas.create_rectangle(mx-2, y,  mx+2, y+h, fill="#C8102E", outline="")
        canvas.create_rectangle(x, my-1, x+w, my+1, fill="#C8102E", outline="")

    def _draw_lang_btn(self):
        """Redessine le bouton langue avec le drapeau et le texte."""
        c = self._lang_canvas
        c.delete("all")
        c.create_rectangle(0, 0, 62, 26, fill=BORDER, outline="")
        if LANG == "FR":
            self._draw_flag_fr(c, 6, 6)
            c.create_text(33, 13, text="FR", fill=TEXT,
                          font=("Courier", 9, "bold"), anchor="w")
        else:
            self._draw_flag_gb(c, 6, 6)
            c.create_text(33, 13, text="EN", fill=TEXT,
                          font=("Courier", 9, "bold"), anchor="w")

    def _toggle_debug(self):
        global DEBUG_OCR
        DEBUG_OCR = not DEBUG_OCR
        if DEBUG_OCR:
            self._btn_debug.config(text="DEBUG: ON", fg=ACCENT)
            try:
                import datetime, shutil
                debug_dir = _BASE_DIR / "debug_ocr"
                if debug_dir.exists():
                    shutil.rmtree(debug_dir)
                debug_dir.mkdir(exist_ok=True)
                log_file = _BASE_DIR / "debug.log"
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(f"=== DEBUG SESSION {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            except Exception:
                pass
        else:
            self._btn_debug.config(text="DEBUG: OFF", fg="#3a3a4a")

    def _toggle_lang(self):
        global LANG
        LANG = "EN" if LANG == "FR" else "FR"
        save_lang(LANG)
        self._draw_lang_btn()
        self._refresh_menu_texts()

    def _refresh_menu_texts(self):
        self._lbl_subtitle.config(text=T("subtitle"))
        self._btn_calibrate.config(text=T("btn_calibrate"))
        self._btn_prefs.config(text=T("btn_prefs"))
        self._btn_start.config(text=T("btn_start"))
        self._lbl_credit.config(text=T("credit"))
        self._update_status()

    def _update_status(self):
        if _ocr.is_calibrated():
            self.status_label.config(text=T("status_ok"), fg=GREEN)
        else:
            self.status_label.config(text=T("status_warn"), fg="#ffaa00")

    def _calibrer(self):
        import tkinter.messagebox as mb
        mb.showinfo(T("calib_title"), T("calib_msg"))
        self.root.withdraw()
        self.root.after(500, self._lancer_calibration)

    def _lancer_calibration(self):
        try:
            selector = RegionSelector(self.root)
            if selector.result:
                # Fork Launcher : la zone est stockee dans Circus OCR
                # (regions.json), pas dans le config.json local.
                _ocr.set_region(selector.result)
            elif selector._too_small:
                # FIX v0.3.1 : zone trop petite → message clair à l'utilisateur
                import tkinter.messagebox as mb
                mb.showwarning(
                    T("calib_title"),
                    "La zone sélectionnée est trop petite.\n"
                    "Dessinez un cadre plus large autour du nombre."
                    if LANG == "FR" else
                    "Selected zone is too small.\n"
                    "Draw a larger frame around the number."
                )
            # else : annulé via Escape, comportement normal
        except Exception as e:
            print(f"Erreur calibration : {e}")
        finally:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
            self.root.geometry("520x580")
            self.root.tk.call("tk", "scaling", _SCALING)
            self._update_status()

    def _preferences(self):
        try:
            mapping = load_csv(CSV_FILE)
        except FileNotFoundError:
            self.status_label.config(text=T("status_no_csv"), fg=RED)
            return
        PrefsWindow(self.root, mapping)

    def _lancer(self):
        if not _ocr.is_calibrated():
            self.status_label.config(text=T("status_calibrate"), fg=RED)
            return
        try:
            mapping = load_csv(CSV_FILE)
        except FileNotFoundError:
            self.status_label.config(text=T("status_no_csv"), fg=RED)
            return
        # Fork Launcher : on s'assure que Circus OCR tourne et on demarre le
        # keepalive (subscription + heartbeat dans un thread dedie) pour que le
        # service ne se coupe pas pendant la surveillance.
        _ocr.start_keepalive()
        region = _ocr.get_region()
        self.root.destroy()
        App(region, mapping)


# ─────────────────────────────────────────────
#  Fenêtre de surveillance
# ─────────────────────────────────────────────

class App:
    def __init__(self, region, mapping):
        self.region    = region
        self.mapping   = mapping
        self.lookup    = build_lookup(mapping)
        update_value_range(mapping)
        self.history   = []
        self.raw_history = []          # lectures brutes (pour multi-signature)
        self._multi_sig_value = None   # multi-signature actuellement affichee
        self.font_size = 10
        self.confirmed_value = None
        self.running = True
        self._loading_active = False
        self._loading_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._loading_idx    = 0
        self.prefs = load_prefs()

        self.root = tk.Tk()
        self.root.title("Star Detection v0.3")
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(320, 80)
        self.root.geometry("600x400")
        _load_icon(self.root)

        val_frame = tk.Frame(self.root, bg=BG, pady=6)
        val_frame.pack(fill="x", padx=10)
        tk.Label(val_frame, text=T("value_label"),
                 bg=BG, fg=MUTED, font=("Courier", 8), anchor="w").pack(fill="x")

        val_row = tk.Frame(val_frame, bg=BG)
        val_row.pack(fill="x")
        self.val_label = tk.Label(val_row, text="—", bg=BG, fg=ACCENT,
                                  font=("Courier", self.font_size + 10, "bold"), anchor="w")
        self.val_label.pack(side="left")

        tk.Button(val_row, text=T("btn_menu"), bg=BG, fg=MUTED,
                  font=("Courier", 9), relief="flat",
                  activebackground=BORDER, activeforeground=TEXT,
                  command=self._retour_menu, cursor="hand2").pack(side="right", padx=4)
        tk.Button(val_row, text="A+", bg=BG, fg=ACCENT,
                  font=("Courier", 9, "bold"), relief="flat",
                  activebackground=BORDER, activeforeground=ACCENT,
                  command=self._font_up, cursor="hand2").pack(side="right", padx=2)
        tk.Button(val_row, text="A-", bg=BG, fg=MUTED,
                  font=("Courier", 9), relief="flat",
                  activebackground=BORDER, activeforeground=TEXT,
                  command=self._font_down, cursor="hand2").pack(side="right", padx=2)

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", padx=10)

        self.result_frame = tk.Frame(self.root, bg=BG)
        self.result_frame.pack(fill="both", expand=True, padx=10, pady=6)
        self._show_placeholder()

        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.mainloop()

    def _start_loading(self):
        if not self.running:
            return
        self._loading_active = True
        self._loading_idx = 0
        try:
            for w in self.result_frame.winfo_children():
                w.destroy()
        except Exception:
            return
        self._animate_loading()

    def _stop_loading(self):
        self._loading_active = False
        try:
            self.val_label.config(fg=ACCENT)
        except Exception:
            pass

    def _animate_loading(self):
        if not self._loading_active or not self.running:
            return
        try:
            frame = self._loading_frames[self._loading_idx % len(self._loading_frames)]
            self.val_label.config(text=frame, fg=MUTED,
                                  font=("Courier", self.font_size + 10, "bold"))
            self._loading_idx += 1
            self.root.after(80, self._animate_loading)
        except Exception:
            pass

    def _font_up(self):
        self.font_size = min(self.font_size + 1, 30)
        self._refresh_results()

    def _font_down(self):
        self.font_size = max(self.font_size - 1, 6)
        self._refresh_results()

    def _refresh_results(self):
        self.val_label.config(font=("Courier", self.font_size + 10, "bold"))
        if self.confirmed_value:
            value = int(self.confirmed_value)
            matches = find_matches(value, self.mapping, self.lookup)
            self._update_ui(self.confirmed_value, matches)
        else:
            self._show_placeholder()

    def _process_candidate(self, candidate):
        if candidate:
            matches = find_matches(int(candidate), self.mapping, self.lookup)
            if not matches:
                for v in _get_variantes(candidate, self.mapping, self.lookup):
                    if v != candidate:
                        try:
                            m = find_matches(int(v), self.mapping, self.lookup)
                            if m:
                                candidate = v
                                matches = m
                                break
                        except ValueError:
                            pass
        else:
            matches = []
        self.root.after(0, self._stop_loading)
        self.root.after(0, lambda: self._update_ui(candidate, matches))

    def _quit(self):
        self.running = False
        try:
            _ocr.stop_keepalive()
        except Exception:
            pass
        self.root.destroy()

    def _retour_menu(self):
        self.running = False
        try:
            _ocr.stop_keepalive()
        except Exception:
            pass
        self.root.destroy()
        Menu()

    def _show_placeholder(self):
        if not self.running:
            return
        self._multi_sig_value = None
        try:
            self._loading_active = False
            self.val_label.config(text="—", fg=ACCENT,
                                  font=("Courier", self.font_size + 10, "bold"))
            for w in self.result_frame.winfo_children():
                w.destroy()
            tk.Label(self.result_frame, text=T("waiting"),
                     bg=BG, fg=MUTED, font=("Courier", self.font_size), anchor="w").pack(fill="x")
        except Exception:
            pass

    def _show_multi_signature(self, value):
        """Affiche une multi-signature : nombre confiant mais inconnu du CSV
        (combinaison de plusieurs rochers). Evite de donner une fausse id."""
        if not self.running:
            return
        try:
            self._loading_active = False
            self.val_label.config(text=str(value), fg=GOLD,
                                  font=("Courier", self.font_size + 10, "bold"))
            for w in self.result_frame.winfo_children():
                w.destroy()
            tk.Label(self.result_frame, text=T("multi_sig"),
                     bg=BG, fg=GOLD, font=("Courier", self.font_size, "bold"),
                     anchor="w", justify="left", wraplength=480).pack(fill="x")
            tk.Label(self.result_frame, text=T("multi_sig_hint"),
                     bg=BG, fg=MUTED, font=("Courier", self.font_size - 1),
                     anchor="w", justify="left", wraplength=480).pack(fill="x")
        except Exception:
            pass

    def _update_ui(self, value, matches):
        if not self.running:
            return
        self._multi_sig_value = None
        try:
            self.val_label.config(text=value if value else "—", fg=ACCENT,
                                  font=("Courier", self.font_size + 10, "bold"))
            for w in self.result_frame.winfo_children(): w.destroy()
        except Exception:
            return
        if not matches:
            tk.Label(self.result_frame, text=T("no_match"),
                     bg=BG, fg=MUTED, font=("Courier", self.font_size), anchor="w").pack(fill="x")
            return

        for i, (sig, nom, contenu, rarete, mult) in enumerate(matches):
            etat_nom = self.prefs.get(nom, "neutre")
            if etat_nom == "prefere":
                row_bg = "#0d2010"
            elif etat_nom == "exclu":
                row_bg = "#200d0d"
            else:
                row_bg = BG_ROW if i % 2 == 0 else BG_ROW_ALT
            row = tk.Frame(self.result_frame, bg=row_bg, pady=4)
            row.pack(fill="x", pady=1)

            line = tk.Frame(row, bg=row_bg)
            line.pack(fill="x", padx=6)

            stars_data = _stars(rarete)
            if stars_data:
                full, empty = stars_data
                star_frame = tk.Frame(line, bg=row_bg)
                star_frame.pack(side="left")
                if full:
                    tk.Label(star_frame, text=STAR_FULL * full, bg=row_bg, fg=GOLD,
                             font=("Courier", self.font_size - 2), anchor="w").pack(side="left")
                if empty:
                    tk.Label(star_frame, text=STAR_EMPTY * empty, bg=row_bg, fg=STAR_COLOR_EMPTY,
                             font=("Courier", self.font_size - 2), anchor="w").pack(side="left")

            tk.Label(line, text=f"  → {mult} × {nom}  ",
                     bg=row_bg, fg=TEXT,
                     font=("Courier", self.font_size, "bold"), anchor="w").pack(side="left")
            tk.Label(line, text=f"(sig. {sig})",
                     bg=row_bg, fg=TEXT,
                     font=("Courier", self.font_size - 1), anchor="w").pack(side="left")

            if etat_nom == "prefere":
                tk.Label(line, text="✓",
                         bg=row_bg, fg=GREEN,
                         font=("Courier", self.font_size, "bold"), anchor="e").pack(side="right", padx=6)
            elif etat_nom == "exclu":
                tk.Label(line, text="✕",
                         bg=row_bg, fg=RED,
                         font=("Courier", self.font_size, "bold"), anchor="e").pack(side="right", padx=6)

            if contenu:
                for item in contenu.split("/"):
                    item = item.strip()
                    if not item:
                        continue
                    etat_item = self.prefs.get(item, "neutre")
                    item_rarete = ""
                    for sig2, (nom2, contenu2, rarete2) in self.mapping.items():
                        if nom2 == item:
                            item_rarete = rarete2
                            break
                    item_row = tk.Frame(row, bg=row_bg)
                    item_row.pack(fill="x", padx=6)

                    if etat_item == "prefere":
                        item_fg = GREEN
                        indicateur = "  ✓"
                    elif etat_item == "exclu":
                        item_fg = RED
                        indicateur = "  ✕"
                    else:
                        item_fg = ACCENT
                        indicateur = ""

                    tk.Label(item_row, text=f"     - {item}",
                             bg=row_bg, fg=item_fg,
                             font=("Courier", self.font_size - 1), anchor="w").pack(side="left")
                    stars_item = _stars(item_rarete)
                    if stars_item:
                        full_i, empty_i = stars_item
                        star_frame_i = tk.Frame(item_row, bg=row_bg)
                        star_frame_i.pack(side="left")
                        if full_i:
                            tk.Label(star_frame_i, text=STAR_FULL * full_i, bg=row_bg, fg=GOLD,
                                     font=("Courier", self.font_size - 3), anchor="w").pack(side="left")
                        if empty_i:
                            tk.Label(star_frame_i, text=STAR_EMPTY * empty_i, bg=row_bg, fg=STAR_COLOR_EMPTY,
                                     font=("Courier", self.font_size - 3), anchor="w").pack(side="left")
                    if indicateur:
                        tk.Label(item_row, text=indicateur,
                                 bg=row_bg, fg=item_fg,
                                 font=("Courier", self.font_size - 1, "bold"), anchor="w").pack(side="left")

    def _monitor_loop(self):
        # Le keepalive (subscription + heartbeat) tourne dans son propre thread
        # (demarre dans Menu._lancer), decouple de l'OCR : meme si une lecture
        # bloque (warmup EasyOCR), la subscription reste vivante.
        # Horodatage de la derniere fois ou la valeur confirmee a ete revue.
        last_confirmed_seen = time.monotonic()
        last_multi_seen = time.monotonic()
        while self.running:
            try:
                now = time.monotonic()
                # Fork Launcher : capture + OCR chiffres delegues a Circus OCR.
                # raw = valeur CSV (pilote le vote) ; raw_value = lecture brute
                # plausible meme hors CSV (detection multi-signature).
                raw, raw_value = read_signature_via_circus_ocr(_ocr, self.lookup)
                if DEBUG_OCR and (raw or raw_value):
                    _debug_log(f"[RAW] csv={raw} brut={raw_value}")
                self.history.append(raw)
                if len(self.history) > HISTORY_SIZE:
                    self.history.pop(0)
                self.raw_history.append(raw_value)
                if len(self.raw_history) > HISTORY_SIZE:
                    self.raw_history.pop(0)

                if raw is not None:
                    if self.confirmed_value is not None and not self._loading_active:
                        confirmed_variants = _get_variantes(self.confirmed_value, self.mapping, self.lookup)
                        confirmed_variants.add(self.confirmed_value)
                        if raw in confirmed_variants:
                            # On revoit la valeur confirmee : on repousse le timeout.
                            last_confirmed_seen = now
                        else:
                            self.root.after(0, self._start_loading)
                    elif self.confirmed_value is None and not self._loading_active:
                        self.root.after(0, self._start_loading)

                # Effacement base sur le temps : on ne vide la valeur confirmee
                # que si elle n'a pas ete revue depuis CLEAR_TIMEOUT_S (la
                # signature a vraiment disparu), au lieu d'effacer des 3 ratés.
                if (self.confirmed_value is not None
                        and (now - last_confirmed_seen) > CLEAR_TIMEOUT_S):
                    self.confirmed_value = None
                    self.history = []
                    last_confirmed_seen = now
                    self.root.after(0, self._show_placeholder)

                # Idem pour une multi-signature affichee : on la garde tant
                # qu'on la revoit, on l'efface si le scan s'arrete.
                if self._multi_sig_value is not None:
                    if raw_value == self._multi_sig_value:
                        last_multi_seen = now
                    elif (now - last_multi_seen) > CLEAR_TIMEOUT_S:
                        self.raw_history = []
                        self.root.after(0, self._show_placeholder)

                valid = [v for v in self.history if v is not None]
                if len(valid) < VOTE_THRESHOLD:
                    # Pas assez de votes CSV : peut-etre une MULTI-SIGNATURE.
                    # Si une lecture brute confiante domine et n'est dans AUCUNE
                    # signature connue (ni elle, ni ses variantes), c'est
                    # vraisemblablement une combinaison de plusieurs rochers.
                    # On l'affiche comme telle plutot que de forcer une fausse id.
                    if self.confirmed_value is None and not self._loading_active:
                        raw_valid = [v for v in self.raw_history if v is not None]
                        if len(raw_valid) >= VOTE_THRESHOLD:
                            from collections import Counter
                            top, cnt = Counter(raw_valid).most_common(1)[0]
                            if cnt >= VOTE_THRESHOLD and not _value_known(top, self.mapping, self.lookup):
                                if self._multi_sig_value != top:
                                    self._multi_sig_value = top
                                    last_multi_seen = now
                                    self.root.after(0, self._show_multi_signature, top)
                                else:
                                    last_multi_seen = now
                    time.sleep(INTERVAL)
                    continue

                csv_cache = {v: bool(find_matches(int(v), self.mapping, self.lookup)) for v in set(valid)}
                groups = {}
                for v in valid:
                    found = None
                    for rep in list(groups.keys()):
                        if v == rep:
                            found = rep
                            break
                        are_variants = v in _get_variantes(rep) or rep in _get_variantes(v)
                        if DEBUG_OCR and (v in ['3370','3570'] or rep in ['3370','3570']):
                            _debug_log(f"[GROUP] v={v} rep={rep} are_variants={are_variants} csv_v={bool(csv_cache.get(v))} csv_rep={bool(csv_cache.get(rep))}")
                        if csv_cache.get(v) and csv_cache.get(rep) and not are_variants:
                            continue
                        if bool(csv_cache.get(v)) != bool(csv_cache.get(rep)):
                            continue
                        if are_variants:
                            found = rep
                            break
                    if found is None:
                        groups[v] = {v: 1}
                    else:
                        groups[found][v] = groups[found].get(v, 0) + 1

                best_rep = max(groups, key=lambda r: sum(groups[r].values()))
                best_count = sum(groups[best_rep].values())
                val_counts = groups[best_rep]

                all_csv = all(bool(find_matches(int(v), self.mapping, self.lookup)) for v in val_counts)

                def _score(v):
                    cnt = val_counts[v]
                    has_match = bool(find_matches(int(v), self.mapping, self.lookup))
                    length_pref = len(v) if has_match else -len(v)
                    base_sig = min((e[0] for e in self.lookup.get(v, [])), default=999999)
                    # Le NOMBRE DE VOTES prime toujours : une lecture vue 50 fois
                    # doit battre un raté transitoire vu 2 fois, meme si ce dernier
                    # a une base plus basse. (Avant : all_csv ignorait le compte ->
                    # 10000 battait 10800.) length/base ne servent que de
                    # departage a votes egaux.
                    return (cnt, length_pref, -base_sig)

                candidate = max(val_counts, key=_score)
                if DEBUG_OCR:
                    _debug_log(f"[VOTE] candidate={candidate} best={best_count}/{len(valid)} groups={list(groups.keys())} all_csv={all_csv}")

                if candidate != self.confirmed_value and not self._loading_active:
                    confirmed_variants = _get_variantes(self.confirmed_value or "", self.mapping, self.lookup)
                    if candidate not in confirmed_variants:
                        self.root.after(0, self._start_loading)

                if self.confirmed_value is None:
                    if best_count < VOTE_THRESHOLD:
                        time.sleep(INTERVAL)
                        continue
                else:
                    confirmed_variants = _get_variantes(self.confirmed_value, self.mapping, self.lookup)
                    if DEBUG_OCR:
                        _debug_log(f"[VARCHECK] confirmed={self.confirmed_value} candidate={candidate} in_variants={candidate in confirmed_variants}")
                    if candidate in confirmed_variants:
                        # Le candidat est une variante de la valeur confirmee.
                        # On ne corrige QUE si cette variante est devenue
                        # nettement plus frequente (vote majoritaire), pas parce
                        # qu'elle a une base plus basse. Evite qu'un rate
                        # transitoire (ex. 10000 vu 2 fois) remplace la vraie
                        # valeur (10800 vue 50 fois).
                        if candidate != self.confirmed_value:
                            candidate_votes = sum(g.get(candidate, 0) for g in groups.values())
                            confirmed_votes = sum(
                                g.get(self.confirmed_value, 0) for g in groups.values()
                            )
                            if candidate_votes >= confirmed_votes + 3:
                                if DEBUG_OCR:
                                    _debug_log(f"[FIXUP] {self.confirmed_value} → {candidate} (votes {confirmed_votes} → {candidate_votes})")
                                self.confirmed_value = candidate
                                self.root.after(0, self._update_ui, candidate,
                                                find_matches(int(candidate), self.mapping, self.lookup))
                        time.sleep(INTERVAL)
                        continue
                    old_count = sum(
                        cnt for v, cnt in
                        [(v, c) for g in groups.values() for v, c in g.items()
                         if v == self.confirmed_value or v in confirmed_variants]
                    )
                    if best_count <= old_count + 7:
                        time.sleep(INTERVAL)
                        continue

                if candidate != self.confirmed_value:
                    self.confirmed_value = candidate
                    self.history = []
                    last_confirmed_seen = now  # nouvelle valeur : timeout reparti
                    c = candidate
                    self._t_detection = time.time()
                    self.root.after(0, self._start_loading)
                    self.root.after(150, lambda cc=c: threading.Thread(
                        target=self._process_candidate,
                        args=(cc,),
                        daemon=True
                    ).start())
                time.sleep(INTERVAL)
            except Exception:
                time.sleep(INTERVAL)


# ─────────────────────────────────────────────
#  Point d'entrée
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys_main
    _args = _sys_main.argv[1:]
    if "--lang" in _args:
        idx = _args.index("--lang")
        if idx + 1 < len(_args) and _args[idx + 1] in ("FR", "EN"):
            _installer_lang = _args[idx + 1]
            if not PREFS_FILE.exists():
                save_lang(_installer_lang)
    LANG = load_lang()
    Menu()
