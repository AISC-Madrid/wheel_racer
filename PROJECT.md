# Hand-Wheel Racer — Project Spec / Build Handoff

> Booth game for the AI Student Collective (AISC Madrid) start-of-year tabling
> session. This file is the single source of truth for a fresh Claude Code
> session. Read it fully before writing code. Rename to `CLAUDE.md` if you want
> Claude Code to auto-load it.

## What we're building

A single-player arcade racing game controlled by **gesturing to hold a steering
wheel**. The player grips a physical rectangular bar/prop with both hands; a
webcam tracks their two wrists via MediaPipe Hands; the tilt of the line between
the wrists steers a top-down car around a short lap circuit. Lap times feed a
live on-screen leaderboard.

The bar is a real physical prop (e.g. a laptop-sized rectangle of cardboard/wood
with grips at each end). It fixes the hand-to-hand distance and — critically —
gives shy visitors something to *hold* rather than a gesture to perform in front
of a crowd. Holding a wheel reads as "arcade cabinet," not "perform for
strangers."

## Hard constraints (do not violate — these killed earlier concepts)

- **Single player**, one machine, one webcam. No 1v1, no second station.
- **~30 seconds per try.** One flying lap is the unit of play. Keep booth
  throughput high; nobody should be able to hog the station for minutes.
- **Welcoming and low-embarrassment.** The player is a hesitant passer-by, not
  a gamer. No poses, no facial expressions, no precision-punishing mechanics,
  nothing that makes a newcomer feel watched-while-failing. This ruled out
  pose-reflex and face-mimicry designs.
- **Outdoors.** Uncontrolled lighting and busy moving backgrounds. Hand
  tracking is confirmed robust for this (last year's hand game worked outdoors);
  do not reintroduce face/pose detection, which degrades outdoors.
- **Small screen, close range.** The game is read up-close by the player, not
  from across the room. Do not design around a big display or spectator-distance
  visibility.

## Locked design decisions

These are settled. Do not re-open them without a strong reason.

- **Steering only. No throttle, no brake.** Both hands are on the bar, so there
  is no free input for gas. The car auto-throttles.
- **Auto-throttle with off-track slowdown.** Car runs at `top_speed` on tarmac
  and eases toward a slower `grass_speed` off it. Off-track is the *only*
  penalty and the only skill test.
- **Very open, sweeping corners (4–5 of them).** Deliberately gentle so the
  perfect lap never requires braking — only drawing a clean racing line. If you
  leave the line you hit grass, slow down, and recover by *steering back on*;
  you never spin out or get stuck.
- **Pure arcade physics.** No inertia, no drift, no momentum. First-timer
  friendly; spinning out publicly is exactly the embarrassment we're avoiding.
- **Fair lap validation via ordered checkpoints.** Prevents infield-cutting for
  fake times. Already implemented in the core (see below).

## Tech stack

- **Python** — reuses the working pipeline from last year's hand game.
- **MediaPipe Hands** — wrist landmark (index 0) of each of the two detected
  hands. Assign left/right by x-coordinate, not by MediaPipe's handedness label
  (the label flips unreliably when hands are close together on the bar).
- **OpenCV** — webcam capture.
- **pygame** — top-down track/car rendering, game loop, HUD, leaderboard panel.
- **SQLite or JSON** — local leaderboard persistence (single machine, no
  backend needed).

## What already exists: `wheel_racer_core.py`

The CV-agnostic control + timing core is written and lives alongside this file.
Copy it into the repo (suggested `src/wheel_racer_core.py`). It has no
MediaPipe/pygame dependency — it takes wrist points and `dt`, returns a drivable
car and validated lap times. **Do not rewrite it; build the capture and render
layers around it.**

### Public API

```python
# --- Steering: two wrist points -> steering in [-1, +1] ---
steer = SteeringFilter(dead_zone_deg=4.0, max_angle_deg=40.0,
                       smoothing=0.35, invert=False)
s = steer.update(wrist_a, wrist_b)   # each (x, y) in image space
s = steer.hold()                     # call instead when a hand is lost; keeps last value

# --- Car: arcade physics, auto-throttle + grass slowdown ---
car = Car(x, y, heading=0.0)
car.update(steering, on_track, dt,
           top_speed=320.0, grass_speed=120.0, accel=400.0, turn_rate=2.6)
# mutates car.x, car.y, car.heading, car.speed

# --- Track: centerline + width -> (on_track, progress) ---
track = Track(centerline, width=140)   # centerline: closed list of (x, y)
on_track, progress = track.query(car.x, car.y)   # progress in [0, 1)
sx, sy, sh = track.start_pose()        # (x, y, heading) on the start line

# --- Lap timing: ordered-checkpoint validation ---
timer = LapTimer(num_checkpoints=4)
lap = timer.update(progress, now)      # returns float lap time on a valid lap, else None
timer.best                             # best time this session (or None)
timer.current_time(now)                # elapsed on the in-progress lap, for the HUD
```

### Key internal properties to understand before tuning

- **Track = centerline polyline + width.** One `query()` gives *both*
  on/off-track (perpendicular distance < half-width) *and* lap progress
  (arc-length along the centerline). Grass detection, checkpoints, and lap
  timing all derive from this single primitive.
- **Constant turning radius.** Angular velocity scales with speed, so radius =
  `top_speed / (steering * turn_rate)` is speed-independent. This is why the
  grass penalty feels "heavier" for free and why a stopped car can't pivot.
- **Lap validation** assumes the car starts near progress 0 and drives forward.
  The finish crossing requires a large forward wrap (>0.75 → <0.25), so landmark
  jitter can't fake a lap; checkpoint index only increases within a lap.

## Build tasks (in order)

1. **Capture layer** — OpenCV webcam + MediaPipe Hands. Extract the two wrist
   points per frame. Return fewer than 2 when hands are lost so the loop can
   call `steer.hold()`. Decide frame mirroring here (see flags).
2. **Track authoring** — hand-place a `centerline` of 4–5 very open corners that
   fits your window and doubles back to form a closed loop of the right total
   length (see tuning). The placeholder in the core's `__main__` is a starting
   point only.
3. **Render loop (pygame)** — draw grass background, tarmac (thick line along the
   centerline, or filled band), the car sprite rotated to `car.heading`, and a
   HUD showing the live lap clock (`timer.current_time`) and best time.
4. **Respawn / anti-stuck** — if the car is off-track for more than ~3s, or is
   facing backwards, snap it to the nearest centerline point facing forward.
   Not yet implemented in the core; needed so a lost player isn't stranded.
   (Nearest point + forward heading is derivable from the track segments.)
5. **Leaderboard** — persist top-10 lap times to SQLite/JSON; render a panel.
   Optionally capture a 3-initials name entry after a qualifying lap (keep it
   fast — booth throughput).
6. **Onboarding / attract state** — a "grab the wheel to start" screen; auto-detect
   both wrists present + level bar to begin a countdown. Onboarding must be
   under ~10s with no manual calibration.

## Tuning guide (do this on real hardware, in order)

1. **Scale the track first.** Constants are pixels for a 1280×720 window. With
   `top_speed=320`, a ~30s lap needs a centerline of roughly 9,600 px folded into
   the screen — the loop will double back on itself. Size the centerline to your
   window, *then* adjust `top_speed` so laps land near 30s.
2. **`accel` is the main skill-gradient dial.** It controls how punishing an
   off-line moment is. High = snappy recovery (forgiving, welcoming). Low = an
   excursion really costs you (bigger leaderboard spread). Start forgiving for
   the booth; tighten only if times cluster too tightly to rank.
3. **Steering feel:** `dead_zone_deg`, `max_angle_deg`, `smoothing`. Tune by feel
   once playable — this is where "feels like a car" lives, not in the CV.

## Open flags / gotchas

- **Steering sign / mirroring is undetermined until you test.** Whether you flip
  the frame decides if steering feels natural or inverted. Don't reason about it —
  get it playable, and if it's reversed, flip the frame OR set
  `SteeringFilter(invert=True)`.
- **Outdoor background is uncontrolled.** Tracking is robust, but any slice-trail
  / compositing / overlay carried from last year may look messy over a live busy
  scene. Test the full *visual* against a real background, not just tracking
  accuracy.
- **Venue lighting.** Test MediaPipe Hands confidence under the actual outdoor
  lighting beforehand; backlighting behaves differently than an indoor test.
- **Hand dropout.** When one hand leaves the bar for a moment, hold last steering
  (`steer.hold()`) rather than snapping to center — a snap yanks the car.

## Suggested repo structure

```
wheel-racer/
├── PROJECT.md               # this file (or rename to CLAUDE.md)
├── requirements.txt         # opencv-python, mediapipe, pygame
├── src/
│   ├── wheel_racer_core.py  # existing control+timing core (do not rewrite)
│   ├── capture.py           # OpenCV + MediaPipe -> wrist points
│   ├── track_data.py        # the authored centerline(s)
│   ├── render.py            # pygame drawing (track, car, HUD, leaderboard)
│   ├── leaderboard.py       # SQLite/JSON persistence
│   └── game.py              # main loop wiring it all together
└── assets/                  # car sprite, fonts, etc.
```
