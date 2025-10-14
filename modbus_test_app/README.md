# Modbus Test App

PyQt5-Anwendung zum Starten zweier Modbus-TCP-Testserver (Battery & Master) und zum direkten Lesen/Schreiben von Registern.

## Voraussetzungen

* Python 3.11 oder neuer
* Virtuelle Umgebung (empfohlen)

## Installation

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Starten

```bash
python app.py
```

Beim ersten Start wird automatisch eine `config.json` unter `resources/` erzeugt. Über das Menü **Einstellungen → Konfiguration…** lassen sich Host, Port und Unit-ID beider Server anpassen. Bei laufenden Servern führt das Speichern zu einem automatischen Neustart.

## Hinweise

* Standardports: Battery 5020, Master 502. Für Ports <1024 sind unter Linux/macOS erhöhte Rechte nötig – wählen Sie in diesem Fall besser einen Port >1024.
* Die GUI stellt pro Server Register-Typ, Adresse, Anzahl sowie Werte bereit. Mehrfachwerte werden komma-separiert eingegeben.
* Logging-Ausgaben erscheinen im unteren Bereich der Anwendung. Der Log-Level lässt sich per Combobox ändern.
