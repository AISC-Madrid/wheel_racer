"""Every tunable value in the game, in one place.

Booth-day tuning happens here and nowhere else. Nothing in this module imports
anything, so it is safe to read from any layer.

Units: pixels and seconds. All distances are in *world* pixels, and the world
is exactly the size of the window (the whole circuit is on screen at once, so
there is no camera transform to reason about).

The tuning order that actually works, from PROJECT.md:
  1. Size the track, then set TOP_SPEED so a lap lands near TARGET_LAP_SECONDS.
  2. Adjust ACCEL — it decides how much an off-line moment costs.
  3. Tune steering feel last, once the car is drivable.
"""

# --- Window -----------------------------------------------------------------

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
TARGET_FPS = 60

# --- Steering ---------------------------------------------------------------
# The player holds a rigid bar; we read the angle of the line between their two
# wrists. Level bar = straight ahead.

# Tilt below this is treated as "level". Stops the car wandering when someone is
# trying to hold still — hands are never perfectly level.
STEER_DEAD_ZONE_DEG = 4.0

# Tilt at which steering is fully locked. Lower = twitchier, needs less arm
# movement. Above this the input saturates rather than clipping awkwardly.
STEER_MAX_ANGLE_DEG = 40.0

# Exponential-smoothing time constant, in seconds. Larger = smoother and
# laggier. Landmark jitter lives around 0.03s, so anything above that damps it.
STEER_SMOOTHING_TAU = 0.08

# Flip if steering comes out backwards on real hardware. PROJECT.md is explicit
# that this is undetermined until tested: flip the frame OR flip this, not both.
STEER_INVERT = False

# --- Car --------------------------------------------------------------------
TOP_SPEED = 185.0
GRASS_SPEED = 100.0
ACCEL = 240.0
# Steering authority, in radians per second at full lock and full speed.
TURN_RATE = 1.7
# How much of the steering goes away as the car slows down. 
TURN_SPEED_RESPONSE = 0.5

# --- Track ------------------------------------------------------------------
TRACK_WIDTH = 62.0

# Which way round the circuit is driven, as seen on screen. Clockwise sends the
# car up the screen off the grid, which is the readable way round: the first
# corner opens away from the player instead of arriving underneath them before
# they have worked out which way the wheel moves the car.
#
# Flipping this reverses the direction without moving the start line and without
# changing the shape — the same corners arrive in the opposite order and turn
# the other way. Worth having at a booth: once the queue has watched ten people
# learn the racing line, turning it round gives everyone a fresh track for free.
RUN_CLOCKWISE = True

# --- The run ----------------------------------------------------------------
LAPS_PER_RUN = 2
TARGET_RUN_SECONDS = 30.0

# --- Lap timing -------------------------------------------------------------
NUM_CHECKPOINT_GATES = 5

# Largest single-frame change in lap progress that counts as "driven". A car at
# TOP_SPEED on a ~4500px circuit advances about 0.0006 per frame, so even a bad
# framerate hitch stays far under this — while a cut across the infield makes
# progress jump by a tenth of a lap or more, which is what this catches.
LAP_MAX_PROGRESS_STEP = 0.05

# --- Recovery ---------------------------------------------------------------
RESPAWN_AFTER_OFF_TRACK_S = 3.0
RESPAWN_AFTER_BACKWARDS_S = 1.5

# --- Booth flow -------------------------------------------------------------
ABANDON_AFTER_HANDS_LOST_S = 6.0
ATTRACT_HOLD_SECONDS = 1.2
ATTRACT_LEVEL_TOLERANCE = 0.35

# --- Camera -----------------------------------------------------------------

CAMERA_INDEX = 0

# Capture resolution. Bigger costs framerate and buys very little: the game
# reads one landmark per hand, and MediaPipe downsamples internally anyway.
CAMERA_CAPTURE_SIZE = (640, 480)


CAMERA_MIRROR = True

# 0 is the light hand model, 1 the heavy one. The heavy model is better at
# finger detail, which this game never looks at, and worse at framerate, which
# it does. Raise only if wrists are being lost outdoors.
CAMERA_MODEL_COMPLEXITY = 0

# Raise detection confidence if the busy outdoor background produces phantom
# hands; lower it if real hands are missed in awkward light.
CAMERA_DETECTION_CONFIDENCE = 0.6
CAMERA_TRACKING_CONFIDENCE = 0.5

# Width of the picture-in-picture preview, in design pixels.
CAMERA_PREVIEW_WIDTH = 240

# Treat the last sample as no longer valid after this long. Covers the camera
# stalling or being unplugged mid-run, which otherwise looks like a player
# holding perfectly still.
CAMERA_STALE_AFTER_S = 0.4
