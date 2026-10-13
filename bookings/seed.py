"""Demo businesses. Usage: uv run python -m bookings.seed clinic [--db data/bookings.db]"""
import argparse
import os

from bookings import db

# The demo business: a chiropractic clinic. Fictional name; prices typical of Bangalore clinics.
CLINIC = dict(
    business=dict(name="SpineCare Chiropractic, HSR Layout", kind="clinic", open_time="09:00", close_time="19:00", chairs=2,
                  closed_weekdays="6"),
    services=[
        dict(name="Initial consultation & assessment", price_inr=1500, duration_min=45, lapse_weeks=None),
        dict(name="Chiropractic adjustment (follow-up)", price_inr=1200, duration_min=30, lapse_weeks=2),
        dict(name="Adjustment + soft-tissue therapy", price_inr=1800, duration_min=45, lapse_weeks=2),
        dict(name="Spinal decompression session", price_inr=2000, duration_min=30, lapse_weeks=1),
        dict(name="Posture & ergonomics review", price_inr=1000, duration_min=30, lapse_weeks=8),
        dict(name="Pregnancy chiropractic care", price_inr=1500, duration_min=30, lapse_weeks=2),
    ],
)
DEMOS = {"clinic": CLINIC}


def seed(conn, kind: str, owner_wa: str) -> int:
    spec = DEMOS[kind]
    biz = db.add_business(conn, owner_wa=owner_wa, **spec["business"])
    for s in spec["services"]:
        db.add_service(conn, biz, **s)
    return biz


def ensure_seeded(conn) -> None:
    """First boot: create the clinic if the DB is empty. The owner is a placeholder unless BOOKINGS_OWNER_WA is set."""
    if conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 0:
        seed(conn, "clinic", os.environ.get("BOOKINGS_OWNER_WA", "910000000000"))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=DEMOS)
    p.add_argument("--db", default=os.environ.get("BOOKINGS_DB", "data/bookings.db"))
    p.add_argument("--owner", default=os.environ.get("BOOKINGS_OWNER_WA", "910000000000"))
    a = p.parse_args()
    conn = db.connect(a.db)
    db.init(conn)
    print("business id", seed(conn, a.kind, a.owner))
