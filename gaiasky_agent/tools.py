"""
Catalog of the actions the assistant can perform, each bound to Gaia Sky's REST
APIv2 server.

This is a line-for-line port of the ~40 tools that lived in
`gaiasky.ai.AiToolRegistry` inside Gaia Sky itself, adapted to call the running
application over HTTP instead of in-process. Names, descriptions, parameters
and result text are kept the same on purpose, so the model sees the same
tools it would inside Gaia Sky. See PLAN.md section 5 for the tool-to-REST
mapping this file implements, including the two tools that are necessarily
degraded (search_objects, get_closest_object) because vanilla Gaia Sky has no
REST-reachable equivalent of the internal calls they used.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .gaiasky import GaiaSkyClient

MAX_ANIMATION_SECONDS = 120.0
MAX_WAIT_SECONDS = 120.0
MIN_ANIMATION_SECONDS = 1.0

SMOOTH_TYPE = "logisticsigmoid"

EXCLUSIVE_TOOLS = {"go_to_object", "go_to_object_instant", "land_on"}

# From gaiasky.render.ComponentTypes.ComponentType: every category but Invisible.
COMPONENT_TYPES = [
    "Stars", "Planets", "Moons", "Satellites", "Asteroids", "Clusters", "MilkyWay",
    "Galaxies", "Pulsars", "Nebulae", "Meshes", "Systems", "Labels", "Orbits",
    "Locations", "Countries", "Equatorial", "Ecliptic", "Galactic", "RecursiveGrid",
    "Constellations", "Boundaries", "Atmospheres", "Clouds", "Keyframes",
    "VelocityVectors", "Ruler", "Axes", "Effects", "Others",
]

# Constants.java bounds, ported so tools clamp exactly as they did in Gaia Sky.
MIN_FOV, MAX_FOV = 1.0, 150.0
MIN_LABEL_SIZE, MAX_LABEL_SIZE = 0.7, 2.5
MIN_LINE_WIDTH, MAX_LINE_WIDTH = 0.2, 3.5
MIN_STAR_BRIGHTNESS, MAX_STAR_BRIGHTNESS = 0.4, 8.0
MIN_POINT_SIZE_SCALE, MAX_POINT_SIZE_SCALE = 0.1, 4.0
MIN_AMBIENT_LIGHT, MAX_AMBIENT_LIGHT = 0.0, 1.0


class ToolError(Exception):
    """Raised by a tool to report a problem back to the model, not to abort the exchange."""


@dataclass
class Param:
    name: str
    type: str  # JSON-schema type: "string", "number", "integer", "boolean"
    description: str
    required: bool = True

    @staticmethod
    def str(name: str, description: str, required: bool) -> "Param":
        return Param(name, "string", description, required)

    @staticmethod
    def num(name: str, description: str, required: bool) -> "Param":
        return Param(name, "number", description, required)

    @staticmethod
    def integer(name: str, description: str, required: bool) -> "Param":
        return Param(name, "integer", description, required)

    @staticmethod
    def bool(name: str, description: str, required: bool) -> "Param":
        return Param(name, "boolean", description, required)


@dataclass
class Tool:
    name: str
    description: str
    parameters: list[Param]
    mutating: bool
    function: Callable[[dict[str, Any]], str]


# ==================================================================== argument helpers

def require_string(args: dict, name: str) -> str:
    value = args.get(name)
    if value is None:
        raise ToolError(f"Missing required parameter '{name}'.")
    value = str(value).strip()
    if not value:
        raise ToolError(f"Parameter '{name}' must not be empty.")
    return value


def optional_string(args: dict, name: str, fallback: str) -> str:
    value = args.get(name)
    if value is None:
        return fallback
    value = str(value).strip()
    return value if value else fallback


def require_double(args: dict, name: str) -> float:
    value = args.get(name)
    if value is None:
        raise ToolError(f"Missing required parameter '{name}'.")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ToolError(f"Parameter '{name}' must be a number, got '{value}'.")


def optional_double(args: dict, name: str, fallback: float) -> float:
    value = args.get(name)
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def require_bool(args: dict, name: str) -> bool:
    value = args.get(name)
    if value is None:
        raise ToolError(f"Missing required parameter '{name}'.")
    return _parse_bool(value, name)


def optional_bool(args: dict, name: str, fallback: bool) -> bool:
    value = args.get(name)
    if value is None:
        return fallback
    try:
        return _parse_bool(value, name)
    except ToolError:
        return fallback


def _parse_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    raise ToolError(f"Parameter '{name}' must be true or false, got '{value}'.")


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def animation_seconds(args: dict, fallback: float) -> float:
    return clamp(optional_double(args, "duration", fallback), 0.0, MAX_ANIMATION_SECONDS)


def format_number(value: float) -> str:
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return str(value)
    absval = abs(value)
    if absval >= 1e9 or (0 < absval < 1e-3):
        return f"{value:.3e}"
    if value == int(value) and absval < 1e15:
        return str(int(value))
    return f"{value:.2f}"


def resolve_component_type(text: str) -> str:
    normalized = text.strip().lower().replace(" ", "").replace("_", "")
    for ct in COMPONENT_TYPES:
        if ct.lower() == normalized:
            return f"element.{ct.lower()}"
    raise ToolError(f"Unknown category '{text}'. Valid categories are: {component_type_names()}.")


def component_type_names() -> str:
    return ", ".join(ct.lower() for ct in COMPONENT_TYPES)


class ToolRegistry:
    """Builds and holds the tool catalog for one Gaia Sky connection."""

    def __init__(self, client: GaiaSkyClient):
        self.client = client
        self.stop = threading.Event()
        self._tools: dict[str, Tool] = {}
        self._register()

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def is_exclusive(self, name: str) -> bool:
        return name in EXCLUSIVE_TOOLS

    def interrupt(self) -> None:
        self.stop.set()

    def resume(self) -> None:
        self.stop.clear()

    def _add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    # --- verification helpers, calling Gaia Sky ---

    def _object_position(self, name: str, units: str = "internal"):
        return self.client.call("scene", "get_object_position", {"name": name, "units": units})

    def _verify_object_exists(self, name: str) -> None:
        if self._object_position(name) is None:
            raise ToolError(
                f"No object named '{name}' exists in the loaded datasets. "
                f"Check the spelling, or try a catalogue identifier."
            )

    def _register(self) -> None:
        self._register_navigation()
        self._register_queries()
        self._register_time()
        self._register_visibility()
        self._register_datasets()
        self._register_graphics()
        self._register_interface()
        self._register_flow()
        self._register_notes()

    # ==================================================================== navigation

    def _register_navigation(self) -> None:
        client = self.client

        def go_to_object(args: dict) -> str:
            name = require_string(args, "name")
            duration = max(MIN_ANIMATION_SECONDS, animation_seconds(args, 5.0))
            angle = optional_double(args, "angle", 5.0)
            wait = optional_bool(args, "wait", True)
            self._verify_object_exists(name)
            if wait:
                client.call("camera", "go_to_object",
                            {"name": name, "sa": angle, "pos_duration": duration,
                             "ori_duration": duration, "sync": True},
                            timeout=duration + 60)
                if self.stop.is_set():
                    return f"The flight to {name} was interrupted."
                client.call("camera", "focus_mode", {"name": name})
                distance = client.call("camera", "get_distance_to_object", {"name": name})
                if distance is not None and distance >= 0:
                    return (f"The camera has arrived at {name} and is focused on it. "
                            f"Distance to its surface: {format_number(distance)} km.")
                return f"The camera has arrived at {name} and is focused on it."
            client.call("camera", "go_to_object",
                        {"name": name, "sa": angle, "pos_duration": duration,
                         "ori_duration": duration, "sync": False})
            return f"The camera is now flying to {name}, which takes about {format_number(duration)} seconds."

        self._add(Tool(
            "go_to_object",
            "Fly the camera to an object and focus on it. Use this whenever the user asks to visit, travel "
            "to, go to, or take a closer look at something. By default this waits until the camera "
            "has arrived, so that anything you say afterwards describes what is actually on screen. "
            "Accepts object names and catalogue identifiers such as HIP 87937.",
            [Param.str("name", "Name or identifier of the object, for example 'Mars', 'Betelgeuse' or 'HIP 87937'.", True),
             Param.num("duration", "Duration of the flight in seconds. Defaults to 5.", False),
             Param.num("angle", "Target solid angle in degrees. Controls how close the camera gets. 20 is close, "
                        "5 is a comfortable distance, 1 is far away. Defaults to 5.0.", False),
             Param.bool("wait", "Whether to wait for the camera to arrive before returning. Defaults to true. "
                        "Only pass false if you deliberately want to do something else while the camera flies.", False)],
            True, go_to_object))

        def go_to_object_instant(args: dict) -> str:
            name = require_string(args, "name")
            self._verify_object_exists(name)
            client.call("camera", "go_to_object_instant", {"name": name})
            return f"The camera is now at {name}."

        self._add(Tool(
            "go_to_object_instant",
            "Jump the camera to an object at once, with no flight. Use this when the user wants to get "
            "somewhere immediately, or when travelling through many objects in quick succession.",
            [Param.str("name", "Name or identifier of the object.", True)],
            True, go_to_object_instant))

        def land_on(args: dict) -> str:
            name = require_string(args, "name")
            self._verify_object_exists(name)
            location = optional_string(args, "location", "")
            latitude = optional_double(args, "latitude", float("nan"))
            longitude = optional_double(args, "longitude", float("nan"))
            if latitude == latitude and longitude == longitude:  # not NaN
                client.call("camera/interactive", "land_at_location",
                            {"name": name, "longitude": longitude, "latitude": latitude}, timeout=600)
            elif location:
                client.call("camera/interactive", "land_at_location",
                            {"name": name, "location": location}, timeout=600)
            else:
                client.call("camera/interactive", "land_on", {"name": name}, timeout=600)
            if location:
                where = f" at {location}"
            elif latitude == latitude and longitude == longitude:
                where = f" at latitude {format_number(latitude)}, longitude {format_number(longitude)}"
            else:
                where = ""
            return f"The camera has landed on {name}{where}."

        self._add(Tool(
            "land_on",
            "Fly down to the surface of a planet and settle just above the ground. Use this when the user "
            "asks to land somewhere. A named place or coordinates land at that spot; with neither, "
            "the descent is to the nearest point. This is a piloted descent that takes a while, and "
            "it returns only when it is done. It only works on planets and moons with a modelled "
            "surface; for anything else, use go_to_object.",
            [Param.str("name", "Name of the planet or moon to land on.", True),
             Param.str("location", "Named place to land at, such as 'Gale Crater'. Optional.", False),
             Param.num("latitude", "Latitude of the landing spot in degrees, with longitude. Optional.", False),
             Param.num("longitude", "Longitude of the landing spot in degrees, with latitude. Optional.", False)],
            True, land_on))

        def set_cinematic_camera(args: dict) -> str:
            enabled = require_bool(args, "enabled")
            client.call("camera/interactive", "set_cinematic", {"cinematic": enabled})
            return "Cinematic camera behaviour is now on." if enabled else "Cinematic camera behaviour is now off."

        self._add(Tool(
            "set_cinematic_camera",
            "Switch the cinematic camera behaviour on or off. When on, camera movements accelerate and "
            "decelerate smoothly, which reads better for tours and recordings; when off, the camera "
            "responds immediately.",
            [Param.bool("enabled", "True for smooth, cinematic motion; false for immediate response.", True)],
            True, set_cinematic_camera))

        def focus_object(args: dict) -> str:
            name = require_string(args, "name")
            self._verify_object_exists(name)
            client.call("camera", "focus_mode", {"name": name})
            return f"The camera is now focused on {name}."

        self._add(Tool(
            "focus_object",
            "Point the camera at an object without travelling to it. Use this when the user wants to look at "
            "or track something from the current position, rather than approach it.",
            [Param.str("name", "Name or identifier of the object to focus on.", True)],
            True, focus_object))

        self._add(Tool(
            "free_camera",
            "Release the camera from its current focus and switch to free navigation mode.",
            [], True,
            lambda args: (client.call("camera", "free_mode"), "The camera is now in free mode.")[1]))

        def stop_camera(args: dict) -> str:
            self.stop.set()
            client.call("camera", "stop")
            time.sleep(0.15)
            self.stop.clear()
            return "Camera motion stopped."

        self._add(Tool(
            "stop_camera",
            "Immediately stop all camera motion. Use this if the user asks to stop, halt or cancel movement.",
            [], True, stop_camera))

        self._add(Tool(
            "center_camera",
            "Centre the current focus in the middle of the view.",
            [], True,
            lambda args: (client.call("camera", "center"), "The focus is now centred.")[1]))

        def set_camera_speed(args: dict) -> str:
            level = int(clamp(require_double(args, "level"), 0, 21))
            client.call("camera", "set_max_speed", {"index": level})
            return f"Camera speed set to level {level}."

        self._add(Tool(
            "set_camera_speed",
            "Set how fast the camera moves in free mode, as a step on the application's speed scale.",
            [Param.integer("level", "Speed level, from 0 (slowest) to 21 (fastest).", True)],
            True, set_camera_speed))

        def set_field_of_view(args: dict) -> str:
            fov = clamp(require_double(args, "fov"), MIN_FOV, MAX_FOV)
            duration = animation_seconds(args, 0.0)
            if duration <= 0:
                client.call("camera", "set_fov", {"fov": fov})
            else:
                client.call("camera", "transition_fov",
                            {"target_fov": fov, "duration": duration, "smooth_type": SMOOTH_TYPE,
                             "smooth_factor": 12.0, "sync": True}, timeout=duration + 60)
            return f"Field of view set to {format_number(fov)} degrees."

        self._add(Tool(
            "set_field_of_view",
            "Set the camera field of view, in degrees. A narrow field of view zooms in, a wide one zooms out.",
            [Param.num("fov", f"Field of view in degrees, between {format_number(MIN_FOV)} and {format_number(MAX_FOV)}.", True),
             Param.num("duration", "Seconds to animate the change over. Defaults to 0, which applies it at once.", False)],
            True, set_field_of_view))

        def track_object(args: dict) -> str:
            name = args.get("name")
            if name is None or not str(name).strip():
                client.call("camera", "remove_tracking_object")
                return "No longer tracking any object."
            name = require_string(args, "name")
            self._verify_object_exists(name)
            client.call("camera", "set_tracking_object", {"name": name})
            return f"Now tracking {name}."

        self._add(Tool(
            "track_object",
            "Keep the camera pointing at an object as it moves, without focusing on it. Pass no name to stop "
            "tracking.",
            [Param.str("name", "Object to track. Omit to stop tracking.", False)],
            True, track_object))

        def set_focus_lock(args: dict) -> str:
            locked = require_bool(args, "locked")
            client.call("camera", "set_focus_lock", {"lock": locked})
            return "The camera now follows the focus." if locked else "The camera no longer follows the focus."

        self._add(Tool(
            "set_focus_lock",
            "Lock or unlock the camera to the focus, so that it follows the object along its orbit instead of "
            "staying put as the object moves away.",
            [Param.bool("locked", "True to follow the focus, false to stay put.", True)],
            True, set_focus_lock))

        def point_camera(args: dict) -> str:
            longitude = require_double(args, "longitude")
            latitude = require_double(args, "latitude")
            frame = optional_string(args, "frame", "equatorial").lower()
            if frame.startswith("gal"):
                client.call("camera", "set_direction_galactic", {"l": longitude, "b": latitude})
                return f"The camera is pointing at galactic l={format_number(longitude)}, b={format_number(latitude)} degrees."
            client.call("camera", "set_direction_equatorial", {"ra": longitude, "dec": latitude})
            return f"The camera is pointing at RA={format_number(longitude)}, dec={format_number(latitude)} degrees."

        self._add(Tool(
            "point_camera",
            "Point the camera at a position on the sky, given in equatorial or galactic coordinates. Use this "
            "for directions rather than objects, such as 'look at the galactic centre'.",
            [Param.num("longitude", "Right ascension, or galactic longitude, in degrees.", True),
             Param.num("latitude", "Declination, or galactic latitude, in degrees.", True),
             Param.str("frame", "Coordinate frame: 'equatorial' or 'galactic'. Defaults to equatorial.", False)],
            True, point_camera))

    # ==================================================================== queries

    def _register_queries(self) -> None:
        client = self.client

        def get_object_info(args: dict) -> str:
            name = require_string(args, "name")
            self._verify_object_exists(name)
            distance = client.call("camera", "get_distance_to_object", {"name": name})
            radius = client.call("scene", "get_object_radius", {"name": name})
            parts = [f"Object '{name}' is present in the scene."]
            if distance is not None and distance >= 0:
                parts.append(f" Distance from the camera to its surface: {format_number(distance)} km.")
            if radius is not None and radius > 0:
                parts.append(f" Radius: {format_number(radius)} km.")
            visible = client.call("scene", "get_object_visibility", {"name": name})
            parts.append(" It is currently " + ("visible." if visible else "hidden."))
            return "".join(parts)

        self._add(Tool(
            "get_object_info",
            "Look up an object in the loaded datasets and report its distance from the camera and its radius. "
            "Use this before describing an object so that the figures you quote are the real ones "
            "from the running simulation, rather than recalled from memory.",
            [Param.str("name", "Name or identifier of the object to look up.", True)],
            False, get_object_info))

        def get_star_parameters(args: dict) -> str:
            name = require_string(args, "name")
            self._verify_object_exists(name)
            p = client.call("scene", "get_star_parameters", {"name": name})
            if not p or len(p) < 7:
                return f"'{name}' is not a star in the loaded catalogues, so it has no astrometry to report."
            return (f"{name}: RA={format_number(p[0])} deg, dec={format_number(p[1])} deg, "
                    f"parallax={format_number(p[2])} mas, proper motion=({format_number(p[3])}, "
                    f"{format_number(p[4])}) mas/yr, radial velocity={format_number(p[5])} km/s, "
                    f"apparent magnitude={format_number(p[6])}.")

        self._add(Tool(
            "get_star_parameters",
            "Report the astrometry and photometry of a star as held in the loaded catalogue: position, "
            "parallax, proper motion, radial velocity and apparent magnitude. Only works for stars.",
            [Param.str("name", "Name or identifier of the star.", True)],
            False, get_star_parameters))

        def find_object(args: dict) -> str:
            name = require_string(args, "name")
            found = self._object_position(name) is not None
            return (f"Yes, '{name}' exists in the loaded datasets." if found else
                    f"No object named '{name}' is present. It may not be in the loaded datasets, "
                    f"or it may be known under a different name or catalogue identifier.")

        self._add(Tool(
            "find_object",
            "Check whether an object exists in the currently loaded datasets. Use this to verify a name "
            "before acting on it, or when the user asks whether something is available. To check many "
            "names at once, such as every stop of a long tour, use find_objects instead: it does the work "
            "in a single call rather than one per name.",
            [Param.str("name", "Name or identifier to look for.", True)],
            False, find_object))

        def find_objects(args: dict) -> str:
            raw = require_string(args, "names")
            names = [n.strip() for n in raw.replace("\n", ",").split(",") if n.strip()]
            if not names:
                raise ToolError("No names given.")
            if len(names) > 80:
                raise ToolError(f"Got {len(names)} names; at most 80 fit in one call. Split into batches.")
            found, missing = [], []
            for candidate in names:
                if self._object_position(candidate) is not None:
                    found.append(candidate)
                else:
                    missing.append(candidate)
            parts = []
            if found:
                parts.append(f"Present ({len(found)}): " + ", ".join(found) + ".")
            if missing:
                parts.append(f"Not found ({len(missing)}): " + ", ".join(missing) + ".")
            return " ".join(parts)

        self._add(Tool(
            "find_objects",
            "Check whether many objects exist in the currently loaded datasets, in one call. Use this to "
            "verify every stop of a tour before starting it, instead of calling find_object once per name. "
            "Reports which of the given names are present and which are not.",
            [Param.str("names", "The names or identifiers to check, separated by commas or newlines. "
                       "Up to 80 per call.", True)],
            False, find_objects))

        def search_objects(args: dict) -> str:
            query = require_string(args, "query")
            limit = int(optional_double(args, "limit", 10.0))
            # Gaia Sky exposes no REST-reachable fuzzy search (it used an internal,
            # in-process index). This degrades honestly to a handful of exact-ish
            # variants rather than pretending to search the catalogue.
            candidates = []
            for variant in (query, query.title(), query.upper(), query.lower()):
                if variant not in candidates:
                    candidates.append(variant)
            for prefix in ("HIP ", "HD ", "Gaia DR3 "):
                candidate = prefix + query
                if candidate not in candidates:
                    candidates.append(candidate)
            found = []
            for candidate in candidates[:max(limit, 1) + 6]:
                if self._object_position(candidate) is not None and candidate not in found:
                    found.append(candidate)
                if len(found) >= limit:
                    break
            if not found:
                return (f"No objects found matching '{query}'. Note: this external agent has no fuzzy search "
                        f"available (Gaia Sky does not expose one over REST), so only exact-ish variants of the "
                        f"name were tried. Ask the user for the precise name or catalogue identifier if this "
                        f"matters.")
            return "Found matching objects: " + ", ".join(found)

        self._add(Tool(
            "search_objects",
            "Search for objects in the loaded datasets using partial names or inexact words. Use this to find "
            "an object when you are unsure of its exact name, or to find related objects. Note: over the REST "
            "API this can only try a few exact-ish variants of the name, not a real fuzzy search.",
            [Param.str("query", "The partial name or word to search for.", True),
             Param.num("limit", "Maximum number of results to return. Defaults to 10.", False)],
            False, search_objects))

        def get_closest_object(args: dict) -> str:
            closest = client.call("camera", "get_closest_object")
            if closest is None:
                return "There is no object near the camera right now."
            # Gaia Sky serializes the IFocus with libgdx's Json, whose shape is not
            # guaranteed; read a name out of it defensively rather than assume a schema.
            name = None
            if isinstance(closest, dict):
                for key in ("name", "names", "candidateName"):
                    value = closest.get(key)
                    if isinstance(value, str) and value:
                        name = value
                        break
                    if isinstance(value, list) and value:
                        name = str(value[0])
                        break
            elif isinstance(closest, str):
                name = closest
            if not name:
                return "There is an object near the camera, but its name could not be read from the response."
            return f"The closest object to the camera is {name}."

        self._add(Tool(
            "get_closest_object",
            "Report which object is currently closest to the camera. Useful for answering 'where am I' or "
            "'what is that' style questions.",
            [], False, get_closest_object))

        def measure_distance(args: dict) -> str:
            obj1 = require_string(args, "object1")
            obj2 = require_string(args, "object2")
            units = optional_string(args, "units", "km")
            p1 = self._object_position(obj1, units)
            p2 = self._object_position(obj2, units)
            if p1 is None:
                return f"Object '{obj1}' not found."
            if p2 is None:
                return f"Object '{obj2}' not found."
            dist = sum((b - a) ** 2 for a, b in zip(p1, p2)) ** 0.5
            return f"The distance between {obj1} and {obj2} is {format_number(dist)} {units}."

        self._add(Tool(
            "measure_distance",
            "Measure the straight-line distance between two objects.",
            [Param.str("object1", "Name of the first object.", True),
             Param.str("object2", "Name of the second object.", True),
             Param.str("units", "Distance units to return (m, km, au, ly, pc, internal). Defaults to km.", False)],
            False, measure_distance))

        def look_at_coordinates(args: dict) -> str:
            ra = require_double(args, "ra")
            dec = require_double(args, "dec")
            client.call("camera", "set_direction_equatorial", {"ra": ra, "dec": dec})
            return f"The camera is now pointing at RA {ra}°, DEC {dec}°."

        self._add(Tool(
            "look_at_coordinates",
            "Point the camera at specific equatorial coordinates (Right Ascension and Declination).",
            [Param.num("ra", "Right Ascension in decimal degrees.", True),
             Param.num("dec", "Declination in decimal degrees.", True)],
            True, look_at_coordinates))

        def get_camera_state(args: dict) -> str:
            pos = client.call("camera", "get_position", {"units": "pc"})
            direction = client.call("camera", "get_direction")
            parts = []
            if pos and len(pos) >= 3:
                parts.append(f"Camera position: x={format_number(pos[0])} pc, y={format_number(pos[1])} pc, "
                              f"z={format_number(pos[2])} pc.")
            if direction and len(direction) >= 3:
                parts.append(f" Direction: ({format_number(direction[0])}, {format_number(direction[1])}, "
                              f"{format_number(direction[2])}).")
            fov = client.call("camera", "get_fov")
            parts.append(f" Field of view: {format_number(fov)} degrees.")
            return "".join(parts)

        self._add(Tool(
            "get_camera_state",
            "Report where the camera is, where it is pointing and how wide its field of view is.",
            [], False, get_camera_state))

        def get_application_info(args: dict) -> str:
            version = client.call("base", "get_version")
            build = client.call("base", "get_build_string")
            data_dir = client.call("base", "get_data_dir")
            config_dir = client.call("base", "get_config_dir")
            return (f"Gaia Sky {version} (build {build}). Data directory: {data_dir}. "
                    f"Configuration directory: {config_dir}.")

        self._add(Tool(
            "get_application_info",
            "Report the version of Gaia Sky that is running and where its data and configuration live. Use "
            "this when the user asks about the application itself rather than about the sky.",
            [], False, get_application_info))

    # ==================================================================== time

    def _register_time(self) -> None:
        client = self.client

        def set_time(args: dict) -> str:
            year = int(require_double(args, "year"))
            month = int(clamp(require_double(args, "month"), 1, 12))
            day = int(clamp(require_double(args, "day"), 1, 31))
            hour = int(clamp(optional_double(args, "hour", 12), 0, 23))
            minute = int(clamp(optional_double(args, "minute", 0), 0, 59))
            second = int(clamp(optional_double(args, "second", 0), 0, 59))
            duration = animation_seconds(args, 0.0)
            if duration <= 0:
                client.call("time", "set_clock",
                            {"yr": year, "month": month, "day": day, "hour": hour, "min": minute,
                             "sec": second, "ms": 0})
            else:
                client.call("time", "transition",
                            {"yr": year, "month": month, "day": day, "hour": hour, "min": minute,
                             "sec": second, "ms": 0, "duration": duration, "smooth_type": SMOOTH_TYPE,
                             "smooth_factor": 12.0, "sync": True}, timeout=duration + 60)
            return f"Simulation time set to {year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d} UTC."

        self._add(Tool(
            "set_time",
            "Set the simulation date and time (UTC). Use this when the user wants to see the sky at a "
            "particular moment, whether historical or in the future.",
            [Param.integer("year", "Year, for example 2026. Use a negative value for BCE.", True),
             Param.integer("month", "Month of the year, from 1 to 12.", True),
             Param.integer("day", "Day of the month, from 1 to 31.", True),
             Param.integer("hour", "Hour of the day, from 0 to 23. Defaults to 12.", False),
             Param.integer("minute", "Minute of the hour, from 0 to 59. Defaults to 0.", False),
             Param.integer("second", "Second of the minute, from 0 to 59. Defaults to 0.", False),
             Param.num("duration", "Seconds to animate the change over. Defaults to 0, which "
                       "jumps straight there. A few seconds makes the change easier to follow.", False)],
            True, set_time))

        def set_time_warp(args: dict) -> str:
            warp = require_double(args, "warp")
            client.call("time", "set_time_warp", {"warp": warp})
            return f"Time warp set to {format_number(warp)}x."

        self._add(Tool(
            "set_time_warp",
            "Set how fast simulation time advances relative to real time. A warp of 1 is real time, 3600 "
            "makes one second of real time equal one hour of simulation time, and negative values "
            "run time backwards.",
            [Param.num("warp", "Time warp factor. For example 1, 3600, 86400 or -3600.", True)],
            True, set_time_warp))

        def set_time_running(args: dict) -> str:
            running = require_bool(args, "running")
            client.call("time", "start_clock" if running else "stop_clock")
            return "Simulation time is now running." if running else "Simulation time is now paused."

        self._add(Tool(
            "set_time_running",
            "Start or stop the flow of simulation time.",
            [Param.bool("running", "True to start the clock, false to pause it.", True)],
            True, set_time_running))

        def get_time(args: dict) -> str:
            millis = client.call("time", "get_clock")
            running = client.call("time", "is_clock_on")
            instant = datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
            return (f"Simulation time is {instant.year:04d}-{instant.month:02d}-{instant.day:02d} "
                    f"{instant.hour:02d}:{instant.minute:02d}:{instant.second:02d} UTC. "
                    f"The clock is {'running' if running else 'paused'}.")

        self._add(Tool(
            "get_time",
            "Report the current simulation date and time, and whether the clock is running.",
            [], False, get_time))

        def use_real_time(args: dict) -> str:
            real = require_bool(args, "real")
            client.call("time", "activate_real_time_frame" if real else "activate_simulation_time_frame")
            return "The clock now follows real time." if real else "The clock now uses simulation time."

        self._add(Tool(
            "use_real_time",
            "Switch the clock between the real current time and the simulation time frame.",
            [Param.bool("real", "True to follow the real clock, false to use simulation time.", True)],
            True, use_real_time))

    # ==================================================================== visibility and the scene

    def _register_visibility(self) -> None:
        client = self.client

        def set_visibility(args: dict) -> str:
            type_name = require_string(args, "type")
            visible = require_bool(args, "visible")
            key = resolve_component_type(type_name)
            client.call("scene", "set_component_type_visibility", {"key": key, "visible": visible})
            return ("Showing " if visible else "Hiding ") + type_name + "."

        self._add(Tool(
            "set_visibility",
            "Show or hide a category of objects, such as stars, planets, orbits, constellations or labels.",
            [Param.str("type", f"Category to toggle. One of: {component_type_names()}.", True),
             Param.bool("visible", "True to show the category, false to hide it.", True)],
            True, set_visibility))

        def get_visibility(args: dict) -> str:
            type_name = require_string(args, "type")
            key = resolve_component_type(type_name)
            visible = client.call("scene", "get_component_type_visibility", {"key": key})
            return f"The category '{type_name}' is " + ("visible." if visible else "hidden.")

        self._add(Tool(
            "get_visibility",
            "Report whether a category of objects is currently shown.",
            [Param.str("type", f"Category to query. One of: {component_type_names()}.", True)],
            False, get_visibility))

        def set_object_visibility(args: dict) -> str:
            name = require_string(args, "name")
            visible = require_bool(args, "visible")
            self._verify_object_exists(name)
            client.call("scene", "set_object_visibility", {"name": name, "visible": visible})
            return ("Showing " if visible else "Hiding ") + name + "."

        self._add(Tool(
            "set_object_visibility",
            "Show or hide one individual object, leaving the rest of its category alone.",
            [Param.str("name", "Name or identifier of the object.", True),
             Param.bool("visible", "True to show it, false to hide it.", True)],
            True, set_object_visibility))

        def set_object_label(args: dict) -> str:
            name = require_string(args, "name")
            mode = require_string(args, "mode").lower()
            self._verify_object_exists(name)
            if mode == "always":
                client.call("scene", "set_force_display_label", {"name": name, "force": True})
            elif mode == "never":
                client.call("scene", "set_mute_label", {"name": name, "mute": True})
            elif mode == "auto":
                client.call("scene", "set_mute_label", {"name": name, "mute": False})
                client.call("scene", "set_force_display_label", {"name": name, "force": False})
            else:
                raise ToolError(f"Unknown label mode '{mode}'. Use 'always', 'never' or 'auto'.")
            return f"Label of {name} set to {mode}."

        self._add(Tool(
            "set_object_label",
            "Force an object's label to be shown at all times, or silence it, regardless of how far away the "
            "camera is. Useful for keeping a name on screen while explaining something.",
            [Param.str("name", "Name or identifier of the object.", True),
             Param.str("mode", "One of 'always', 'never' or 'auto'.", True)],
            True, set_object_label))

        def set_label_size(args: dict) -> str:
            factor = clamp(require_double(args, "factor"), MIN_LABEL_SIZE, MAX_LABEL_SIZE)
            client.call("scene", "set_label_size_factor", {"factor": factor})
            return f"Label size factor set to {format_number(factor)}."

        self._add(Tool(
            "set_label_size",
            "Scale every label in the scene up or down.",
            [Param.num("factor", f"Size factor, between {format_number(MIN_LABEL_SIZE)} and "
                       f"{format_number(MAX_LABEL_SIZE)}. 1 is the default size.", True)],
            True, set_label_size))

        def set_line_width(args: dict) -> str:
            factor = clamp(require_double(args, "factor"), MIN_LINE_WIDTH, MAX_LINE_WIDTH)
            client.call("scene", "set_line_width_factor", {"factor": factor})
            return f"Line width factor set to {format_number(factor)}."

        self._add(Tool(
            "set_line_width",
            "Scale the width of orbits, trajectories and other lines up or down.",
            [Param.num("factor", f"Width factor, between {format_number(MIN_LINE_WIDTH)} and "
                       f"{format_number(MAX_LINE_WIDTH)}. 1 is the default width.", True)],
            True, set_line_width))

        def add_shape_around_object(args: dict) -> str:
            name = require_string(args, "name")
            obj = require_string(args, "object")
            size = require_double(args, "size")
            shape = optional_string(args, "shape", "sphere").upper()
            primitive = optional_string(args, "primitive", "lines").upper()
            r = clamp(optional_double(args, "red", 0.4), 0, 1)
            g = clamp(optional_double(args, "green", 0.7), 0, 1)
            b = clamp(optional_double(args, "blue", 1.0), 0, 1)
            a = clamp(optional_double(args, "alpha", 0.4), 0, 1)
            self._verify_object_exists(obj)
            client.call("scene", "add_shape_around_object",
                        {"name": name, "shapeType": shape, "primitive": primitive, "size": size,
                         "obj_name": obj, "r": r, "g": g, "b": b, "a": a, "label": True, "track": True})
            return f"Added a {shape.lower()} of {format_number(size)} km around {obj}, named '{name}'."

        self._add(Tool(
            "add_shape_around_object",
            "Draw a translucent shape centred on an object, to convey a scale or a boundary. Useful for "
            "illustrating a distance, such as a sphere of one light year around a star.",
            [Param.str("name", "A name for the shape, so that it can be removed later.", True),
             Param.str("object", "Object the shape is centred on.", True),
             Param.num("size", "Diameter of the shape, in kilometres.", True),
             Param.str("shape", "One of: sphere, icosphere, octahedronsphere, cylinder, ring, cone. "
                       "Defaults to sphere.", False),
             Param.str("primitive", "'lines' for a wireframe or 'triangles' for a solid. Defaults to lines.", False),
             Param.num("red", "Red component of the colour, from 0 to 1. Defaults to 0.4.", False),
             Param.num("green", "Green component of the colour, from 0 to 1. Defaults to 0.7.", False),
             Param.num("blue", "Blue component of the colour, from 0 to 1. Defaults to 1.", False),
             Param.num("alpha", "Opacity, from 0 to 1. Defaults to 0.4.", False)],
            True, add_shape_around_object))

        def remove_object(args: dict) -> str:
            name = require_string(args, "name")
            client.call("scene", "remove_object", {"name": name})
            return f"Removed '{name}'."

        self._add(Tool(
            "remove_object",
            "Remove an object that was added to the scene, such as a shape drawn earlier.",
            [Param.str("name", "Name of the object to remove.", True)],
            True, remove_object))

    # ==================================================================== datasets

    def _register_datasets(self) -> None:
        client = self.client

        def list_datasets(args: dict) -> str:
            datasets = client.call("data", "list_datasets")
            if not datasets:
                return "No datasets are loaded."
            return "Loaded datasets: " + ", ".join(datasets) + "."

        self._add(Tool(
            "list_datasets",
            "List the datasets currently loaded, by name. Use this before showing, hiding or highlighting one, "
            "since these operations need the exact dataset name.",
            [], False, list_datasets))

        def require_dataset(name: str) -> None:
            if not client.call("data", "dataset_exists", {"name": name}):
                raise ToolError(f"No dataset named '{name}' is loaded. Use list_datasets to see what is available.")

        def set_dataset_visibility(args: dict) -> str:
            name = require_string(args, "name")
            visible = require_bool(args, "visible")
            require_dataset(name)
            client.call("data", "show_dataset" if visible else "hide_dataset", {"name": name})
            return ("Showing dataset '" if visible else "Hiding dataset '") + name + "'."

        self._add(Tool(
            "set_dataset_visibility",
            "Show or hide a whole loaded dataset.",
            [Param.str("name", "Exact name of the dataset, as reported by list_datasets.", True),
             Param.bool("visible", "True to show the dataset, false to hide it.", True)],
            True, set_dataset_visibility))

        def highlight_dataset(args: dict) -> str:
            name = require_string(args, "name")
            highlight = require_bool(args, "highlight")
            color = int(optional_double(args, "color", 0))
            require_dataset(name)
            client.call("data", "highlight_dataset", {"name": name, "color_idx": color, "highlight": highlight})
            return (f"Highlighting dataset '{name}'." if highlight else
                    f"Dataset '{name}' is no longer highlighted.")

        self._add(Tool(
            "highlight_dataset",
            "Highlight a dataset in a distinct colour, so that it stands out from the rest of the scene.",
            [Param.str("name", "Exact name of the dataset, as reported by list_datasets.", True),
             Param.bool("highlight", "True to highlight it, false to return it to normal.", True),
             Param.integer("color", "Index into the highlight colour palette. Defaults to 0.", False)],
            True, highlight_dataset))

    # ==================================================================== graphics

    def _register_graphics(self) -> None:
        client = self.client

        def set_star_brightness(args: dict) -> str:
            value = clamp(require_double(args, "value"), MIN_STAR_BRIGHTNESS, MAX_STAR_BRIGHTNESS)
            client.call("graphics", "set_star_brightness", {"value": value})
            return f"Star brightness set to {format_number(value)}."

        self._add(Tool(
            "set_star_brightness", "Set how bright stars are drawn.",
            [Param.num("value", f"Brightness, between {format_number(MIN_STAR_BRIGHTNESS)} and "
                       f"{format_number(MAX_STAR_BRIGHTNESS)}.", True)],
            True, set_star_brightness))

        def set_star_size(args: dict) -> str:
            value = clamp(require_double(args, "value"), MIN_POINT_SIZE_SCALE, MAX_POINT_SIZE_SCALE)
            client.call("graphics", "set_point_size", {"size": value})
            return f"Star size set to {format_number(value)}."

        self._add(Tool(
            "set_star_size", "Set the size stars are drawn at.",
            [Param.num("value", f"Point size, between {format_number(MIN_POINT_SIZE_SCALE)} and "
                       f"{format_number(MAX_POINT_SIZE_SCALE)}.", True)],
            True, set_star_size))

        def set_ambient_light(args: dict) -> str:
            value = clamp(require_double(args, "value"), MIN_AMBIENT_LIGHT, MAX_AMBIENT_LIGHT)
            client.call("graphics", "set_ambient_light", {"value": value})
            return f"Ambient light set to {format_number(value)}."

        self._add(Tool(
            "set_ambient_light", "Set the ambient light level, which lifts the unlit side of planets out of "
            "complete darkness.",
            [Param.num("value", "Ambient light, from 0 to 1.", True)],
            True, set_ambient_light))

        def set_bloom(args: dict) -> str:
            value = clamp(require_double(args, "value"), 0, 1)
            client.call("graphics", "effect_bloom", {"value": value})
            return f"Bloom set to {format_number(value)}."

        self._add(Tool(
            "set_bloom", "Set the strength of the bloom effect, which makes bright areas glow.",
            [Param.num("value", "Bloom strength, from 0 to 1.", True)],
            True, set_bloom))

        def set_visual_effect(args: dict) -> str:
            effect = require_string(args, "effect").lower().replace(" ", "_")
            enabled = require_bool(args, "enabled")
            mapping = {"lens_flare": "effect_lens_flare", "motion_blur": "effect_motion_blur",
                       "star_glow": "effect_star_glow"}
            method = mapping.get(effect)
            if method is None:
                raise ToolError(f"Unknown effect '{effect}'. Valid effects are: lens_flare, motion_blur, star_glow.")
            param = "active" if effect == "motion_blur" else "state"
            client.call("graphics", method, {param: enabled})
            return ("Enabled " if enabled else "Disabled ") + effect.replace("_", " ") + "."

        self._add(Tool(
            "set_visual_effect", "Switch one of the visual effects on or off.",
            [Param.str("effect", "One of: lens_flare, motion_blur, star_glow.", True),
             Param.bool("enabled", "True to enable the effect, false to disable it.", True)],
            True, set_visual_effect))

        def set_image_adjustment(args: dict) -> str:
            prop = require_string(args, "property").lower()
            value = require_double(args, "value")
            mapping = {"brightness": "set_image_brightness", "contrast": "set_image_contrast",
                       "hue": "set_image_hue", "saturation": "set_image_saturation",
                       "gamma": "set_gamma_correction"}
            method = mapping.get(prop)
            if method is None:
                raise ToolError(f"Unknown property '{prop}'. Valid properties are: brightness, contrast, hue, "
                                 f"saturation, gamma.")
            client.call("graphics", method, {"level": value})
            return f"{prop.capitalize()} set to {format_number(value)}."

        self._add(Tool(
            "set_image_adjustment",
            "Adjust the image the way a display would: brightness, contrast, hue, saturation or gamma.",
            [Param.str("property", "One of: brightness, contrast, hue, saturation, gamma.", True),
             Param.num("value", "The level to set. 1 is neutral for all of them except brightness and contrast, "
                       "which are neutral at 1 as well. Gamma runs from 0.5 to 3.", True)],
            True, set_image_adjustment))

        def set_tone_mapping(args: dict) -> str:
            tone_type = require_string(args, "type").lower()
            if tone_type not in ("auto", "exposure", "none"):
                raise ToolError(f"Unknown tone mapping type '{tone_type}'. Valid types are: auto, exposure, none.")
            client.call("graphics", "set_hdr_tone_mapping", {"type": tone_type})
            if tone_type == "exposure":
                exposure = clamp(optional_double(args, "exposure", 1.0), 0, 20)
                client.call("graphics", "set_exposure_tone_mapping", {"level": exposure})
                return f"Tone mapping set to exposure, at level {format_number(exposure)}."
            return f"Tone mapping set to {tone_type}."

        self._add(Tool(
            "set_tone_mapping", "Set the tone mapping curve used to map the rendered image onto the display.",
            [Param.str("type", "One of: auto, exposure, none.", True),
             Param.num("exposure", "Exposure level, from 0 to 20. Only used with the exposure type.", False)],
            True, set_tone_mapping))

        def set_projection_mode(args: dict) -> str:
            mode = require_string(args, "mode").lower()
            if mode == "normal":
                client.call("graphics", "mode_panorama", {"state": False})
                client.call("graphics", "mode_stereoscopic", {"state": False})
            elif mode == "panorama":
                client.call("graphics", "mode_panorama", {"state": True})
            elif mode == "planetarium":
                client.call("graphics", "mode_planetarium", {"state": True})
            elif mode == "orthosphere":
                client.call("graphics", "mode_orthosphere", {"state": True})
            elif mode == "stereoscopic":
                client.call("graphics", "mode_stereoscopic", {"state": True})
            else:
                raise ToolError(f"Unknown mode '{mode}'. Valid modes are: normal, panorama, planetarium, "
                                 f"orthosphere, stereoscopic.")
            return f"Projection mode set to {mode}."

        self._add(Tool(
            "set_projection_mode",
            "Switch the whole view into one of the alternative projection modes, or back to normal.",
            [Param.str("mode", "One of: normal, panorama, planetarium, orthosphere, stereoscopic.", True)],
            True, set_projection_mode))

        def take_screenshot(args: dict) -> str:
            client.call("output", "screenshot")
            directory = client.call("output", "get_current_screenshots_dir")
            return f"Screenshot saved to {directory}."

        self._add(Tool(
            "take_screenshot", "Save a screenshot of the current view to the screenshots directory.",
            [], True, take_screenshot))

    # ==================================================================== user interface

    def _register_interface(self) -> None:
        # No tool here writes text over the Gaia Sky view itself: Gaia Sky's own
        # on-screen widgets sit at screen positions the REST API cannot move, and
        # duplicating the assistant's words as text embedded in the 3D view reads as
        # clutter now that a proper chat window exists. show_notification below shows
        # its own floating, self-closing banner (see ui.py's NotificationToast) — not
        # drawn inside Gaia Sky, and not posted into the chat transcript either.
        client = self.client

        def show_notification(args: dict) -> str:
            message = require_string(args, "message")
            return message

        self._add(Tool(
            "show_notification",
            "Show a short, one-line floating notification over the screen (not inside Gaia Sky itself, and "
            "not part of your reply). Use this sparingly for a brief heads-up, such as announcing what is "
            "about to happen next on a tour. Only one is shown at a time, so do not call this repeatedly in "
            "quick succession.",
            [Param.str("message", "The text to show. Keep it to one line.", True)],
            False, show_notification))

        valid_panes = ["Time", "Camera", "Visibility", "VisualSettings", "Datasets", "LocationLog", "Bookmarks"]

        def set_pane_expanded(args: dict) -> str:
            pane = require_string(args, "pane")
            expanded = require_bool(args, "expanded")
            resolved = next((v for v in valid_panes if v.lower() == pane.lower()), None)
            if resolved is None:
                raise ToolError(f"Unknown pane '{pane}'. Valid panes are: {', '.join(valid_panes)}.")
            client.call("ui", "expand_pane" if expanded else "collapse_pane", {"name": resolved})
            return ("Expanded the " if expanded else "Collapsed the ") + resolved + " pane."

        self._add(Tool(
            "set_pane_expanded",
            "Expand or collapse one of the panes in the controls panel on the left.",
            [Param.str("pane", f"One of: {', '.join(valid_panes)}.", True),
             Param.bool("expanded", "True to expand the pane, false to collapse it.", True)],
            True, set_pane_expanded))

        def set_ui_element_visibility(args: dict) -> str:
            element = require_string(args, "element").lower()
            visible = require_bool(args, "visible")
            if element == "minimap":
                client.call("ui", "set_minimap_visibility", {"visible": visible})
            elif element == "crosshair":
                client.call("ui", "set_crosshair_visibility", {"visible": visible})
            else:
                raise ToolError(f"Unknown element '{element}'. Valid elements are: minimap, crosshair.")
            return ("Showing the " if visible else "Hiding the ") + element + "."

        self._add(Tool(
            "set_ui_element_visibility", "Show or hide one of the overlay elements of the interface.",
            [Param.str("element", "One of: minimap, crosshair.", True),
             Param.bool("visible", "True to show the element, false to hide it.", True)],
            True, set_ui_element_visibility))

    # ==================================================================== flow

    def _register_flow(self) -> None:
        def wait(args: dict) -> str:
            seconds = clamp(require_double(args, "seconds"), 0.0, MAX_WAIT_SECONDS)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if self.stop.is_set():
                    return f"The wait was interrupted after {format_number(seconds - (deadline - time.monotonic()))} seconds."
                time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))
            return f"Waited {format_number(seconds)} seconds. Continue with the task."

        self._add(Tool(
            "wait",
            "Pause for a number of seconds before doing anything else. Use this when the user asks for "
            "timed pauses, such as staying at each stop of a tour for a few seconds before moving "
            "on, or when something on screen needs a moment to develop before you look at it "
            "again. Time passes in real time while you wait; the simulation keeps running.",
            [Param.num("seconds", f"How long to wait, in seconds. At most {int(MAX_WAIT_SECONDS)}.", True),
             Param.str("reason", "Optional short note on what the pause is for, shown to the user, for example "
                       "'contemplating Saturn'.", False)],
            False, wait))

    # ==================================================================== notes and lists
    #
    # Plain text files on disk, entirely local to this app (no REST calls). They give the
    # model somewhere durable to put a tour plan, an itinerary, a checklist or any other
    # list-shaped output the user wants to keep, independent of chat scrollback. They also
    # let the model build up a long-running task (a 40-stop tour, say) incrementally across
    # many turns without having to hold the whole thing in one reply.

    def _register_notes(self) -> None:
        def notes_dir():
            from .config import config_dir
            directory = config_dir() / "notes"
            directory.mkdir(parents=True, exist_ok=True)
            return directory

        def note_path(title: str):
            safe = "".join(c if c.isalnum() or c in " ._-" else "_" for c in title).strip(" .") or "note"
            return notes_dir() / (safe[:120] + ".txt")

        def save_note(args: dict) -> str:
            title = require_string(args, "title")
            content = require_string(args, "content")
            append = optional_bool(args, "append", False)
            path = note_path(title)
            if append and path.exists():
                with path.open("a", encoding="utf-8") as f:
                    f.write("\n" + content)
                return f"Appended to '{title}' at {path}."
            path.write_text(content, encoding="utf-8")
            return f"Saved '{title}' to {path}."

        self._add(Tool(
            "save_note",
            "Save a piece of text to a plain text file the user can keep, independent of this chat. Use "
            "this for anything list-shaped or worth keeping as a document: a tour itinerary, a list of "
            "objects with notes on each, a checklist, a summary. Also useful for building up a long task "
            "over many turns, such as logging each stop of a long tour as you go, so the work survives "
            "even if the conversation is interrupted. Writing to an existing title overwrites it unless "
            "'append' is set.",
            [Param.str("title", "A short name for the note, used as its file name.", True),
             Param.str("content", "The text to write. May be as long as needed, and may use Markdown "
                       "formatting such as lists and headings.", True),
             Param.bool("append", "True to add this content to the end of an existing note instead of "
                        "replacing it. Defaults to false.", False)],
            True, save_note))

        def read_note(args: dict) -> str:
            title = require_string(args, "title")
            path = note_path(title)
            if not path.exists():
                raise ToolError(f"No note named '{title}'. Use list_notes to see what is saved.")
            return path.read_text(encoding="utf-8")

        self._add(Tool(
            "read_note",
            "Read back a note saved earlier with save_note. Use this to check progress on a long-running "
            "task, or to recall a list written down in an earlier turn.",
            [Param.str("title", "Name of the note to read.", True)],
            False, read_note))

        def list_notes(args: dict) -> str:
            paths = sorted(notes_dir().glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not paths:
                return "No notes saved yet."
            return "Saved notes: " + ", ".join(p.stem for p in paths) + "."

        self._add(Tool(
            "list_notes",
            "List the titles of all notes saved so far with save_note.",
            [], False, list_notes))

        def delete_note(args: dict) -> str:
            title = require_string(args, "title")
            path = note_path(title)
            if not path.exists():
                raise ToolError(f"No note named '{title}'.")
            path.unlink()
            return f"Deleted note '{title}'."

        self._add(Tool(
            "delete_note",
            "Delete a note saved earlier with save_note. Use this only when the user asks to remove or "
            "clear one, not to tidy up on your own initiative.",
            [Param.str("title", "Name of the note to delete.", True)],
            True, delete_note))


SYSTEM_PROMPT = """\
You are the in-app assistant for Gaia Sky, an open-source astronomy visualization program that renders \
a scientifically accurate 3D universe built from the Gaia mission catalogues.

You help the user explore and understand what they are seeing. You can drive the application through the \
tools available to you: flying the camera, querying the running simulation, changing the date and time, \
controlling what is visible, and adjusting the rendering. You talk to the user only through your own \
replies, in this chat window — none of your tools write text over the Gaia Sky view itself.

Guidelines:
- When the user asks to go somewhere or see something, use the tools rather than only describing it.
- Before quoting distances, sizes or dates, query them with the tools. The simulation is the source of \
truth; do not rely on recalled figures when a tool can give you the real one.
- If an object is not found, say so plainly and suggest an alternative name or catalogue identifier. \
Do not pretend an action succeeded.
- You may chain several tools to fulfil one request, for example setting the date and then flying to a planet.
- Keep replies short and conversational. The user is looking at the visualization, not at a wall of text. \
Explain the astronomy when it adds something, but do not lecture.
- Replies are rendered as Markdown, so headings, lists, tables, code fences and emphasis all display \
properly. Use them where they help, and prose where they do not; a table is for comparing figures, \
not for narrating a journey.
- When planning a tour or a sequence of actions involving specific celestial objects, verify their \
presence in the currently loaded datasets before promising to visit them. For more than a couple of \
names, check them all in one go with find_objects (one call, names separated by commas) rather than \
calling find_object once per name — this matters for long tours, where checking forty names one by one \
would be needlessly slow. However, for general knowledge questions, you may answer based on your \
internal knowledge even if the object is not loaded.

Guided tours:
- A tour means flying the camera. Listing the objects instead is not a tour, and is not what \
was asked for. Begin the first stop straight away, in the same turn the tour is requested: call \
go_to_object before you write anything else. Never answer a tour request with a table, a list or a \
plan, and never offer to fly somewhere "if you like" — the user has already asked you to.
- Do not change camera modes (like toggling cinematic camera or changing field of view) during a tour unless explicitly requested.
- Work through the tour yourself from beginning to end, without stopping to ask whether to continue. \
This applies just as much to a long tour (dozens of stops) as to a short one: keep going, stop after \
stop, until every one has been visited. There is no limit on how many stops you may make in one tour; \
narrating between flights is enough to keep going indefinitely.
- Do as many stops as the user asked for. If they ask for a hundred objects, visit a hundred; the \
length of a tour is theirs to decide, not yours. Never refuse or shorten one because it seems long, \
and never propose a smaller number instead. The user can stop you at any moment.
- Handle one stop per turn, in this order: call go_to_object and let it wait for the camera to arrive; \
then narrate two or three sentences about the object in your reply. Only after that request the next stop.
- When the user asks to stay at each stop for some time, or to pause between actions, use the wait \
tool with that many seconds after narrating and before moving on. The pause is real; never pretend to \
wait by writing filler, and never skip a pause the user asked for.
- Never call go_to_object twice in the same turn. The second call will be declined, because the user \
would otherwise arrive somewhere new before hearing about the last place.
- If the user wants a written itinerary of a tour, a list of objects to visit later, or any other list-\
shaped text kept as a document, use save_note rather than only putting it in your reply — that way it \
survives after the chat scrolls away. This is separate from actually running a tour: a note is a list \
kept for later, not a substitute for flying the camera when a tour is asked for.

Working with tools:
- Do what is asked. Never decline a request because it involves a lot of steps, and never ask for \
permission to carry on with work already asked for.
- Do not repeat the call you have just made with the same arguments; it will be declined. If a tool \
did not achieve what you wanted, either use a different one or explain the situation to the user.
- show_notification is the one exception to "tools act, they don't talk": it shows a brief floating \
heads-up over the screen, separate from your reply. Use it sparingly and only once per turn — it is a \
side note, not a replacement for saying the substance of what happened in your reply.
- When you have done what was asked, answer the user. Do not keep calling tools once the task is done.

Language:
- Always reply in the language of the message you are answering. If the user writes in Spanish, \
answer in Spanish; if in Catalan, in Catalan; and so on for any other language.
- Keep to that language throughout the reply, including headings and table headers. Do not change \
language part way through, and do not fall back to English because a tool returned its result in English.
- Follow the user when they change language, from the message in which they change it onwards.
- Object names, catalogue identifiers and units are written as they are, whatever the language."""


def system_prompt(extra: str | None = None) -> str:
    """Builds the system prompt, appending any user-supplied additions from the configuration."""
    if extra and extra.strip():
        return SYSTEM_PROMPT + "\n\nAdditional instructions:\n" + extra.strip()
    return SYSTEM_PROMPT
