# Star Detection — fork Circus Launcher

Fork de [`kainann/StarDetection`](https://github.com/kainann/StarDetection)
adapté à l'écosystème **Circus Launcher**.

Star Detection lit la **signature radar** minière de Star Citizen (un nombre)
et la fait correspondre à un minerai via `liste.csv`.

## Différence avec l'upstream

L'upstream embarque son propre **Tesseract** et fait l'OCR localement. Ce fork
**délègue la capture + l'OCR au service partagé Circus OCR** (EasyOCR), comme
les autres outils Circus (VOIP, Racing) :

- plus de Tesseract / OpenCV embarqués → installeur beaucoup plus léger ;
- l'OCR chiffres passe par `POST /regions/radar-signature/ocr-digits` ;
- la zone radar est calibrée et stockée dans Circus OCR (`regions.json`,
  région `radar-signature`), soit depuis Circus Launcher (page Calibration
  OCR), soit depuis Star Detection lui-même (bouton Calibrer) ;
- la **correction OCR** (matrice de confusion, distance de Hamming, variantes)
  et le **matching CSV minier** restent dans Star Detection.

La logique métier (vote, préférences, rareté, interface FR/EN) est identique à
l'upstream.

## Dépendance

Star Detection **nécessite Circus OCR** installé (via Circus Launcher). Au
lancement de la surveillance, le service est démarré automatiquement s'il ne
tourne pas. Découverte du service : `CIRCUS_OCR_URL` (défaut
`http://127.0.0.1:8765`).

## Architecture du fork

```
app/
  screen_monitor.py      <- app principale (GUI tkinter, vote, CSV, correction OCR)
  circus_ocr_client.py   <- client du service Circus OCR (HTTP + regions.json)
  liste.csv              <- base signatures radar -> minerais
```

## Crédits

- Outil original : **Kainan & Claude AI** — https://github.com/kainann/StarDetection
- [EasyOCR](https://github.com/JaidedAI/EasyOCR) (via Circus OCR)
- Fork Launcher : intégration Circus OCR
