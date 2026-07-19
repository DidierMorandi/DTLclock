# DTLclock v1.6.0

> Une horloge comtoise mécanique réaliste pour Windows.

## Fonctionnalités

- Balancier animé
- Trotteuse mécanique synchronisée avec le balancier
- Tic-tac avec commandes indépendantes de démarrage, d'arrêt et de volume
- Deux carillons au choix : XVIIIe siècle métallique et XIXe siècle grave
- Carillon automatique aux heures et aux demi-heures
- Essai du carillon
- Heures silencieuses configurables via un compartiment secret
- Interface adaptative
- Exécutable Windows autonome produit avec PyInstaller

## Installation

DTLclock nécessite Python 3.10 ou une version plus récente.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python DTLclock.py
```

## Fonctionnement

Le balancier entraîne le mécanisme de l'horloge. La trotteuse avance à chaque
battement mécanique, même lorsque le son du tic-tac est arrêté, muet ou
indisponible. Quand le son est actif, le tic ou le tac est joué au battement
correspondant.

À chaque heure pleine, le carillon sélectionné sonne l'heure sur un cycle de
12 heures. À chaque demi-heure, DTLclock joue un coup fixe du carillon XVIIIe.
Le carillon automatique peut être entièrement désactivé. Le bouton caché dans
la rose centrale de l'horloge ouvre un panneau permettant de choisir les heures
silencieuses ; une heure cochée neutralise sa sonnerie à l'heure pleine et à la
demi-heure.

Le fichier `DTLclock.ini` contient les paramètres visuels et mécaniques. S'il
est absent ou si une valeur est incorrecte, l'application utilise ses valeurs
intégrées par défaut.

## Compilation Windows

Installez PyInstaller dans l'environnement virtuel actif, puis construisez
l'exécutable :

```powershell
python -m pip install PyInstaller
python -m PyInstaller DTLclock.spec
```

L'exécutable produit se trouve dans `dist\DTLclock.exe`.

## Historique

### 1.6.0

- synchronisation mécanique de la trotteuse avec le balancier ;
- panneau des heures silencieuses ;
- nouvelle interface ;
- nombreux correctifs.
