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
# Pure arcade physics: no inertia, no drift. The car auto-throttles toward a
# target speed that depends only on whether it is on tarmac.

# Speed on tarmac. Set this *after* the track is authored, so that one lap comes
# out near TARGET_LAP_SECONDS.
TOP_SPEED = 155.0

# Speed on grass. The only penalty in the game.
GRASS_SPEED = 60.0

# How fast speed eases toward its target, px/s². This is the main skill-gradient
# dial: high = snappy recovery and a welcoming game, low = an excursion really
# costs you and the leaderboard spreads out. Start forgiving for the booth.
# At 200, recovering from grass to full speed takes (155-60)/200 ≈ 0.5s.
ACCEL = 200.0

# Steering authority. Turn radius at full lock is TOP_SPEED / TURN_RATE, and is
# independent of current speed by construction — see car.py. At these values
# that is ~60px, so the sweeping corners sit around 25-35% of full lock.
TURN_RATE = 2.6

# --- Track ------------------------------------------------------------------

# Full tarmac width. The car is on-track while its centre is within half of this
# of the centreline. Narrower than PROJECT.md's 140 because the whole circuit is
# folded into one screen and a wide band would overlap itself.
TRACK_WIDTH = 62.0

# What we are aiming for once the circuit is drawn.
TARGET_LAP_SECONDS = 30.0

# --- Lap timing -------------------------------------------------------------

# Ordered gates a lap must pass through, evenly spaced around the circuit.
# Stops anyone cutting across the infield for a fake time.
NUM_CHECKPOINTS = 4

# --- Recovery ---------------------------------------------------------------

# Snap back to the track after this long fully off it. Nobody should be able to
# get stranded in a corner of the screen with a queue watching.
RESPAWN_AFTER_OFF_TRACK_S = 3.0

# Snap back after this long pointing the wrong way round the circuit.
RESPAWN_AFTER_BACKWARDS_S = 1.5

# --- Booth flow -------------------------------------------------------------

# Abandon the lap and return to the attract screen after this long with fewer
# than two wrists visible. Without it, a player who walks away mid-lap leaves
# the car circling on the last held steering value forever.
ABANDON_AFTER_HANDS_LOST_S = 6.0
