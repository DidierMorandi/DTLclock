# DTLclock v1.6.0

> A realistic French grandfather clock for Windows.

## Features

- Animated pendulum
- Mechanical second hand
- Adjustable tick-tock
- XVIIIth & XIXth century chimes
- Automatic chimes
- Silent hours
- Responsive interface
- Stand-alone executable

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python DTLclock.py
```

Build:

```powershell
python -m PyInstaller DTLclock.spec
```

## Version 1.6.0

- mechanical second hand synchronization;
- silent-hours panel;
- redesigned interface;
- bug fixes.
