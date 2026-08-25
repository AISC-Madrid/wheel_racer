# 🏎️ Hand-Wheel Racer

**Grab a cardboard rectangle. Pretend it's a steering wheel. Drive.**

That's it. That's the game. A webcam watches your two wrists, the tilt of the
line between them steers a little red car around a circuit, and thirty seconds
later you find out whether you're better at imaginary driving than everyone
else at the stand.

Built for the AI Student Collective (AISC) Madrid tabling booth, where the hard
part isn't the computer vision — it's getting a shy person walking past to try
something in front of a queue.

![The game](docs/game.png)

## How it works

You hold a physical bar with both hands. MediaPipe finds your wrists. The angle
between them is your steering wheel.

- **No throttle, no brake.** Both your hands are busy holding the bar, so the
  car drives itself. All you do is point it.
- **Two laps.** About thirty seconds. The queue moves.
- **Grass is the only punishment.** Leave the tarmac and you slow down. That's
  the entire skill: draw a clean line. You can't spin, you can't crash, and you
  can't get stuck — which matters, because spinning out in front of strangers is
  exactly the thing that stops people having a go.
- **No calibration, no buttons.** Pick up the bar, hold it level, and the
  countdown starts.

Beat your own best and the screen throws confetti at you:

![A personal best](docs/celebration.png)

## The second screen

The laptop runs the game. A monitor facing the stand runs a **timing tower** —
who's driving right now, their live lap clock, and how badly they're losing to
the record.

![The leaderboard](docs/leaderboard.png)

There's a car quietly lapping the circuit behind the board at the current record
pace, because a stand that isn't moving is a poster. Climb into the top three
and your row slides up and lights gold. Take the record outright and the whole
screen stops what it's doing to say so.

## Play it

Needs Python 3.12.

```bash
uv sync --extra cv        # the webcam stack
uv run python run.py
```

That opens both windows — game and leaderboard. Drag each onto the screen it
belongs on, press **F11** to make it fullscreen, and you're a racing team.

No webcam handy? No problem:

```bash
uv sync                   # skip MediaPipe entirely
uv run python run.py --input keyboard
```

Arrow keys steer, space starts. The whole game runs end to end without a camera,
which is how most of it got built.

### Keys

| | |
|---|---|
| **F11** or **Cmd-F** | fullscreen, on either window |
| **Esc** | quit |
| **Tab / Enter** | move through the sign-in form |
| **← →** or **A / D** | steer, in keyboard mode |

## What's in here

```
run.py                 start the stand (game + leaderboard)
leaderboard.py         the second screen, on its own
src/wheel_racer/
  car.py               arcade physics: auto-throttle, grass, no drift
  track.py             centreline + width → on-track? how far round?
  track_data.py        the circuit itself, as a handful of control points
  laptimer.py          ordered-checkpoint validation, so times are real
  camera.py            OpenCV + MediaPipe → two wrist points
  steering.py          two wrists → one number in [-1, +1]
  render.py            the game screen
  leaderboard.py       the timing tower
  live.py              the file the two screens talk through
```


## Tests

```bash
uv run --extra dev pytest
```

Around 475 of them, all green, and none need a webcam.

## A note on the data

The booth collects names and email addresses, so `data/` is gitignored and stays
that way. The leaderboard shows **first names only** — a screen at a public
stand that strangers can photograph is the last place anyone's contact details
belong.

---

<sub>Made for AISC Madrid · steering wheel not included, cardboard sold
separately</sub>
