"""DTLclock — une horloge comtoise qui sonne à chaque heure pleine."""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import threading
import time
import tkinter as tk
import wave
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from tkinter import font as tkfont

from PIL import Image, ImageTk
from playsound import playsound


VERSION = "1.3.0"
BASE_DIR = (
    Path(sys._MEIPASS)  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    else Path(__file__).resolve().parent
)
IMAGE_PATH = BASE_DIR / "horloge.png"
BOB_IMAGE_PATH = BASE_DIR / "balancier.png"
BELLS = {
    "1700": BASE_DIR / "bell1700.wav",
    "1800": BASE_DIR / "bell1800.wav",
}
TICK_WAV_PATH = BASE_DIR / "tick.wav"
TOCK_WAV_PATH = BASE_DIR / "tock.wav"

root = tk.Tk()
root.title(f"DTLclock v{VERSION}")
root.geometry("500x900")
root.minsize(420, 760)
root.resizable(True, True)

BACKGROUND_COLOR = "#efe4b0"
TEXT_COLOR = "#17130c"
root.configure(background=BACKGROUND_COLOR)

stop_event = threading.Event()
audio_lock = threading.Lock()
tick_lock = threading.Lock()
monitoring_thread: threading.Thread | None = None
selected_bell = "1800"
carillon_enabled = True
silent_hours: set[int] = set()

# Le tic-tac est décoratif et non essentiel à l'horloge : s'il manque, on le
# désactive silencieusement plutôt que d'empêcher le programme de démarrer.
ticking_enabled = TICK_WAV_PATH.is_file() and TOCK_WAV_PATH.is_file()
_next_tick_index = 0
tick_volume_percent = 20.0
pendulum_running = True
ticking_active = True if ticking_enabled else False

# Copies temporaires, mises à l'échelle du volume choisi ; c'est ce qui est
# réellement joué (les fichiers d'origine ne sont jamais modifiés). Un nom de
# fichier UNIQUE est généré à chaque changement de volume : sur Windows,
# `playsound` (MCI) peut resservir le contenu déjà chargé pour un nom de
# fichier réutilisé, ce qui donnait l'impression que le volume ne changeait
# jamais. Un nom différent à chaque fois force une lecture fraîche.
_volume_generation = 0
current_tick_path: Path | None = None
current_tock_path: Path | None = None

_tick_raw_samples: list[int] = []
_tock_raw_samples: list[int] = []
_tick_wav_params: wave._wave_params | None = None
_tock_wav_params: wave._wave_params | None = None


def load_pcm16_mono(path: Path) -> tuple[list[int], wave._wave_params]:
    """Charge un WAV mono 16 bits en mémoire (liste d'échantillons signés)."""
    with wave.open(str(path), "rb") as wav_file:
        params = wav_file.getparams()
        raw_bytes = wav_file.readframes(params.nframes)
    sample_count = len(raw_bytes) // 2
    samples = list(struct.unpack(f"<{sample_count}h", raw_bytes))
    return samples, params


def write_pcm16_mono(path: Path, samples: list[int], params: wave._wave_params) -> None:
    """Écrit une liste d'échantillons dans un WAV mono 16 bits."""
    with wave.open(str(path), "w") as wav_file:
        wav_file.setparams(params)
        wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))


if ticking_enabled:
    try:
        _tick_raw_samples, _tick_wav_params = load_pcm16_mono(TICK_WAV_PATH)
        _tock_raw_samples, _tock_wav_params = load_pcm16_mono(TOCK_WAV_PATH)
    except (wave.Error, OSError):
        ticking_enabled = False


def apply_tick_volume(volume_percent: float) -> None:
    """Régénère, sous un nom inédit, les copies de tick/tock au volume demandé."""
    global current_tick_path, current_tock_path, _volume_generation

    if not ticking_enabled:
        return

    factor = max(0.0, min(1.0, volume_percent / 100))
    _volume_generation += 1
    temp_dir = Path(tempfile.gettempdir())
    new_tick_path = temp_dir / f"dtlclock_tick_{_volume_generation}.wav"
    new_tock_path = temp_dir / f"dtlclock_tock_{_volume_generation}.wav"

    try:
        for raw_samples, params, out_path in (
            (_tick_raw_samples, _tick_wav_params, new_tick_path),
            (_tock_raw_samples, _tock_wav_params, new_tock_path),
        ):
            scaled = [
                max(-32768, min(32767, round(sample * factor))) for sample in raw_samples
            ]
            write_pcm16_mono(out_path, scaled, params)
    except OSError:
        return  # on garde les fichiers précédents plutôt que de planter

    old_tick_path, old_tock_path = current_tick_path, current_tock_path
    current_tick_path, current_tock_path = new_tick_path, new_tock_path

    # Nettoyage au mieux : si l'ancien fichier est encore en cours de lecture,
    # on laisse tomber sans bruit, le système le nettoiera au redémarrage.
    for old_path in (old_tick_path, old_tock_path):
        if old_path is not None:
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass


if ticking_enabled:
    apply_tick_volume(tick_volume_percent)


def set_status(text: str) -> None:
    """Programme une mise à jour sûre de l'interface depuis un thread."""
    try:
        root.after(0, status.set, text)
    except (RuntimeError, tk.TclError):
        pass


def choose_bell() -> None:
    """Mémorise le choix de l'interface dans une valeur lisible en arrière-plan."""
    global selected_bell
    selected_bell = carillon.get()


def set_carillon_enabled() -> None:
    """Active ou neutralise les sonneries automatiques."""
    global carillon_enabled

    carillon_enabled = bool(carillon_enabled_var.get())
    status.set("Carillon actif." if carillon_enabled else "Carillon coupé.")


def update_silent_hours() -> None:
    """Reconstruit l'ensemble des heures pendant lesquelles le carillon se tait."""
    global silent_hours

    silent_hours = {
        hour for hour, variable in enumerate(silent_hour_vars) if variable.get()
    }


def ring(hour: int | None = None) -> None:
    """Joue autant de coups que l'heure courante (24 coups à minuit)."""
    current_hour = datetime.now().hour if hour is None else hour
    strikes = current_hour % 12 or 12
    wav_path = BELLS[selected_bell]

    with audio_lock:
        if stop_event.is_set():
            return

        for strike_number in range(strikes):
            if stop_event.is_set() or not carillon_enabled:
                return
            playsound(str(wav_path))
            if strike_number < strikes - 1 and stop_event.wait(1.8):
                return

        set_status("Carillon actif.")


def ring_half_hour() -> None:
    """Joue un unique coup de bell1700.wav pour marquer la demi-heure.

    Ce timbre est fixe (indépendant du carillon choisi pour l'heure pleine),
    à la manière du petit coup distinct que sonnent certaines comtoises entre
    deux heures pleines.
    """
    wav_path = BELLS["1700"]

    with audio_lock:
        if stop_event.is_set() or not carillon_enabled:
            return

        playsound(str(wav_path))
        set_status("Carillon actif.")


def run_bell_test(wav_path: Path) -> None:
    """Joue un coup d'essai dans un thread d'arrière-plan."""
    error_message: str | None = None

    if not audio_lock.acquire(blocking=False):
        final_status = "Un carillon est déjà en cours"
    else:
        try:
            set_status("Test du carillon en cours…")
            playsound(str(wav_path))
        except Exception as error:
            error_message = str(error)
        finally:
            audio_lock.release()

        is_monitoring = (
            monitoring_thread is not None
            and monitoring_thread.is_alive()
            and not stop_event.is_set()
        )
        final_status = (
            "Carillon actif."
            if is_monitoring and carillon_enabled
            else "Carillon coupé."
            if is_monitoring
            else "Carillon inactif."
        )

    def finish_test() -> None:
        test_button.configure(state="normal")
        status.set(final_status)
        if error_message is not None:
            messagebox.showerror(
                "DTLclock", f"Impossible de jouer le carillon.\n\n{error_message}"
            )

    try:
        root.after(0, finish_test)
    except (RuntimeError, tk.TclError):
        pass


def test_bell() -> None:
    """Lance un test d'un seul coup sans bloquer l'interface."""
    choose_bell()
    test_button.configure(state="disabled")
    threading.Thread(
        target=run_bell_test,
        args=(BELLS[selected_bell],),
        daemon=True,
    ).start()


def play_tick_sound(wav_path: Path) -> None:
    """Joue un tic ou un tac dans un thread d'arrière-plan.

    Verrou non bloquant dédié (indépendant de `audio_lock`) : un tic-tac ne
    doit jamais attendre ni bloquer la sonnerie des heures, et inversement.
    Si un appel précédent traîne encore, on saute simplement ce battement
    plutôt que d'empiler les threads.
    """
    if not tick_lock.acquire(blocking=False):
        return
    try:
        playsound(str(wav_path))
    except Exception:
        pass
    finally:
        tick_lock.release()


def fire_tick() -> None:
    """Déclenche un tic (ou un tac, en alternance) et programme le suivant."""
    global _next_tick_index

    if ticking_active and tick_volume_percent > 0 and current_tick_path is not None:
        wav_path = current_tick_path if _next_tick_index % 2 == 0 else current_tock_path
        threading.Thread(target=play_tick_sound, args=(wav_path,), daemon=True).start()
    _next_tick_index += 1
    schedule_next_tick()


def schedule_next_tick() -> None:
    """Programme le prochain tic-tac pile quand le balancier atteint le bout de sa course.

    Le calage se fait sur l'instant absolu (`pendulum_start_time` + un quart
    de période + N demi-périodes) plutôt que par un simple `after(1000, ...)`
    répété, pour éviter toute dérive progressive entre le son et le mouvement
    visuel du balancier. Le quart de période supplémentaire décale le repère
    du passage au centre (vitesse maximale) vers l'extrémité de l'oscillation
    (vitesse nulle), instant où le tic-tac doit se faire entendre.
    """
    half_period_seconds = PENDULUM_PERIOD_SECONDS / 2
    quarter_period_seconds = PENDULUM_PERIOD_SECONDS / 4
    target_time = (
        pendulum_start_time
        + quarter_period_seconds
        + _next_tick_index * half_period_seconds
    )
    delay_ms = max(0, round((target_time - time.perf_counter()) * 1000))
    try:
        root.after(delay_ms, fire_tick)
    except (RuntimeError, tk.TclError):
        pass


def monitor() -> None:
    """Surveille l'horloge : sonnerie complète à l'heure, un coup à la demie."""
    last_ring: tuple[int, int, int, int] | None = None
    last_half_ring: tuple[int, int, int, int] | None = None

    while not stop_event.is_set():
        now = datetime.now()
        current_slot = (now.year, now.month, now.day, now.hour)

        if (
            carillon_enabled
            and now.hour not in silent_hours
            and now.minute == 0
            and current_slot != last_ring
        ):
            last_ring = current_slot
            try:
                ring(now.hour)
            except Exception as error:
                set_status("Erreur pendant la lecture du carillon")
                try:
                    root.after(
                        0,
                        lambda details=str(error): messagebox.showerror(
                            "DTLclock", f"Impossible de jouer le carillon.\n\n{details}"
                        ),
                    )
                except (RuntimeError, tk.TclError):
                    pass
        elif (
            carillon_enabled
            and now.hour not in silent_hours
            and now.minute == 30
            and current_slot != last_half_ring
        ):
            last_half_ring = current_slot
            try:
                ring_half_hour()
            except Exception as error:
                set_status("Erreur pendant la lecture du carillon")
                try:
                    root.after(
                        0,
                        lambda details=str(error): messagebox.showerror(
                            "DTLclock", f"Impossible de jouer le carillon.\n\n{details}"
                        ),
                    )
                except (RuntimeError, tk.TclError):
                    pass

        stop_event.wait(1)


def start_carillon_monitor() -> None:
    """Démarre la surveillance automatique du carillon."""
    global monitoring_thread

    if monitoring_thread is not None and monitoring_thread.is_alive():
        return

    choose_bell()
    stop_event.clear()
    monitoring_thread = threading.Thread(target=monitor, daemon=True)
    monitoring_thread.start()

    status.set("Carillon actif." if carillon_enabled else "Carillon coupé.")


def stop_carillon_monitor() -> None:
    """Arrête la surveillance automatique du carillon."""
    stop_event.set()
    status.set("Carillon inactif.")



def start_clock() -> None:
    """Relance l'horloge : balancier et tic-tac."""
    global pendulum_running, pendulum_start_time

    if not pendulum_running:
        pendulum_start_time = time.perf_counter()
        pendulum_running = True
        clock_start_button.configure(state="disabled")
        clock_stop_button.configure(state="normal")

    update_clock_hands()
    start_ticking()


def stop_clock() -> None:
    """Arrête complètement l'horloge et place le balancier à la verticale."""
    global pendulum_running

    pendulum_running = False
    update_pendulum(0.0)
    stop_ticking()
    clock_start_button.configure(state="normal")
    clock_stop_button.configure(state="disabled")


def start_ticking() -> None:
    """Active le son du tic-tac."""
    global ticking_active

    if not ticking_enabled:
        return

    ticking_active = True
    tick_start_button.configure(state="disabled")
    tick_stop_button.configure(state="normal")


def stop_ticking() -> None:
    """Coupe le son du tic-tac sans arrêter le balancier."""
    global ticking_active

    ticking_active = False
    tick_start_button.configure(state="normal")
    tick_stop_button.configure(state="disabled")


def close() -> None:
    stop_event.set()
    root.destroy()


try:
    with Image.open(IMAGE_PATH) as loaded_image:
        source_image = loaded_image.copy()
        clock_image = source_image.resize((500, 889), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(clock_image)
except (FileNotFoundError, OSError) as error:
    messagebox.showerror("DTLclock", f"Impossible de charger horloge.png.\n\n{error}")
    root.destroy()
    raise SystemExit(1) from error

try:
    with Image.open(BOB_IMAGE_PATH) as loaded_bob_image:
        source_bob_image = loaded_bob_image.convert("RGBA")
except (FileNotFoundError, OSError) as error:
    messagebox.showerror("DTLclock", f"Impossible de charger balancier.png.\n\n{error}")
    root.destroy()
    raise SystemExit(1) from error

canvas = tk.Canvas(
    root,
    background=BACKGROUND_COLOR,
    highlightthickness=0,
)
canvas.place(x=0, y=0, relwidth=1, relheight=1)
background_item = canvas.create_image(250, 450, image=photo)

# Coordonnées exprimées dans l'image source 1080 × 1920.
PENDULUM_PIVOT_X = 550
PENDULUM_PIVOT_Y = 950
PENDULUM_ROD_LENGTH = 395
PENDULUM_BOB_RADIUS = 74
PENDULUM_MAX_ANGLE = 7
PENDULUM_PERIOD_SECONDS = 2.0
PENDULUM_FRAME_DELAY_MS = 16
# Écartement latéral des deux branches parallèles fines de part et d'autre
# de la tige centrale (motif "gridiron"), en coordonnées de l'image source.
PENDULUM_SIDE_ROD_OFFSET = 9
# Position (fraction de la longueur visible de la tige, 0 = pivot,
# 1 = bord de la sphère) des petites traverses décoratives reliant les
# deux branches latérales.
PENDULUM_CROSSBAR_FRACTIONS = (0.28, 0.55, 0.82)

# Position du centre du cadran dans l'image source 1080 × 1920.
DIAL_CENTER_X = 550
DIAL_CENTER_Y = 540
HOUR_HAND_LENGTH = 86
MINUTE_HAND_LENGTH = 124
SECOND_HAND_LENGTH = 132
CLOCK_REFRESH_MS = 200

# Petite porte secrète du socle, exprimée dans le repère 1080 x 1920.
DOOR_LEFT = 650
DOOR_TOP = 1490
DOOR_RIGHT = 965
DOOR_BOTTOM = 1840
DOOR_KNOB_X = 925
DOOR_KNOB_Y = 1665
DOOR_ANIMATION_STEPS = 8

# Profils des aiguilles heure/minute façon "spatule Louis XV" : un contre-poids
# derrière le pivot, un renflement près du moyeu, puis une lame effilée qui se
# rouvre légèrement avant la pointe (silhouette classique des comtoises dorées).
# Chaque paire (distance le long de l'aiguille, demi-largeur) est exprimée dans
# les unités de l'image source ; les distances négatives sont le contre-poids.
HOUR_HAND_PROFILE = (
    (-20.0, 0.0),
    (-11.0, 4.6),
    (0.0, 7.6),
    (10.0, 9.2),
    (48.0, 3.6),
    (70.0, 2.4),
    (73.0, 5.0),
    (HOUR_HAND_LENGTH, 0.0),
)
MINUTE_HAND_PROFILE = (
    (-26.0, 0.0),
    (-14.0, 3.4),
    (0.0, 5.6),
    (14.0, 6.8),
    (70.0, 2.4),
    (102.0, 1.7),
    (106.0, 3.6),
    (MINUTE_HAND_LENGTH, 0.0),
)

rod_shadow = canvas.create_line(0, 0, 0, 0, fill="#241509", capstyle="round")
rod_highlight = canvas.create_line(0, 0, 0, 0, fill="#b98632", capstyle="round")

# Deux branches parallèles plus fines de part et d'autre de la tige
# centrale, façon balancier "gridiron", reliées par de petites traverses
# décoratives avec rivets.
rod_side_left_shadow = canvas.create_line(0, 0, 0, 0, fill="#241509", capstyle="round")
rod_side_left_highlight = canvas.create_line(0, 0, 0, 0, fill="#b98632", capstyle="round")
rod_side_right_shadow = canvas.create_line(0, 0, 0, 0, fill="#241509", capstyle="round")
rod_side_right_highlight = canvas.create_line(0, 0, 0, 0, fill="#b98632", capstyle="round")
crossbar_lines = [
    canvas.create_line(0, 0, 0, 0, fill="#8a5a22", capstyle="round")
    for _ in PENDULUM_CROSSBAR_FRACTIONS
]
crossbar_rivets = [
    canvas.create_oval(0, 0, 0, 0, fill="#c4973e", outline="")
    for _ in range(len(PENDULUM_CROSSBAR_FRACTIONS) * 2)
]

bob_image_item = canvas.create_image(0, 0, anchor="center")
pivot_outer = canvas.create_oval(0, 0, 0, 0, fill="#49300f", outline="", state="hidden")
pivot_inner = canvas.create_oval(0, 0, 0, 0, fill="#c4973e", outline="", state="hidden")

# Cache de la photo du balancier, tournée à l'angle courant. Reconstruit
# uniquement quand le diamètre change (redimensionnement de la fenêtre) ;
# à diamètre fixe, chaque angle (résolution 0,1°) n'est calculé qu'une fois.
_bob_cache_diameter: int | None = None
_bob_base_image: Image.Image | None = None
_bob_rotation_cache: dict[float, ImageTk.PhotoImage] = {}


def get_rotated_bob_photo(diameter: int, angle_degrees: float) -> ImageTk.PhotoImage:
    """Retourne l'image du balancier redimensionnée et tournée, avec cache."""
    global _bob_cache_diameter, _bob_base_image, _bob_rotation_cache

    if diameter != _bob_cache_diameter:
        _bob_cache_diameter = diameter
        _bob_base_image = source_bob_image.resize(
            (diameter, diameter), Image.Resampling.LANCZOS
        )
        _bob_rotation_cache = {}

    angle_key = round(angle_degrees, 1)
    cached_photo = _bob_rotation_cache.get(angle_key)
    if cached_photo is None:
        # Signe négatif : le balancier tourne dans le même sens que le
        # déplacement de la tige. Inverser si le rendu part à l'envers.
        rotated = _bob_base_image.rotate(
            -angle_key, resample=Image.BICUBIC, expand=True
        )
        cached_photo = ImageTk.PhotoImage(rotated)
        _bob_rotation_cache[angle_key] = cached_photo
    return cached_photo


# Aiguilles vivantes dessinées par-dessus le cadran de l'image. L'heure et la
# minute sont des polygones effilés (silhouette "spatule"), pas de simples
# traits, avec une ombre portée pour donner du relief sur le cadran peint.
hour_hand_shadow = canvas.create_polygon(0, 0, 0, 0, 0, 0, fill="#1a0f06", outline="")
hour_hand = canvas.create_polygon(
    0, 0, 0, 0, 0, 0, fill="#171008", outline="#caa14a", width=1
)
minute_hand_shadow = canvas.create_polygon(0, 0, 0, 0, 0, 0, fill="#1a0f06", outline="")
minute_hand = canvas.create_polygon(
    0, 0, 0, 0, 0, 0, fill="#171008", outline="#caa14a", width=1
)
second_hand = canvas.create_line(0, 0, 0, 0, fill="#8b1e14", capstyle="round")
dial_center_outer = canvas.create_oval(0, 0, 0, 0, fill="#3d260b", outline="")
dial_center_inner = canvas.create_oval(0, 0, 0, 0, fill="#c89a3c", outline="")

heading_font = tkfont.Font(root=root, family="Verdana", size=14, weight="bold")
text_font = tkfont.Font(root=root, family="Verdana", size=12)
button_font = tkfont.Font(root=root, family="Verdana", size=12)
status_font = tkfont.Font(root=root, family="Verdana", size=12, slant="italic")

last_image_size = (500, 889)
resize_job: str | None = None
_volume_write_job: str | None = None
image_scale = 500 / source_image.width
image_offset_x = 0.0
image_offset_y = (900 - 889) / 2
current_pendulum_angle = 0.0
pendulum_start_time = time.perf_counter()
door_open = False
door_animating = False
door_animation_step = 0


def update_pendulum(angle_degrees: float) -> None:
    """Dessine le balancier à l'angle demandé dans le repère de l'image."""
    global current_pendulum_angle

    current_pendulum_angle = angle_degrees
    angle_radians = math.radians(angle_degrees)
    pivot_x = image_offset_x + PENDULUM_PIVOT_X * image_scale
    pivot_y = image_offset_y + PENDULUM_PIVOT_Y * image_scale
    rod_length = PENDULUM_ROD_LENGTH * image_scale
    radius = PENDULUM_BOB_RADIUS * image_scale
    end_x = pivot_x + rod_length * math.sin(angle_radians)
    end_y = pivot_y + rod_length * math.cos(angle_radians)

    # La tige visible s'arrête au bord supérieur du balancier, pas à son
    # centre : `end_x, end_y` reste le centre du disque (utilisé pour le
    # positionner), mais le trait de tige n'est tracé que jusqu'à son bord.
    rod_visible_length = max(0.0, rod_length - radius)
    rod_end_x = pivot_x + rod_visible_length * math.sin(angle_radians)
    rod_end_y = pivot_y + rod_visible_length * math.cos(angle_radians)

    canvas.coords(rod_shadow, pivot_x, pivot_y, rod_end_x, rod_end_y)
    canvas.coords(rod_highlight, pivot_x, pivot_y, rod_end_x, rod_end_y)
    canvas.itemconfigure(rod_shadow, width=max(2, round(10 * image_scale)))
    canvas.itemconfigure(rod_highlight, width=max(1, round(4 * image_scale)))

    # Branches latérales : mêmes points pivot/extrémité que la tige
    # centrale, décalés perpendiculairement à la tige.
    perp_x = math.cos(angle_radians)
    perp_y = -math.sin(angle_radians)
    side_offset = PENDULUM_SIDE_ROD_OFFSET * image_scale

    left_pivot_x = pivot_x - perp_x * side_offset
    left_pivot_y = pivot_y - perp_y * side_offset
    left_end_x = rod_end_x - perp_x * side_offset
    left_end_y = rod_end_y - perp_y * side_offset
    right_pivot_x = pivot_x + perp_x * side_offset
    right_pivot_y = pivot_y + perp_y * side_offset
    right_end_x = rod_end_x + perp_x * side_offset
    right_end_y = rod_end_y + perp_y * side_offset

    canvas.coords(rod_side_left_shadow, left_pivot_x, left_pivot_y, left_end_x, left_end_y)
    canvas.coords(rod_side_left_highlight, left_pivot_x, left_pivot_y, left_end_x, left_end_y)
    canvas.coords(rod_side_right_shadow, right_pivot_x, right_pivot_y, right_end_x, right_end_y)
    canvas.coords(rod_side_right_highlight, right_pivot_x, right_pivot_y, right_end_x, right_end_y)
    canvas.itemconfigure(rod_side_left_shadow, width=max(1, round(5 * image_scale)))
    canvas.itemconfigure(rod_side_left_highlight, width=max(1, round(2 * image_scale)))
    canvas.itemconfigure(rod_side_right_shadow, width=max(1, round(5 * image_scale)))
    canvas.itemconfigure(rod_side_right_highlight, width=max(1, round(2 * image_scale)))

    # Petites traverses décoratives reliant les deux branches latérales,
    # avec un rivet à chaque extrémité.
    crossbar_width = max(1, round(3 * image_scale))
    rivet_radius = max(1.5, 3 * image_scale)
    for index, fraction in enumerate(PENDULUM_CROSSBAR_FRACTIONS):
        bar_x = pivot_x + rod_visible_length * fraction * math.sin(angle_radians)
        bar_y = pivot_y + rod_visible_length * fraction * math.cos(angle_radians)
        bar_left_x = bar_x - perp_x * side_offset
        bar_left_y = bar_y - perp_y * side_offset
        bar_right_x = bar_x + perp_x * side_offset
        bar_right_y = bar_y + perp_y * side_offset

        canvas.coords(crossbar_lines[index], bar_left_x, bar_left_y, bar_right_x, bar_right_y)
        canvas.itemconfigure(crossbar_lines[index], width=crossbar_width)
        canvas.coords(
            crossbar_rivets[2 * index],
            bar_left_x - rivet_radius,
            bar_left_y - rivet_radius,
            bar_left_x + rivet_radius,
            bar_left_y + rivet_radius,
        )
        canvas.coords(
            crossbar_rivets[2 * index + 1],
            bar_right_x - rivet_radius,
            bar_right_y - rivet_radius,
            bar_right_x + rivet_radius,
            bar_right_y + rivet_radius,
        )

    bob_diameter = max(2, round(radius * 2))
    # La sphère ne tourne plus sur elle-même : elle reste solidaire de la
    # tige en position (via end_x, end_y) mais garde une orientation fixe,
    # comme un vrai balancier de pendule.
    bob_photo = get_rotated_bob_photo(bob_diameter, 0.0)
    canvas.itemconfigure(bob_image_item, image=bob_photo)
    canvas.coords(bob_image_item, end_x, end_y)

    pivot_radius = max(2, 10 * image_scale)
    canvas.coords(
        pivot_outer,
        pivot_x - pivot_radius,
        pivot_y - pivot_radius,
        pivot_x + pivot_radius,
        pivot_y + pivot_radius,
    )
    inner_radius = pivot_radius * 0.55
    canvas.coords(
        pivot_inner,
        pivot_x - inner_radius,
        pivot_y - inner_radius,
        pivot_x + inner_radius,
        pivot_y + inner_radius,
    )


def animate_pendulum() -> None:
    """Anime le balancier, ou le maintient vertical lorsque l'horloge est arrêtée."""
    if pendulum_running:
        elapsed = time.perf_counter() - pendulum_start_time
        angle = PENDULUM_MAX_ANGLE * math.sin(
            2 * math.pi * elapsed / PENDULUM_PERIOD_SECONDS
        )
        update_pendulum(angle)
    elif current_pendulum_angle != 0.0:
        update_pendulum(0.0)

    root.after(PENDULUM_FRAME_DELAY_MS, animate_pendulum)



def hand_polygon_points(
    center_x: float,
    center_y: float,
    angle: float,
    profile: tuple[tuple[float, float], ...],
    scale: float,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> list[float]:
    """Calcule le contour (liste plate x, y) d'une aiguille effilée.

    `profile` donne, pour chaque section, la distance le long de l'aiguille
    (négative pour le contre-poids derrière le pivot) et la demi-largeur à
    cet endroit. Le contour est construit en suivant les bords gauche puis
    droit du profil, ce qui donne une lame pleine plutôt qu'un simple trait.
    """
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    perp_x, perp_y = -sin_a, cos_a
    left_side: list[float] = []
    right_side: list[float] = []

    for distance, half_width in profile:
        d = distance * scale
        w = half_width * scale
        point_x = center_x + offset_x + d * cos_a
        point_y = center_y + offset_y + d * sin_a
        left_side.extend((point_x + perp_x * w, point_y + perp_y * w))
        right_side.extend((point_x - perp_x * w, point_y - perp_y * w))

    right_side_pairs = [
        right_side[i : i + 2] for i in range(0, len(right_side), 2)
    ]
    right_side_pairs.reverse()

    points = left_side[:]
    for pair in right_side_pairs:
        points.extend(pair)
    return points


def update_clock_hands(now: datetime | None = None) -> None:
    """Affiche l'heure courante sur le cadran analogique."""
    current = datetime.now() if now is None else now
    center_x = image_offset_x + DIAL_CENTER_X * image_scale
    center_y = image_offset_y + DIAL_CENTER_Y * image_scale

    hour_angle = math.radians(
        30 * (current.hour % 12) + 0.5 * current.minute + current.second / 120 - 90
    )
    minute_angle = math.radians(
        6 * current.minute + 0.1 * current.second + current.microsecond / 10_000_000 - 90
    )
    second_angle = math.radians(
        6 * current.second + current.microsecond / 166_666.6667 - 90
    )

    def endpoint(length: float, angle: float) -> tuple[float, float]:
        scaled_length = length * image_scale
        return (
            center_x + scaled_length * math.cos(angle),
            center_y + scaled_length * math.sin(angle),
        )

    second_x, second_y = endpoint(SECOND_HAND_LENGTH, second_angle)
    shadow_offset = max(1.0, 2.0 * image_scale)

    canvas.coords(
        hour_hand_shadow,
        *hand_polygon_points(
            center_x, center_y, hour_angle, HOUR_HAND_PROFILE, image_scale,
            offset_x=shadow_offset, offset_y=shadow_offset,
        ),
    )
    canvas.coords(
        hour_hand,
        *hand_polygon_points(center_x, center_y, hour_angle, HOUR_HAND_PROFILE, image_scale),
    )
    canvas.itemconfigure(hour_hand, width=max(1, round(1.4 * image_scale)))

    canvas.coords(
        minute_hand_shadow,
        *hand_polygon_points(
            center_x, center_y, minute_angle, MINUTE_HAND_PROFILE, image_scale,
            offset_x=shadow_offset, offset_y=shadow_offset,
        ),
    )
    canvas.coords(
        minute_hand,
        *hand_polygon_points(center_x, center_y, minute_angle, MINUTE_HAND_PROFILE, image_scale),
    )
    canvas.itemconfigure(minute_hand, width=max(1, round(1.2 * image_scale)))

    # Une trotteuse discrète rend visible le fait que l'heure est réellement vivante.
    tail_length = 24 * image_scale
    tail_x = center_x - tail_length * math.cos(second_angle)
    tail_y = center_y - tail_length * math.sin(second_angle)
    canvas.coords(second_hand, tail_x, tail_y, second_x, second_y)
    canvas.itemconfigure(second_hand, width=max(1, round(2 * image_scale)))

    outer_radius = max(3.0, 12 * image_scale)
    inner_radius = outer_radius * 0.48
    canvas.coords(
        dial_center_outer,
        center_x - outer_radius,
        center_y - outer_radius,
        center_x + outer_radius,
        center_y + outer_radius,
    )
    canvas.coords(
        dial_center_inner,
        center_x - inner_radius,
        center_y - inner_radius,
        center_x + inner_radius,
        center_y + inner_radius,
    )

    # Maintient les aiguilles au-dessus de l'image, même après un redimensionnement.
    for item in (
        hour_hand_shadow,
        minute_hand_shadow,
        hour_hand,
        minute_hand,
        second_hand,
        dial_center_outer,
        dial_center_inner,
    ):
        canvas.tag_raise(item)


def animate_clock_hands() -> None:
    """Actualise les aiguilles uniquement lorsque l'horloge fonctionne."""
    if pendulum_running:
        update_clock_hands()

    root.after(CLOCK_REFRESH_MS, animate_clock_hands)


def position_secret_door() -> None:
    """Replace la porte, son bouton et le tableau intérieur après redimensionnement."""
    left = image_offset_x + DOOR_LEFT * image_scale
    top = image_offset_y + DOOR_TOP * image_scale
    right = image_offset_x + DOOR_RIGHT * image_scale
    bottom = image_offset_y + DOOR_BOTTOM * image_scale

    # Lors de l'ouverture, la porte se replie visuellement vers sa charnière gauche.
    progress = door_animation_step / DOOR_ANIMATION_STEPS
    visible_right = right - (right - left) * progress
    canvas.coords(secret_door, left, top, visible_right, bottom)
    canvas.itemconfigure(secret_door, width=max(1, round(3 * image_scale)))

    knob_x = image_offset_x + DOOR_KNOB_X * image_scale
    knob_y = image_offset_y + DOOR_KNOB_Y * image_scale
    knob_x = knob_x - (knob_x - left) * progress
    knob_radius = max(3.0, 10 * image_scale)
    canvas.coords(
        secret_door_knob,
        knob_x - knob_radius,
        knob_y - knob_radius,
        knob_x + knob_radius,
        knob_y + knob_radius,
    )
    canvas.itemconfigure(secret_door_knob, width=max(1, round(2 * image_scale)))

    panel_width = max(235, round((DOOR_RIGHT - DOOR_LEFT) * image_scale))
    panel_height = max(270, round((DOOR_BOTTOM - DOOR_TOP) * image_scale))
    panel_x = min(left, max(5.0, root.winfo_width() - panel_width - 5.0))
    schedule_panel.place(
        x=panel_x,
        y=top,
        width=panel_width,
        height=panel_height,
    )


def finish_door_animation(opening: bool) -> None:
    """Termine l'animation et révèle ou masque le tableau des 24 heures."""
    global door_open, door_animating

    door_open = opening
    door_animating = False
    if opening:
        schedule_panel.lift()
        schedule_panel.place_configure()
    else:
        schedule_panel.lower()
        schedule_panel.place_forget()
    position_secret_door()


def animate_secret_door(opening: bool) -> None:
    """Anime discrètement l'ouverture ou la fermeture de la porte du socle."""
    global door_animation_step

    if opening:
        door_animation_step += 1
    else:
        door_animation_step -= 1

    door_animation_step = max(0, min(DOOR_ANIMATION_STEPS, door_animation_step))
    position_secret_door()

    target = DOOR_ANIMATION_STEPS if opening else 0
    if door_animation_step == target:
        finish_door_animation(opening)
    else:
        root.after(22, lambda: animate_secret_door(opening))


def toggle_secret_door(_event: tk.Event[tk.Misc] | None = None) -> None:
    """Ouvre ou referme la petite porte secrète du socle."""
    global door_animating

    if door_animating:
        return
    door_animating = True
    if not door_open:
        schedule_panel.place(
            x=image_offset_x + DOOR_LEFT * image_scale,
            y=image_offset_y + DOOR_TOP * image_scale,
        )
        schedule_panel.lower()
    animate_secret_door(not door_open)


def set_all_silent_hours(value: bool) -> None:
    """Coche ou décoche les 24 heures en une seule opération."""
    for variable in silent_hour_vars:
        variable.set(value)
    update_silent_hours()


def resize_clock_image() -> None:
    """Ajuste l'image à la fenêtre tout en conservant ses proportions."""
    global photo, last_image_size, resize_job
    global image_scale, image_offset_x, image_offset_y

    resize_job = None
    available_width = max(root.winfo_width(), 1)
    available_height = max(root.winfo_height(), 1)
    scale = min(
        available_width / source_image.width,
        available_height / source_image.height,
    )
    target_size = (
        max(1, round(source_image.width * scale)),
        max(1, round(source_image.height * scale)),
    )

    if min(target_size) < 40:
        return

    if target_size != last_image_size:
        resized_image = source_image.resize(target_size, Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(resized_image)
        canvas.itemconfigure(background_item, image=photo)
        last_image_size = target_size

    image_scale = target_size[0] / source_image.width
    image_offset_x = (available_width - target_size[0]) / 2
    image_offset_y = (available_height - target_size[1]) / 2
    canvas.coords(
        background_item,
        image_offset_x + target_size[0] / 2,
        image_offset_y + target_size[1] / 2,
    )
    update_pendulum(current_pendulum_angle)
    update_clock_hands()
    position_secret_door()

    interface_scale = max(
        0.65,
        min(available_width / 1080, available_height / 1920),
    )
    heading_font.configure(size=max(12, round(14 * interface_scale)))
    text_font.configure(size=max(10, round(12 * interface_scale)))
    button_font.configure(size=max(10, round(12 * interface_scale)))
    status_font.configure(size=max(10, round(12 * interface_scale)))


def schedule_image_resize(_event: tk.Event[tk.Misc]) -> None:
    """Regroupe les événements rapides produits pendant un redimensionnement."""
    global resize_job

    if resize_job is not None:
        root.after_cancel(resize_job)
    resize_job = root.after(80, resize_clock_image)


def on_tick_volume_change(value: str) -> None:
    """Met à jour le volume du tic-tac, en différant l'écriture du fichier.

    Le curseur peut envoyer de nombreux événements pendant qu'on le fait
    glisser ; on ne régénère les WAV mis à l'échelle que 120 ms après le
    dernier mouvement, pour ne pas écrire sur disque en continu.
    """
    global tick_volume_percent, _volume_write_job

    tick_volume_percent = float(value)
    if _volume_write_job is not None:
        root.after_cancel(_volume_write_job)
    _volume_write_job = root.after(
        120, lambda: apply_tick_volume(tick_volume_percent)
    )


root.bind("<Configure>", schedule_image_resize)

left_controls = tk.Frame(root, background=BACKGROUND_COLOR)
left_controls.place(relx=0.02, rely=0.985, anchor="sw")

button_options = {
    "font": button_font,
    "foreground": TEXT_COLOR,
    "background": BACKGROUND_COLOR,
    "activebackground": BACKGROUND_COLOR,
    "activeforeground": "#9a7418",
    "borderwidth": 0,
    "highlightthickness": 0,
    "relief": "flat",
    "cursor": "hand2",
    "anchor": "w",
    "padx": 0,
    "pady": 0,
}


def add_hover_effect(button: tk.Button) -> None:
    """Donne aux commandes l'apparence de liens discrets au survol."""
    button.bind(
        "<Enter>",
        lambda _event, widget=button: widget.configure(foreground="#9a7418"),
    )
    button.bind(
        "<Leave>",
        lambda _event, widget=button: widget.configure(foreground=TEXT_COLOR),
    )


# --- Horloge ---------------------------------------------------------------
tk.Label(
    left_controls,
    text="Horloge",
    font=heading_font,
    foreground=TEXT_COLOR,
    background=BACKGROUND_COLOR,
).pack(anchor="w", pady=(0, 1))

clock_start_button = tk.Button(
    left_controls,
    text="Démarrer",
    command=start_clock,
    state="disabled",
    **button_options,
)
clock_start_button.pack(anchor="w", fill="x")
add_hover_effect(clock_start_button)

clock_stop_button = tk.Button(
    left_controls,
    text="Arrêter",
    command=stop_clock,
    **button_options,
)
clock_stop_button.pack(anchor="w", fill="x")
add_hover_effect(clock_stop_button)

# --- Tic-tac ---------------------------------------------------------------
tk.Label(
    left_controls,
    text="Tic-Tac",
    font=heading_font,
    foreground=TEXT_COLOR,
    background=BACKGROUND_COLOR,
).pack(anchor="w", pady=(12, 1))

tick_start_button = tk.Button(
    left_controls,
    text="Démarrer",
    command=start_ticking,
    state="disabled" if ticking_active else "normal",
    **button_options,
)
tick_start_button.pack(anchor="w", fill="x")
add_hover_effect(tick_start_button)

tick_stop_button = tk.Button(
    left_controls,
    text="Arrêter",
    command=stop_ticking,
    state="normal" if ticking_active else "disabled",
    **button_options,
)
tick_stop_button.pack(anchor="w", fill="x")
add_hover_effect(tick_stop_button)

tk.Label(
    left_controls,
    text="Volume tic-tac",
    font=text_font,
    foreground=TEXT_COLOR,
    background=BACKGROUND_COLOR,
).pack(anchor="w", pady=(1, 0))

tick_volume_scale = tk.Scale(
    left_controls,
    from_=0,
    to=100,
    orient="horizontal",
    showvalue=False,
    length=150,
    sliderlength=16,
    font=text_font,
    foreground=TEXT_COLOR,
    background=BACKGROUND_COLOR,
    activebackground=BACKGROUND_COLOR,
    troughcolor="#cbb877",
    highlightthickness=0,
    borderwidth=0,
    sliderrelief="flat",
    command=on_tick_volume_change,
)
tick_volume_scale.set(tick_volume_percent)
tick_volume_scale.pack(anchor="w")
if not ticking_enabled:
    tick_volume_scale.configure(state="disabled")

# --- Carillon --------------------------------------------------------------
tk.Label(
    left_controls,
    text="Carillon",
    font=heading_font,
    foreground=TEXT_COLOR,
    background=BACKGROUND_COLOR,
).pack(anchor="w", pady=(12, 1))

carillon = tk.StringVar(value="1800")
carillon_enabled_var = tk.BooleanVar(value=True)

carillon_checkbox = tk.Checkbutton(
    left_controls,
    text="Carillon automatique",
    variable=carillon_enabled_var,
    command=set_carillon_enabled,
    font=text_font,
    foreground=TEXT_COLOR,
    background=BACKGROUND_COLOR,
    activebackground=BACKGROUND_COLOR,
    activeforeground=TEXT_COLOR,
    selectcolor=BACKGROUND_COLOR,
    borderwidth=0,
    highlightthickness=0,
    cursor="hand2",
    anchor="w",
    padx=0,
    pady=0,
)
carillon_checkbox.pack(anchor="w", pady=(0, 2))

tk.Radiobutton(
    left_controls,
    text="XVIIIe (métallique)",
    variable=carillon,
    value="1700",
    command=choose_bell,
    font=text_font,
    foreground=TEXT_COLOR,
    background=BACKGROUND_COLOR,
    activebackground=BACKGROUND_COLOR,
    activeforeground=TEXT_COLOR,
    selectcolor=BACKGROUND_COLOR,
    borderwidth=0,
    highlightthickness=0,
).pack(anchor="w")

tk.Radiobutton(
    left_controls,
    text="XIXe (grave)",
    variable=carillon,
    value="1800",
    command=choose_bell,
    font=text_font,
    foreground=TEXT_COLOR,
    background=BACKGROUND_COLOR,
    activebackground=BACKGROUND_COLOR,
    activeforeground=TEXT_COLOR,
    selectcolor=BACKGROUND_COLOR,
    borderwidth=0,
    highlightthickness=0,
).pack(anchor="w")

test_button = tk.Button(
    left_controls,
    text="Essai du carillon",
    command=test_bell,
    **button_options,
)
test_button.pack(anchor="w", fill="x", pady=(1, 0))
add_hover_effect(test_button)

status = tk.StringVar(value="Carillon actif.")
status_label = tk.Label(
    left_controls,
    textvariable=status,
    font=status_font,
    foreground="#5b5032",
    background=BACKGROUND_COLOR,
    justify="left",
    anchor="w",
)
status_label.pack(anchor="w", fill="x", pady=(1, 0))

# --- Porte secrète du socle -----------------------------------------------
# La porte est volontairement presque invisible : un simple joint sombre et
# un petit bouton doré. Le bouton et la porte entière sont cliquables.
secret_door = canvas.create_rectangle(
    0,
    0,
    0,
    0,
    fill="",
    outline="#4b2b18",
    width=2,
)
secret_door_knob = canvas.create_oval(
    0,
    0,
    0,
    0,
    fill="#d6ad45",
    outline="#6d4a16",
    width=2,
)
canvas.tag_bind(secret_door, "<Button-1>", toggle_secret_door)
canvas.tag_bind(secret_door_knob, "<Button-1>", toggle_secret_door)
canvas.tag_bind(secret_door, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
canvas.tag_bind(secret_door_knob, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
canvas.tag_bind(secret_door, "<Leave>", lambda _event: canvas.configure(cursor=""))
canvas.tag_bind(secret_door_knob, "<Leave>", lambda _event: canvas.configure(cursor=""))

schedule_panel = tk.Frame(
    root,
    background="#eadca5",
    borderwidth=2,
    relief="ridge",
)

panel_title = tk.Label(
    schedule_panel,
    text="Silence du carillon",
    font=heading_font,
    foreground=TEXT_COLOR,
    background="#eadca5",
)
panel_title.pack(pady=(5, 2))

panel_hint = tk.Label(
    schedule_panel,
    text="Cocher les heures silencieuses",
    font=tkfont.Font(root=root, family="Verdana", size=9, slant="italic"),
    foreground="#5b5032",
    background="#eadca5",
)
panel_hint.pack(pady=(0, 3))

hours_grid = tk.Frame(schedule_panel, background="#eadca5")
hours_grid.pack(expand=True)
silent_hour_vars: list[tk.BooleanVar] = []
for hour in range(24):
    variable = tk.BooleanVar(value=False)
    silent_hour_vars.append(variable)
    checkbox = tk.Checkbutton(
        hours_grid,
        text=f"{hour:02d} h",
        variable=variable,
        command=update_silent_hours,
        font=tkfont.Font(root=root, family="Verdana", size=9),
        foreground=TEXT_COLOR,
        background="#eadca5",
        activebackground="#eadca5",
        activeforeground=TEXT_COLOR,
        selectcolor="#eadca5",
        borderwidth=0,
        highlightthickness=0,
        padx=2,
        pady=0,
    )
    checkbox.grid(row=hour % 6, column=hour // 6, sticky="w", padx=2)

panel_buttons = tk.Frame(schedule_panel, background="#eadca5")
panel_buttons.pack(pady=(2, 5))
for label, value in (("Tout cocher", True), ("Tout décocher", False)):
    button = tk.Button(
        panel_buttons,
        text=label,
        command=lambda selected=value: set_all_silent_hours(selected),
        font=tkfont.Font(root=root, family="Verdana", size=8),
        foreground=TEXT_COLOR,
        background="#eadca5",
        activebackground="#d9c780",
        borderwidth=1,
        relief="groove",
        padx=4,
        pady=1,
    )
    button.pack(side="left", padx=2)

close_panel_button = tk.Button(
    panel_buttons,
    text="Refermer",
    command=toggle_secret_door,
    font=tkfont.Font(root=root, family="Verdana", size=8),
    foreground=TEXT_COLOR,
    background="#eadca5",
    activebackground="#d9c780",
    borderwidth=1,
    relief="groove",
    padx=4,
    pady=1,
)
close_panel_button.pack(side="left", padx=2)
schedule_panel.place_forget()

root.protocol("WM_DELETE_WINDOW", close)
animate_pendulum()
animate_clock_hands()
if ticking_enabled:
    schedule_next_tick()
start_carillon_monitor()

if __name__ == "__main__":
    root.mainloop()
