# DTLclock v1.6.0

> Une horloge comtoise mécanique réaliste pour Windows.

## Fonctionnalités

- Balancier animé
- Trotteuse synchronisée avec le tic-tac
- Tic-tac avec volume indépendant
- Deux carillons (XVIIIe / XIXe siècle)
- Carillon automatique
- Essai du carillon
- Heures silencieuses via compartiment secret
- Interface adaptative
- Version autonome PyInstaller

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python DTLclock.py
```

Compilation :

```powershell
python -m PyInstaller DTLclock.spec
```

## Fonctionnement

Le balancier entraîne l'échappement. À chaque battement, la trotteuse avance d'un cran exactement au moment où le tic ou le tac est joué.

## Historique

### 1.6.0

- synchronisation mécanique de la trotteuse ;
- panneau des heures silencieuses ;
- nouvelle interface ;
- nombreux correctifs.
