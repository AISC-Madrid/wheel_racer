"""The server half of Hand-Wheel Racer.

The booth's laptop runs the game; this runs on a VPS and holds the results.
It is a separate package from `wheel_racer` on purpose — building the game must
never mean installing a web framework, and deploying the server must never mean
shipping MediaPipe to a container.

What ties them together is `models.py`, which is the wire contract, and the
rule that `store.normalise_email` and `wheel_racer.players.normalise_email`
have to agree about what makes two addresses the same person.
"""
