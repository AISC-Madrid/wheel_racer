# The board

The half of Hand-Wheel Racer that does not live at the stand. It holds every
run anybody has driven, serves the public board, and is the reason a player who
drove at the last fair on a different laptop still gets their record back.

One FastAPI process over one SQLite file. Reading the board needs nothing;
writing to it needs a token the booth has.

## Deploying it (Coolify)

1. **New resource → Docker Compose / Dockerfile**, pointed at this repository.
2. **Dockerfile location**: `/server/Dockerfile`. Leave the base directory at
   `/` — the build context has to be the repository root, because the page and
   the logo are shared with the booth.
3. **Persistent volume**: mount one at `/data`. Everything anybody has ever
   driven is in there.
4. **Environment variables**:

   | Variable | What it is |
   |---|---|
   | `WHEEL_RACER_BOOTH_TOKEN` | What the booth laptop sends. Long and random. |
   | `WHEEL_RACER_ADMIN_TOKEN` | The mailing list and moderation. A different one. |
   | `WHEEL_RACER_DB` | Optional. Defaults to `/data/wheel-racer.sqlite3`. |
   | `WHEEL_RACER_BOARD_SIZE` | Optional. Rows on the board, default 8. |
   | `WHEEL_RACER_MIN_LAP_S` / `_MAX_LAP_S` | Optional. What counts as a plausible lap, default 5–600. |

   Generate the tokens with `python -c "import secrets;print(secrets.token_urlsafe(32))"`.

5. Give it a domain. Coolify's proxy gets the certificate.

`GET /health` says whether it came up and whether both tokens are set. A server
with no tokens serves the board perfectly and refuses the booth — which is a
state worth being able to see from outside.

The schema is applied on startup, so deploying an update is a redeploy and
nothing else.

## Pointing a booth at it

In a `.env` file at the top of the repository, on the laptop:

```
WHEEL_RACER_SERVER=https://racer.example.com
WHEEL_RACER_BOOTH_TOKEN=the-booth-token
```

Then `python run.py`. The station starts with it and prints the kiosk URL.

## Running the fair

Everything below needs the **admin** token, not the booth one.

```bash
# The mailing list, with the consent that justifies it attached
curl -H "Authorization: Bearer $ADMIN" https://racer.example.com/api/admin/players.csv

# Somebody signed in as something that must not be on a screen behind a stand
curl -X POST -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
     -d '{"email":"them@example.com","hidden":true}' \
     https://racer.example.com/api/admin/players/hide

# "Please delete my data" — removes the player and every run they set
curl -X POST -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
     -d '{"email":"them@example.com"}' \
     https://racer.example.com/api/admin/players/erase
```

Hiding is reversible and keeps the result; erasing is neither. Reach for the
first one during a fair and the second one only when somebody asks.

### Starting the board again

Setting a stand up means driving it, and none of that belongs on the screen the
fair sees. Half an hour before the doors open:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN" https://racer.example.com/api/admin/reset
# {"since":"2026-03-14T09:12:44.031Z","players":0,"runs":0}
```

The board is empty, the counters are at nil, and anybody who drove during the
testing signs in as somebody new — so their first real run is a personal best
and gets its confetti. **Nothing is deleted.** It is a line the board counts
from, not a `DELETE`, and everything before it is still in the file and still
in `players.csv` with the consent that came with it.

The booth laptops follow along on their own. The line travels with the board
they already poll every three seconds, and a station that sees it move empties
its own roster of who has played here today. There is nothing to do at the
stand and no file to go and delete.

Two variations and an undo:

```bash
# Draw the line somewhere else — the four o'clock realisation that it should
# have been at two.
curl -X POST -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json"      -d '{"since":"2026-03-14T14:00:00+01:00"}'      https://racer.example.com/api/admin/reset

# Put everything back, whenever it was driven.
curl -X DELETE -H "Authorization: Bearer $ADMIN" https://racer.example.com/api/admin/reset
```

`GET /health` reports `board_since`, so "why is the ranking empty" is a
question answerable from a phone without a token.

A run counts by when it was **driven**, not by when it arrived. A laptop that
spent the morning offline and empties its queue at two o'clock does not pour
the morning's testing onto a board that was reset at one.

## The data

`/data/wheel-racer.sqlite3` holds names, email addresses and the consent stamp
that goes with them. It is one file, so a backup is a copy — but it is a copy
of everybody's contact details, so it goes somewhere encrypted or it does not
leave the machine.

Four tables, and the whole schema is in `wheel_racer_server/db.py`:

* `players` — one row per address, with the terms they agreed to and when.
* `runs` — every lap anybody has driven. A player's best is `MIN(seconds)` over
  these rather than a stored column, so a moderation delete or a late arrival
  from a booth's queue cannot leave two facts disagreeing.
* `stations` — what each booth is doing right now. Ages out on its own.
* `meta` — one row, holding the moment the board counts from.

## Working on it

```bash
uv sync --extra server --extra dev
uv run pytest server/tests

WHEEL_RACER_DB=data/dev.sqlite3 \
WHEEL_RACER_BOOTH_TOKEN=dev WHEEL_RACER_ADMIN_TOKEN=dev-admin \
uv run uvicorn wheel_racer_server.main:app --reload --app-dir server
```

The board is then at <http://127.0.0.1:8000/> and the API docs at `/docs`.
