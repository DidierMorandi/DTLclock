# DTLclock v1.6.0

> A realistic French grandfather clock for Windows.

## Features

- Animated pendulum
- Mechanical second hand synchronized with the pendulum
- Tick-tock with independent start, stop, and volume controls
- Two selectable chimes: 18th-century metallic and 19th-century deep tone
- Automatic hourly and half-hour chimes
- Chime test
- Silent hours configured through a hidden compartment
- Responsive interface
- Stand-alone Windows executable built with PyInstaller

## Installation

DTLclock requires Python 3.10 or later.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python DTLclock.py
```

## Operation

The pendulum drives the clock mechanism. The second hand advances at each
mechanical beat, even when the tick-tock sound is stopped, muted, or unavailable.
When enabled, the tick or tock is played at the corresponding beat.

At each full hour, the selected chime strikes the time on a 12-hour cycle. At
each half-hour, DTLclock plays one fixed 18th-century chime strike. Automatic
chimes can be disabled globally. The hidden button in the clock's central rose
opens a panel where individual silent hours can be selected; a selected hour
silences both its full-hour and half-hour strikes.

`DTLclock.ini` contains the visual and mechanical settings. If it is missing or
a value is invalid, the application uses its built-in defaults.

## Windows build

Install PyInstaller in the active virtual environment, then build the executable:

```powershell
python -m pip install PyInstaller
python -m PyInstaller DTLclock.spec
```

The generated executable is written to `dist\DTLclock.exe`.

## Version 1.6.0

- mechanical second hand synchronized with the pendulum;
- silent-hours panel;
- redesigned interface;
- bug fixes.
