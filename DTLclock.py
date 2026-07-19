"""DTLclock — une horloge comtoise qui sonne à chaque heure pleine."""

from __future__ import annotations

import configparser
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


SCRIPT_DIR = Path(__file__).resolve().parent
EXECUTABLE_DIR = Path(sys.executable).resolve().parent
BUNDLE_DIR = (
    Path(sys._MEIPASS)  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    else SCRIPT_DIR
)

# Le fichier .ini reste prioritairement à côté du script ou de l'exécutable.
BASE_DIR = EXECUTABLE_DIR if getattr(sys, "frozen", False) else SCRIPT_DIR


def find_asset(filename: str) -> Path:
    """Cherche une ressource dans les emplacements possibles de DTLclock."""
    candidates = (
        BASE_DIR / filename,
        SCRIPT_DIR / filename,
        BUNDLE_DIR / filename,
        Path.cwd() / filename,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


IMAGE_PATH = find_asset("horloge.png")
BOB_IMAGE_PATH = find_asset("balancier.png")
BELLS = {
    "1700": find_asset("bell1700.wav"),
    "1800": find_asset("bell1800.wav"),
}
TICK_WAV_PATH = find_asset("tick.wav")
TOCK_WAV_PATH = find_asset("tock.wav")

# Constantes de paramétrage chargées depuis DTLclock.ini, à côté du script.
# Si le fichier est absent ou qu'une clé y manque, on retombe silencieusement
# sur la valeur d'origine du code : le programme démarre quand même.
CONFIG_PATH = BASE_DIR / "DTLclock.ini"
_config = configparser.ConfigParser()
_config.read(CONFIG_PATH, encoding="utf-8")


def _cfg_str(section: str, key: str, fallback: str) -> str:
    return _config.get(section, key, fallback=fallback)


def _cfg_int(section: str, key: str, fallback: int) -> int:
    try:
        return _config.getint(section, key, fallback=fallback)
    except ValueError:
        return fallback


def _cfg_float(section: str, key: str, fallback: float) -> float:
    try:
        return _config.getfloat(section, key, fallback=fallback)
    except ValueError:
        return fallback


def _cfg_float_tuple(section: str, key: str, fallback: tuple[float, ...]) -> tuple[float, ...]:
    raw = _config.get(section, key, fallback=None)
    if raw is None:
        return fallback
    try:
        return tuple(float(part.strip()) for part in raw.split(","))
    except ValueError:
        return fallback


VERSION = _cfg_str("general", "VERSION", "1.6.0")

root = tk.Tk()
root.title(f"DTLclock v{VERSION}")
root.minsize(420, 760)
root.resizable(True, True)

BACKGROUND_COLOR = _cfg_str("couleurs", "BACKGROUND_COLOR", "#efe4b0")
TEXT_COLOR = _cfg_str("couleurs", "TEXT_COLOR", "#17130c")
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
    except (wave.Error, OSError, struct.error):
        # Les fichiers existent mais ne sont pas convertibles par notre réglage
        # de volume : on les jouera directement au lieu de désactiver le tic-tac.
        _tick_raw_samples = []
        _tock_raw_samples = []
        _tick_wav_params = None
        _tock_wav_params = None


def apply_tick_volume(volume_percent: float) -> None:
    """Régénère, sous un nom inédit, les copies de tick/tock au volume demandé."""
    global current_tick_path, current_tock_path, _volume_generation

    if not ticking_enabled:
        return

    # Si le WAV n'est pas au format attendu, on conserve les originaux.
    if (
        not _tick_raw_samples
        or not _tock_raw_samples
        or _tick_wav_params is None
        or _tock_wav_params is None
    ):
        current_tick_path = TICK_WAV_PATH
        current_tock_path = TOCK_WAV_PATH
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

    # Ne supprimer que d'anciennes COPIES temporaires générées par DTLclock.
    # Les fichiers source tick.wav et tock.wav ne doivent jamais être touchés.
    temp_dir = Path(tempfile.gettempdir()).resolve()
    protected_paths = {
        TICK_WAV_PATH.resolve(),
        TOCK_WAV_PATH.resolve(),
    }

    for old_path in (old_tick_path, old_tock_path):
        if old_path is None:
            continue

        try:
            resolved_old_path = old_path.resolve()
            is_generated_copy = (
                resolved_old_path.parent == temp_dir
                and resolved_old_path.name.startswith(("dtlclock_tick_", "dtlclock_tock_"))
            )
            if is_generated_copy and resolved_old_path not in protected_paths:
                resolved_old_path.unlink(missing_ok=True)
        except OSError:
            pass


if ticking_enabled:
    current_tick_path = TICK_WAV_PATH
    current_tock_path = TOCK_WAV_PATH
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
    """Fait avancer l'échappement, la trotteuse et le tic-tac au même instant."""
    global _next_tick_index, displayed_clock_time

    # Le mécanisme continue de battre même lorsque le son du tic-tac est coupé.
    # La trotteuse avance donc d'un cran exactement à chaque extrémité du balancier.
    if pendulum_running:
        displayed_clock_time = datetime.now().replace(microsecond=0)
        update_clock_hands(displayed_clock_time)

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
    """Relance l'horloge : balancier, échappement et tic-tac."""
    global pendulum_running, pendulum_start_time, displayed_clock_time

    if not pendulum_running:
        pendulum_start_time = time.perf_counter()
        displayed_clock_time = datetime.now().replace(microsecond=0)
        pendulum_running = True
        clock_start_button.configure(state="disabled")
        clock_stop_button.configure(state="normal")

    update_clock_hands(displayed_clock_time)
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
    """Active le son du tic-tac sans produire de battement artificiel."""
    global ticking_active, _next_tick_index, pendulum_start_time

    if not ticking_enabled:
        messagebox.showwarning(
            "DTLclock",
            "Impossible d'activer le tic-tac.\n\n"
            f"Fichier recherché :\n{TICK_WAV_PATH}\n{TOCK_WAV_PATH}",
        )
        return

    ticking_active = True
    _next_tick_index = 0
    pendulum_start_time = time.perf_counter()
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


def get_primary_work_area() -> tuple[int, int, int, int]:
    """Retourne la zone utile de l'écran principal, hors barre des tâches."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            work_area = wintypes.RECT()
            spi_getworkarea = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(
                spi_getworkarea, 0, ctypes.byref(work_area), 0
            ):
                return (
                    work_area.left,
                    work_area.top,
                    work_area.right,
                    work_area.bottom,
                )
        except (AttributeError, OSError):
            pass

    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def set_initial_full_height_geometry() -> None:
    """Dimensionne DTLclock presque sur toute la hauteur de l'écran principal."""
    work_left, work_top, work_right, work_bottom = get_primary_work_area()
    work_width = max(1, work_right - work_left)
    work_height = max(1, work_bottom - work_top)

    # Une petite marge évite que la bordure de fenêtre touche les bords.
    target_height = max(760, round(work_height * 0.97))
    target_width = max(
        420,
        round(target_height * source_image.width / source_image.height),
    )

    # Sur un écran très étroit, on conserve les proportions de l'image.
    if target_width > work_width:
        target_width = max(420, round(work_width * 0.97))
        target_height = max(
            760,
            round(target_width * source_image.height / source_image.width),
        )

    x = work_left + max(0, (work_width - target_width) // 2)
    y = work_top + max(0, (work_height - target_height) // 2)
    root.geometry(f"{target_width}x{target_height}+{x}+{y}")


set_initial_full_height_geometry()

canvas = tk.Canvas(
    root,
    background=BACKGROUND_COLOR,
    highlightthickness=0,
)
canvas.place(x=0, y=0, relwidth=1, relheight=1)
background_item = canvas.create_image(250, 450, image=photo)

# Coordonnées exprimées dans l'image source 1080 × 1920.
PENDULUM_PIVOT_X = _cfg_int("balancier", "PENDULUM_PIVOT_X", 550)
PENDULUM_PIVOT_Y = _cfg_int("balancier", "PENDULUM_PIVOT_Y", 950)
PENDULUM_ROD_LENGTH = _cfg_int("balancier", "PENDULUM_ROD_LENGTH", 395)
PENDULUM_BOB_RADIUS = _cfg_int("balancier", "PENDULUM_BOB_RADIUS", 74)
PENDULUM_MAX_ANGLE = _cfg_int("balancier", "PENDULUM_MAX_ANGLE", 7)
PENDULUM_PERIOD_SECONDS = _cfg_float("balancier", "PENDULUM_PERIOD_SECONDS", 2.0)
PENDULUM_FRAME_DELAY_MS = _cfg_int("balancier", "PENDULUM_FRAME_DELAY_MS", 16)
# Écartement latéral des deux branches parallèles fines de part et d'autre
# de la tige centrale (motif "gridiron"), en coordonnées de l'image source.
PENDULUM_SIDE_ROD_OFFSET = _cfg_int("balancier", "PENDULUM_SIDE_ROD_OFFSET", 9)
# Position (fraction de la longueur visible de la tige, 0 = pivot,
# 1 = bord de la sphère) des petites traverses décoratives reliant les
# deux branches latérales.
PENDULUM_CROSSBAR_FRACTIONS = _cfg_float_tuple(
    "balancier", "PENDULUM_CROSSBAR_FRACTIONS", (0.28, 0.55, 0.82)
)

# Position du centre du cadran dans l'image source 1080 × 1920.
DIAL_CENTER_X = _cfg_int("cadran", "DIAL_CENTER_X", 550)
DIAL_CENTER_Y = _cfg_int("cadran", "DIAL_CENTER_Y", 540)
HOUR_HAND_LENGTH = _cfg_int("cadran", "HOUR_HAND_LENGTH", 86)
MINUTE_HAND_LENGTH = _cfg_int("cadran", "MINUTE_HAND_LENGTH", 124)
SECOND_HAND_LENGTH = _cfg_int("cadran", "SECOND_HAND_LENGTH", 132)
CLOCK_REFRESH_MS = _cfg_int("cadran", "CLOCK_REFRESH_MS", 200)

# Bouton caché dans la rose centrale du socle.
ROSE_BUTTON_X = _cfg_int("bouton_rose", "ROSE_BUTTON_X", 550)
ROSE_BUTTON_Y = _cfg_int("bouton_rose", "ROSE_BUTTON_Y", 1655)
ROSE_BUTTON_RADIUS = _cfg_int("bouton_rose", "ROSE_BUTTON_RADIUS", 14)

# Compartiment central du socle, exprimé dans le repère 1080 × 1920.
COMPARTMENT_LEFT = _cfg_int("compartiment", "COMPARTMENT_LEFT", 335)
COMPARTMENT_TOP = _cfg_int("compartiment", "COMPARTMENT_TOP", 1460)
COMPARTMENT_RIGHT = _cfg_int("compartiment", "COMPARTMENT_RIGHT", 790)
COMPARTMENT_BOTTOM = _cfg_int("compartiment", "COMPARTMENT_BOTTOM", 1815)

# Le tableau occupe une fraction du compartiment et reste toujours centré.
SCHEDULE_WIDTH_RATIO = _cfg_float("tableau_horaires", "SCHEDULE_WIDTH_RATIO", 0.68)
SCHEDULE_HEIGHT_RATIO = _cfg_float("tableau_horaires", "SCHEDULE_HEIGHT_RATIO", 0.52)

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

# Heure effectivement affichée par le mécanisme. Elle ne progresse qu'à chaque
# échappement du balancier, au même instant que le tic ou le tac.
displayed_clock_time = datetime.now().replace(microsecond=0)

schedule_open = False


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
    """Affiche l'heure transmise par le mécanisme de l'horloge."""
    current = displayed_clock_time if now is None else now
    center_x = image_offset_x + DIAL_CENTER_X * image_scale
    center_y = image_offset_y + DIAL_CENTER_Y * image_scale

    hour_angle = math.radians(
        30 * (current.hour % 12) + 0.5 * current.minute + current.second / 120 - 90
    )
    minute_angle = math.radians(
        6 * current.minute + 0.1 * current.second - 90
    )
    # Trotteuse mécanique : un saut net de 6 degrés par échappement.
    second_angle = math.radians(6 * current.second - 90)

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
    """Dessine l'état initial ; les battements suivants sont pilotés par fire_tick()."""
    update_clock_hands()


def position_schedule_panel() -> None:
    """Place le bouton sur la rose et centre le tableau dans le socle."""
    button_x = image_offset_x + ROSE_BUTTON_X * image_scale
    button_y = image_offset_y + ROSE_BUTTON_Y * image_scale
    button_radius = max(4.0, ROSE_BUTTON_RADIUS * image_scale)

    canvas.coords(
        rose_button,
        button_x - button_radius,
        button_y - button_radius,
        button_x + button_radius,
        button_y + button_radius,
    )
    canvas.itemconfigure(
        rose_button,
        width=max(1, round(2 * image_scale)),
        state="hidden" if schedule_open else "normal",
    )

    compartment_left = image_offset_x + COMPARTMENT_LEFT * image_scale
    compartment_top = image_offset_y + COMPARTMENT_TOP * image_scale
    compartment_right = image_offset_x + COMPARTMENT_RIGHT * image_scale
    compartment_bottom = image_offset_y + COMPARTMENT_BOTTOM * image_scale

    compartment_width = compartment_right - compartment_left
    compartment_height = compartment_bottom - compartment_top

    panel_width = max(1, round(compartment_width * SCHEDULE_WIDTH_RATIO))
    panel_height = max(1, round(compartment_height * SCHEDULE_HEIGHT_RATIO))

    panel_x = compartment_left + (compartment_width - panel_width) / 2
    # Décalage volontairement visible vers le bas : 9 % de la hauteur du
    # compartiment, soit environ 25 à 30 pixels à la taille d'affichage actuelle.
    panel_y = (
        compartment_top
        + (compartment_height - panel_height) / 2
        + compartment_height * 0.09
    )

    if schedule_open:
        schedule_panel.place(
            x=round(panel_x),
            y=round(panel_y),
            width=panel_width,
            height=panel_height,
        )
        schedule_panel.lift()
    else:
        schedule_panel.place_forget()


def toggle_schedule_panel(_event: tk.Event[tk.Misc] | None = None) -> None:
    """Affiche ou masque le tableau des heures silencieuses."""
    global schedule_open

    schedule_open = not schedule_open
    position_schedule_panel()


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
    position_schedule_panel()

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

# --- Tableau des heures silencieuses ---------------------------------------
# Fermé : seul un petit bouton doré est posé au centre de la rose du socle.
# Ouvert : le tableau apparaît directement dans le meuble, sans porte animée.
rose_button = canvas.create_oval(
    0,
    0,
    0,
    0,
    fill="#d6ad45",
    outline="#6d4a16",
    width=2,
)
canvas.tag_bind(rose_button, "<Button-1>", toggle_schedule_panel)
canvas.tag_bind(
    rose_button,
    "<Enter>",
    lambda _event: canvas.configure(cursor="hand2"),
)
canvas.tag_bind(
    rose_button,
    "<Leave>",
    lambda _event: canvas.configure(cursor=""),
)

PANEL_BACKGROUND = _cfg_str("couleurs", "PANEL_BACKGROUND", "#eadca5")

schedule_panel = tk.Frame(
    root,
    background=PANEL_BACKGROUND,
    borderwidth=0,
    relief="flat",
)

panel_hint = tk.Label(
    schedule_panel,
    text="Cocher les heures silencieuses",
    font=tkfont.Font(root=root, family="Verdana", size=9, slant="italic"),
    foreground="#5b5032",
    background=PANEL_BACKGROUND,
)
panel_hint.pack(pady=(1, 1))

hours_grid = tk.Frame(schedule_panel, background=PANEL_BACKGROUND)
hours_grid.pack(expand=True, pady=(0, 1))
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
        background=PANEL_BACKGROUND,
        activebackground=PANEL_BACKGROUND,
        activeforeground=TEXT_COLOR,
        selectcolor=PANEL_BACKGROUND,
        borderwidth=0,
        highlightthickness=0,
        padx=1,
        pady=0,
        cursor="hand2",
    )
    checkbox.grid(row=hour % 6, column=hour // 6, sticky="w", padx=1)

panel_buttons = tk.Frame(schedule_panel, background=PANEL_BACKGROUND)
panel_buttons.pack(pady=(1, 2))

for label, value in (("Tout cocher", True), ("Tout décocher", False)):
    button = tk.Button(
        panel_buttons,
        text=label,
        command=lambda selected=value: set_all_silent_hours(selected),
        font=tkfont.Font(root=root, family="Verdana", size=8),
        foreground=TEXT_COLOR,
        background=PANEL_BACKGROUND,
        activebackground="#d9c780",
        borderwidth=1,
        relief="groove",
        padx=3,
        pady=0,
        cursor="hand2",
    )
    button.pack(side="left", padx=1)

close_panel_button = tk.Button(
    panel_buttons,
    text="Fermer",
    command=toggle_schedule_panel,
    font=tkfont.Font(root=root, family="Verdana", size=8),
    foreground=TEXT_COLOR,
    background=PANEL_BACKGROUND,
    activebackground="#d9c780",
    borderwidth=1,
    relief="groove",
    padx=3,
    pady=0,
    cursor="hand2",
)
close_panel_button.pack(side="left", padx=1)

schedule_panel.place_forget()

root.protocol("WM_DELETE_WINDOW", close)
animate_pendulum()
animate_clock_hands()
if ticking_enabled:
    # Active le tic-tac après le démarrage de Tkinter, puis laisse le premier
    # battement se produire naturellement à l'extrémité suivante du balancier.
    root.after(250, start_ticking)
    root.after(260, schedule_next_tick)
else:
    root.after(
        250,
        lambda: status.set(
            f"Tic-tac indisponible : {TICK_WAV_PATH.name}/{TOCK_WAV_PATH.name}"
        ),
    )
start_carillon_monitor()

if __name__ == "__main__":
    root.mainloop()
