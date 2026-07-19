# DTLclock v1.1.3

DTLclock transforme votre ordinateur en horloge comtoise : à chaque heure pleine,
le programme joue autant de coups que l'heure affichée. À minuit, il joue 24 coups.

Deux sonorités sont proposées :

- une comtoise du XIXe siècle, au timbre grave ;
- une comtoise du XVIIIe siècle, au timbre métallique.

## Installation

DTLclock nécessite Python 3.10 ou une version plus récente.

Dans PowerShell, placez-vous dans le dossier du projet puis exécutez :

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Utilisation

Lancez le programme avec :

```powershell
python DTLclock.py
```

Choisissez un carillon, puis cliquez sur **Démarrer la Comtoise**. Vous pouvez
changer de carillon pendant que le programme fonctionne ; le nouveau choix sera
utilisé à la prochaine sonnerie. Le bouton **Arrêter** suspend la surveillance.
Le bouton **Tester le carillon** joue immédiatement un seul coup du carillon
sélectionné, sans démarrer la surveillance horaire.

La fenêtre est redimensionnable. Le bouton d'agrandissement de Windows permet de
l'adapter à un écran portrait, notamment en 1080 × 1920. L'illustration occupe
toute la fenêtre en conservant ses proportions. Les choix de carillon sont
superposés en bas à gauche et les commandes en bas à droite, dans les espaces
libres autour de l'horloge.

Le balancier est redessiné par l'application et oscille automatiquement au-dessus
de l'image. Son animation reste fluide et conserve sa position lorsque la fenêtre
est redimensionnée. Son axe de rotation est placé sous l'arche supérieure de
l'ouverture, avec une longue tige descendant entre les deux poids.

Le programme doit rester ouvert pour pouvoir sonner. Les fichiers `horloge.png`,
`bell1700.wav` et `bell1800.wav` doivent rester dans le même dossier que
`DTLclock.py`.

La version Windows autonome se trouve dans `dist\DTLclock.exe`. Elle intègre les
images, les sons et les dépendances : Python n'est pas nécessaire pour l'utiliser.

## Contenu

- `DTLclock.py` : application Tkinter ;
- `horloge.png` : illustration de l'horloge ;
- `bell1700.wav` : carillon métallique ;
- `bell1800.wav` : carillon grave ;
- `requirements.txt` : dépendances Python.

## Version

Version actuelle : **1.1.3**.
