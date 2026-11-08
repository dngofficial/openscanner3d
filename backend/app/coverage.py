"""Angle checklist for a stationary object: 12 sides x 3 heights, from the phone's motion sensors.

Azimuth is how far the phone's heading has turned since the scan started (degrees, positive = the
user has walked left around the object while facing it). Pitch is how far the camera looks below the
horizon. Both come from the phone; they assume the object stays centred in the frame, so they are
guidance for coverage, not measured camera poses.
"""
SECTORS = 12
RINGS = (  # name, pitch-below-horizon range in degrees [min, max), photos wanted per side
    dict(name="low", min=-15, max=25, target=2),
    dict(name="mid", min=25, max=50, target=2),
    dict(name="high", min=50, max=86, target=1),
)
# A second photo of the same side must differ by this much in azimuth or pitch, so it adds a new view.
MIN_SEPARATION = 10


def plan() -> dict:
    return dict(sectors=SECTORS, rings=[dict(ring) for ring in RINGS], min_separation=MIN_SEPARATION)


def ring_of(pitch: float) -> int | None:
    for index, ring in enumerate(RINGS):
        if ring["min"] <= pitch < ring["max"]:
            return index
    return None


def sector_of(azimuth: float) -> int:
    width = 360 / SECTORS
    return int(((azimuth % 360) + width / 2) // width) % SECTORS


def summarise(poses: list[dict]) -> dict:
    counts = [[0] * SECTORS for _ in RINGS]
    for pose in poses:
        if pose.get("ring") is not None:
            counts[pose["ring"]][pose["sector"]] += 1
    wanted = sum(ring["target"] * SECTORS for ring in RINGS)
    have = sum(min(counts[r][s], ring["target"]) for r, ring in enumerate(RINGS) for s in range(SECTORS))
    return dict(counts=counts, have=have, wanted=wanted, complete=have >= wanted,
                unguided=sum(1 for pose in poses if pose.get("ring") is None))
