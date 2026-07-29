# Gaia Sky AI Agent — Plan de implementación

Aplicación **totalmente externa** que reimplementa el asistente IA agéntico que se desarrolló
dentro de Gaia Sky (PR rechazado por estar fuera de alcance), como una app independiente que:

- Se comunica con Gaia Sky **exclusivamente por su API REST** (sin tocar una sola línea de Gaia Sky).
- Muestra el chat **visualmente dentro** de Gaia Sky mediante una ventana *overlay*
  (sin bordes, translúcida, siempre encima), de modo que parece un panel nativo.
- Funciona contra los **builds oficiales** (AppImage de Linux y versión Windows), **sin depender
  de ninguno de nuestros fixes** (thread-safety en CameraModule, sync de `go_to_object`,
  endurecimiento de OctreeNode). Ese es el requisito central: todo vive fuera.

El código original a portar son ~4400 líneas Java: `AiAgent`, `AiClient`, `AiToolRegistry`
(~40 herramientas), `AiConversation(Store)`, `AiChatInterface`, `AiMarkdown`, `AiSettingsWindow`.

---

## 1. Arquitectura

```
┌───────────────────────────────┐        HTTP (localhost)        ┌──────────────────────┐
│  gaiaskyAIagent (Python)      │                                │  Gaia Sky (oficial)  │
│                               │  GET /apiv2/camera/go_to_object│                      │
│  ┌─────────┐   ┌───────────┐  │  GET /apiv2/scene/...          │  Servidor REST       │
│  │ Overlay │←→│  Agente    │──┼───────────────────────────────→│  (Spark, APIv2)      │
│  │ PyQt6   │   │ (bucle de │  │                                │  restPort: 30007     │
│  └─────────┘   │  tools)   │  │                                └──────────────────────┘
│                └─────┬─────┘  │
│                      │ chat/completions (tool calling)
│                      ▼
│    Ollama nativo  ó  endpoint OpenAI-compatible
└───────────────────────────────┘
```

- **Lenguaje**: Python 3.10+. Dependencias mínimas: `PyQt6` (UI) y `requests` (HTTP).
- **Sin backend en Java**: el "harness" completo (registro de herramientas, bucle agéntico,
  backends LLM, persistencia de conversaciones, UI) se porta a Python.
- Gaia Sky ni se parchea ni se recompila: solo hay que activar `restPort` en su `config.yaml`.

## 2. Restricción clave: funcionar contra Gaia Sky *vanilla*

Análisis de cada fix que llevaba el PR y qué implica no tenerlo:

| Fix (commit local) | Qué arreglaba | Impacto en la app externa | Mitigación |
|---|---|---|---|
| `a6dd3357d` (parte): sobrecarga de 4 args de `go_to_object(String,double,double,boolean)` ignoraba `sync` | Vuelos asíncronos se volvían síncronos | **Ninguno si evitamos esa sobrecarga.** La de **5 args** `go_to_object(name, sa, pos_duration, ori_duration, sync)` respeta `sync` también en vanilla | Llamar **siempre a la sobrecarga de 5 args con parámetros nombrados** (ver §4) |
| `f98928320` thread-safety en CameraModule (GlyphLayout corrupto al llamar la API desde hilos no-render) | Crashes esporádicos con comandos de cámara desde otro hilo | **Riesgo real**: el handler REST de Spark también invoca la API desde un hilo no-render. Es una carrera de datos poco frecuente; el scripting Py4J lleva años usándola así | Ritmo natural más lento (HTTP + latencia LLM), no encadenar cambios de foco innecesarios, y **enviar el fix como PR separado** (el maintainer se ofreció a revisarlos). **Resuelto upstream**: el commit `650fd82e3` ("fix: NPE and memory leak when octants fade out. Thread-safety issues in camera module.", 2026-07-29) envuelve los `em.post(...)` de `CameraModule` en `api.base.post_runnable(...)`, exactamente esta clase de fix. Vive en `master`; aún no confirmado en qué release/AppImage etiquetada cae, así que la mitigación de ritmo se mantiene por si el usuario corre un build más antiguo |
| `ad3ad1130` + `2b6a79a10` NPE/leak en octree al hacer fade-out | Crashes en vuelos largos por catálogos de estrellas | Riesgo residual idéntico al de cualquier usuario de vanilla; nada que hacer desde fuera | PR separado upstream. **Resuelto upstream** en el mismo commit `650fd82e3` (`OctreeUpdater.java`, `OctreeExtractor.java`, `ModelComponent.java`). Mismo matiz de versión que la fila de arriba |
| `b22801644` lag de UI (bucle infinito) | Spike de lag | Era en la UI del chat interno; **no aplica** (nuestra UI es propia) | — |
| `5bd4b396e`, `fcb60be44`, `cdd0dd71c`, `4b12ac8c7`, `afa0764b6`, `fb383dc5e` | Comportamiento del agente (modos de cámara en tours, ángulo objetivo, búsqueda, medir distancias, wait, esquema de Ollama Cloud) | Viven en el harness, **se portan íntegros a Python** (salvo `search_objects`, degradada; ver §5) | — |

**Acción paralela recomendada** (fuera de este proyecto): enviar 3 PRs individuales a
Codeberg con los fixes de CameraModule (thread-safety), `go_to_object` sync y OctreeNode.
El maintainer dijo explícitamente que los revisaría encantado. Benefician a esta app.

**Actualización 2026-07-29**: revisando el histórico de `gaiasky/` upstream, 2 de los 3
ya están mergeados en `master` (ver notas "Resuelto upstream" en la tabla de arriba):
thread-safety de `CameraModule` y NPE/leak de octree, ambos en `650fd82e3`. El tercero
(`go_to_object` de 4 args ignorando `sync`) sigue presente tal cual en
`CameraModule.java:679-684` — la mitigación de este cliente (llamar siempre a la
sobrecarga de 5 args nombrados, §4) sigue siendo necesaria. Aparte, el maintainer
también implementó, por su cuenta, la sugerencia de un toggle de `restPort` en la
ventana de Preferencias (`754ed269e`, `4f1d9652d`) — ver `automaticinstallationintegration.md`.
Ninguno de estos cambios altera el contrato REST (`/apiv2/<módulo>/<método>`) del que
depende este cliente; no ha hecho falta tocar `gaiasky.py`/`tools.py`.

## 3. Activar la API REST en los builds oficiales

- El servidor REST viene **desactivado** (`restPort: -1`). Hay que editar `config.yaml`:
  - Linux (AppImage): `~/.config/gaiasky/config.yaml`
  - Windows: `%USERPROFILE%\.gaiasky\config.yaml`
  - Clave: `program → net → restPort: 30007` (puerto de usuario libre, 1024–49151).
- Verificación: con Gaia Sky abierto, `http://localhost:30007/api/help` responde JSON.
- El servidor responde `"GUI not yet initialized"` (success=false) hasta que la UI arranca:
  la app debe hacer *ping* y esperar/reintentar.
- **Seguridad**: Spark escucha en todas las interfaces (no solo localhost) y el propio
  Gaia Sky avisa de que la API puede permitir ejecución remota. El README debe advertir:
  usar solo en redes de confianza / cortafuegos. La app siempre habla con `localhost`.

## 4. Cómo se llama a la API (mecánica REST, verificada en el código del servidor)

- Rutas: `GET/POST http://localhost:30007/apiv2/<módulo>/<método>?p1=v1&p2=v2`.
  Módulos: `base, camera, camera/interactive, time, scene, graphics, data, output, ui, …`
  (APIv1 en `/api/<método>` queda como reserva; usaremos APIv2, que es 1:1 con las tools Java).
- **Matching de sobrecargas**: el servidor elige el método cuyo número de parámetros coincide
  exactamente con los query params y cuyos **nombres** coinciden (o `arg0..argN`).
  El build oficial compila con `-parameters` (lo añadió el maintainer en abril 2026), así que
  los **nombres reales funcionan y desambiguan**. Regla del cliente:
  - por defecto `arg0..argN` (siempre válido);
  - **nombres reales cuando hay colisión de aridad**, p. ej. `go_to_object` de 5 args
    (`name=…&sa=…&pos_duration=…&ori_duration=…&sync=…` selecciona la variante String
    y nunca la variante Entity, que exigiría `object=`).
- Respuesta: JSON `{"value": …, "success": bool, "text": "…"}`; HTTP 400 si falla el matching.
- **Quirks a manejar en el cliente** (comprobados en `RESTServer.java`):
  1. Si el método invocado lanza excepción, el servidor **la traga y devuelve
     `success=true, value=null`** → no siempre se puede distinguir éxito de fallo; donde
     importe, verificar el efecto con una consulta posterior (p. ej. distancia a objeto).
  2. Retornos no primitivos (objetos `IFocus`, listas) se serializan con libgdx `Json` y
     pueden traer envoltorios (`{"class":…,"items":[…]}`) → parser defensivo.
  3. Llamadas `sync=true` **bloquean la petición HTTP** hasta acabar el vuelo/transición.
     Es seguro (el hilo de Spark no es el de render) y nos da la semántica "espera a llegar"
     del tool original **sin fix alguno**. El cliente usa timeouts generosos
     (`duración + 60 s`; aterrizajes: 600 s).
  4. Cancelación: una petición bloqueada no se puede abortar desde fuera, pero una llamada
     **paralela** a `camera/stop` termina la transición (Spark atiende peticiones concurrentes).
     Es el mecanismo de "Stop" del agente.

## 5. Mapeo de las ~40 herramientas → REST

Se conservan nombre, descripción, parámetros y textos de resultado del `AiToolRegistry` Java
(el modelo verá exactamente las mismas tools). Verificación de existencia de objetos:
`scene/get_object_position(name)` devuelve `null` si no existe (sustituye a `scene.get_object`,
que vía REST serializaría una entidad entera).

| Tool | Endpoint(s) APIv2 | Estado |
|---|---|---|
| go_to_object | `camera/go_to_object` (5 args nombrados, `sync` según `wait`) + `camera/focus_mode` + `camera/get_distance_to_object` | ✅ integra (con control de ángulo `sa`) |
| go_to_object_instant | `camera/go_to_object_instant` | ✅ |
| land_on | `camera/interactive/land_on` / `land_at_location` (nombre, lugar o lat+lon) | ✅ (sin la comprobación de atmósfera, que era interna; se confía en la API) |
| set_cinematic_camera | `camera/interactive/set_cinematic` | ✅ |
| focus_object / free_camera / stop_camera / center_camera | `camera/focus_mode` / `free_mode` / `stop` / `center` | ✅ |
| set_camera_speed / set_field_of_view / track_object / set_focus_lock / point_camera | `camera/set_max_speed`, `set_fov` o `transition_fov(…,sync)`, `set_tracking_object`/`remove_tracking_object`, `set_focus_lock`, `set_direction_equatorial`/`_galactic` | ✅ |
| get_object_info | `get_object_position` + `get_distance_to_object` + `scene/get_object_radius` + `get_object_visibility` | ✅ |
| get_star_parameters | `scene/get_star_parameters` → array de 7 doubles | ✅ |
| find_object | `scene/get_object_position` (null ⇒ no existe) | ✅ |
| **search_objects** | *(usaba `scene.matchingFocusableNodes`, interno, sin endpoint)* | ⚠️ **degradada**: prueba variantes exactas (tal cual, Title Case, MAYÚSCULAS, prefijos de catálogo comunes) con `get_object_position` e informa con honestidad de que no hay búsqueda difusa externa |
| **get_closest_object** | `camera/get_closest_object` | ⚠️ **frágil**: devuelve un objeto serializado por libgdx; se parsea el nombre defensivamente y si no, mensaje de no-disponible |
| measure_distance | 2 × `scene/get_object_position(name, units)` + cálculo local | ✅ |
| look_at_coordinates / get_camera_state / get_application_info | `set_direction_equatorial`; `get_position("pc")`+`get_direction`+`get_fov`; `base/get_version`+`get_build_string`+`get_data_dir`+`get_config_dir` | ✅ |
| set_time / set_time_warp / set_time_running / get_time / use_real_time | `time/set_clock` (7 args) o `time/transition` (11 args, `sync=true`); `set_time_warp`; `start_clock`/`stop_clock`; `get_clock`+`is_clock_on`; `activate_real_time_frame`/`activate_simulation_time_frame` | ✅ |
| set_visibility / get_visibility | `scene/set_component_type_visibility(key, visible)` — mapa local `stars→element.stars`, etc. (lista completa del enum ComponentType copiada al cliente) | ✅ |
| set_object_visibility / set_object_label / set_label_size / set_line_width | `scene/set_object_visibility`, `set_force_display_label`+`set_mute_label`, `set_label_size_factor` (0.7–2.5), `set_line_width_factor` (0.2–3.5) | ✅ |
| add_shape_around_object / remove_object | `scene/add_shape_around_object` (11 args) / `scene/remove_object` | ✅ |
| list_datasets / set_dataset_visibility / highlight_dataset | `data/list_datasets`, `dataset_exists`+`show_dataset`/`hide_dataset`, `highlight_dataset` | ✅ |
| set_star_brightness (0.4–8) / set_star_size (0.1–4) / set_ambient_light / set_bloom / set_visual_effect / set_image_adjustment / set_tone_mapping / set_projection_mode / take_screenshot | `graphics/*` y `output/screenshot`+`get_current_screenshots_dir` | ✅ |
| show_notification / show_explanation / clear_explanation / show_headline / clear_messages / set_pane_expanded / set_ui_element_visibility | `ui/display_popup_notification`, `display_text` (id fijo 8410, wrap local a 46 col / 20 líneas), `remove_object(8410)`, `set_headline_message`+`set_subhead_message`, `clear_all_messages`, `expand_pane`/`collapse_pane`, `set_minimap_visibility`/`set_crosshair_visibility` | ✅ |
| wait | Implementación local (sleep troceado en 100 ms honrando cancelación, máx. 120 s) | ✅ |

Constantes portadas de `Constants.java`: FOV 1–150, label 0.7–2.5, línea 0.2–3.5,
brillo estelar 0.4–8, tamaño 0.1–4, ambient 0–1, animaciones 1–120 s.

## 6. Port del bucle agéntico (`agent.py` ← `AiAgent.java`)

Se porta 1:1 la lógica que ya funcionaba:

- Bucle: enviar historial → si hay `tool_calls`, ejecutarlas y realimentar; si no, respuesta final.
- **Tools exclusivas** (`go_to_object`, `go_to_object_instant`, `land_on`): solo la primera de
  la ronda se ejecuta; el resto se rechaza con el mensaje que obliga a narrar antes de moverse.
- `refuse()`: rechaza repetición idéntica inmediata y >6 llamadas seguidas a la misma tool.
- Presupuesto de tool calls configurable (0 = ilimitado); al agotarse, petición de cierre
  **sin tools** ("Answer now, in prose…"), igual que en Java.
- Turno vacío tras tools → repregunta sin tools (mismo truco `finalAnswer`).
- Errores de tool devueltos al modelo como resultado (no abortan el intercambio).
- Hilo trabajador (threading) + callbacks; la UI los recibe vía señales Qt (thread-safe).
- **Cancelación**: evento `stop` (corta `wait` y el bucle) + llamada paralela a `camera/stop`.
- Prompt de sistema: el mismo de `AiToolRegistry.systemPrompt` (tours, idioma del usuario,
  no cambiar modos de cámara en tours, verificación previa con tools, etc.) + apéndice del
  usuario configurable.

## 7. Backends LLM (`llm.py` ← `AiClient.java`)

- **Ollama nativo** (`/api/chat`, no streaming) y **OpenAI-compatible** (`/v1/chat/completions`).
- Se portan: normalización de URL (`resolveEndpoint`, tolera URL con o sin sufijo de endpoint),
  esquemas de tools (function calling), `tool_name` para Ollama vs `tool_call_id` para OpenAI
  (incluye el fix de Ollama Cloud `fb383dc5e`), argumentos como objeto (Ollama) o string JSON
  (OpenAI), mensajes de error humanizados por código HTTP, `listModels`
  (`/api/tags` vs `/v1/models`), temperatura y timeout configurables.

## 8. UI overlay (`ui.py`)

- **Ventana**: `FramelessWindowHint | WindowStaysOnTopHint | Tool` + `WA_TranslucentBackground`.
  Panel redondeado semitransparente (opacidad configurable), arrastrable por la cabecera,
  redimensionable por los bordes (como hacía el panel Java). Botones: nueva conversación,
  historial, ajustes, minimizar a burbuja, cerrar.
- **Transcript**: burbujas usuario/asistente. El markdown del modelo se convierte con
  `QTextDocument.setMarkdown()` → **las tablas se renderizan como tablas reales de UI**
  con la fuente normal del sistema (exactamente lo que sugirió el maintainer; nada de
  monospace). Línea discreta y plegable por cada tool ejecutada.
- **Indicadores**: punto de estado de conexión con Gaia Sky (ping periódico en hilo aparte),
  spinner/"pensando…" durante el intercambio, botón Stop mientras corre.
- **Ajustes** (diálogo): URL de Gaia Sky, backend (Ollama/OpenAI), URL del servidor, API key,
  modelo (desplegable con botón de refresco contra el servidor), temperatura, timeout,
  presupuesto de tools, prompt adicional, opacidad y tamaño de fuente, modo overlay/ventana.
- **Limitaciones asumidas** (documentadas en README):
  - *Always-on-top* es fiable en Windows y X11; en **Wayland** puro no se puede forzar →
    ejecutar con `QT_QPA_PLATFORM=xcb` o usar regla de ventana de KDE, o `--window`.
  - Si Gaia Sky va en **fullscreen exclusivo**, el overlay no se ve → usar ventana sin
    bordes/maximizada. Es la misma limitación de cualquier overlay (Discord, Steam…).
  - Modo alternativo `--window`: ventana normal (para quien prefiera medio monitor).

## 9. Persistencia (`config.py`, `store.py`)

- Config JSON en `~/.config/gaiaskyAIagent/config.json` (Linux) / `%APPDATA%\GaiaSkyAIAgent\`
  (Windows). Nunca se guarda la API key en texto plano en el repo; solo en la config del usuario.
- Conversaciones como JSON individuales (título derivado del primer mensaje, timestamps),
  listado/reanudar/borrar desde la UI; al reanudar se reconstruye el turno de sistema con el
  prompt vigente (igual que hacía `AiAgent.load`).

## 10. Estructura del repositorio

```
gaiaskyAIagent/
├── PLAN.md                  ← este documento
├── README.md                (instalación, activar REST, seguridad, empaquetado; EN + guía ES)
├── requirements.txt         (PyQt6, requests)
├── run.py                   (entrada; también objetivo de PyInstaller)
├── .gitignore
└── gaiasky_agent/
    ├── __init__.py
    ├── main.py              argumentos CLI (--window, --gaiasky URL, --cli), arranque Qt
    ├── config.py            carga/guardado de configuración          (~120 líneas)
    ├── gaiasky.py           cliente REST (argN/nombres, quirks §4)   (~180)
    ├── tools.py             las ~40 tools + prompt de sistema        (~900)
    ├── llm.py               backends Ollama/OpenAI                   (~280)
    ├── agent.py             bucle agéntico, refuse, cancelación      (~260)
    ├── store.py             conversaciones                           (~90)
    ├── cli.py               REPL de terminal para probar sin UI      (~80)
    └── ui.py                overlay PyQt6 completo                   (~750)
```

## 11. Empaquetado y distribución

- **Desarrollo**: `pip install -r requirements.txt && python run.py`.
- **Windows**: `pyinstaller --noconsole --onefile --name GaiaSkyAIAgent run.py` → `.exe` único.
- **Linux**: ejecución directa con Python del sistema, o `pyinstaller --onefile`;
  AppImage propio como mejora futura (python-appimage).
- Licencia: decidir antes de publicar (MPL-2.0 encajaría con el ecosistema Gaia Sky).

## 12. Fases y criterios de aceptación

1. **F1 — Núcleo sin UI** (`gaiasky.py`, `tools.py`, `llm.py`, `agent.py`, `cli.py`):
   desde el REPL de terminal, con Gaia Sky oficial + `restPort` activo:
   "llévame a Marte" vuela, espera la llegada, enfoca y reporta la distancia real;
   un tour de 3 paradas alterna vuelo → `show_explanation` → narración; Stop funciona.
2. **F2 — Overlay** (`ui.py`, `main.py`): panel translúcido sobre Gaia Sky, chat completo,
   markdown con tablas reales, indicador de conexión, cancelación desde el botón.
3. **F3 — Pulido**: ajustes completos con prueba de conexión y refresco de modelos,
   historial de conversaciones, README bilingüe, empaquetado Windows.

## 13. Riesgos conocidos y decisiones

| Riesgo | Decisión |
|---|---|
| Carrera de hilos en vanilla al mandar comandos de cámara vía REST (lo que arreglaba `f98928320`) | Aceptado y documentado; mitigado por el ritmo del agente. **Resuelto en `master` upstream desde `650fd82e3` (2026-07-29)**; se mantiene la mitigación por si el usuario corre una versión anterior |
| El servidor REST traga excepciones (éxito falso) | Verificación posterior en tools críticas; mensajes honestos al modelo |
| `search_objects` sin equivalente REST | Versión degradada honesta; si upstream expone búsqueda algún día, se restaura |
| `get_closest_object` serializa un objeto complejo | Parser defensivo con fallback |
| Overlay en Wayland / fullscreen exclusivo | Modos `--window` y `QT_QPA_PLATFORM=xcb` documentados |
| Peticiones bloqueantes no abortables | `camera/stop` concurrente + timeouts generosos |
