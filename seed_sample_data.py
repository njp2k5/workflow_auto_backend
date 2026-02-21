import os
from datetime import date, timedelta

from dotenv import load_dotenv
from passlib.context import CryptContext
from sqlalchemy import create_engine, text


def main() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    engine = create_engine(database_url)
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    members = [
        ("Nikhil J Prasad", "Manager", "nikhil*123"),
        ("S Govind Krishnan", "Backend Developer", "govind*123"),
        ("Kailas S S", "Frontend developer", "kailas*123"),
        ("Mukundan V S", "Devops", "mukundan*123"),
    ]

    transcriptions = [
        "Quarterly insurance claim review and settlement status discussion.",
        "Pending claim validations for high-value policies were prioritized.",
        "Fraud-check workflow updates for suspicious claims were approved.",
        "API integration blockers between claim intake and validation modules.",
        "Action items for SLA improvements and escalation handling.",
    ]

    meeting_dates = [
        date.today() - timedelta(days=20),
        date.today() - timedelta(days=15),
        date.today() - timedelta(days=10),
        date.today() - timedelta(days=5),
        date.today(),
    ]

    task_descriptions = [
        "Review pending claim documents and flag missing attachments.",
        "Implement backend validation for policy-holder ID matching.",
        "Update frontend claim dashboard with verification status badges.",
        "Set up CI/CD alerts for failed claim validation deployments.",
        "Prepare weekly report for rejected and re-opened claims.",
    ]

    deadlines = [
        date.today() + timedelta(days=2),
        date.today() + timedelta(days=4),
        date.today() + timedelta(days=6),
        date.today() + timedelta(days=8),
        date.today() + timedelta(days=10),
    ]

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE tasks, meetings, transcription, members RESTART IDENTITY CASCADE"))

        member_ids = []
        for member_name, designation, plain_password in members:
            hashed_password = pwd_context.hash(plain_password)
            result = conn.execute(
                text(
                    """
                    INSERT INTO members (member_name, designation, password)
                    VALUES (:member_name, :designation, :password)
                    RETURNING member_id
                    """
                ),
                {
                    "member_name": member_name,
                    "designation": designation,
                    "password": hashed_password,
                },
            )
            member_ids.append(result.scalar_one())

        transcription_ids = []
        for summary in transcriptions:
            result = conn.execute(
                text(
                    """
                    INSERT INTO transcription (transcription_summary)
                    VALUES (:summary)
                    RETURNING transcription_id
                    """
                ),
                {"summary": summary},
            )
            transcription_ids.append(result.scalar_one())

        for meeting_date, transcription_id in zip(meeting_dates, transcription_ids):
            conn.execute(
                text(
                    """
                    INSERT INTO meetings (meeting_date, transcription_id)
                    VALUES (:meeting_date, :transcription_id)
                    """
                ),
                {
                    "meeting_date": meeting_date,
                    "transcription_id": transcription_id,
                },
            )

        for index in range(5):
            conn.execute(
                text(
                    """
                    INSERT INTO tasks (member_id, description, deadline)
                    VALUES (:member_id, :description, :deadline)
                    """
                ),
                {
                    "member_id": member_ids[index % len(member_ids)],
                    "description": task_descriptions[index],
                    "deadline": deadlines[index],
                },
            )

    with engine.connect() as conn:
        counts = {
            "members": conn.execute(text("SELECT COUNT(*) FROM members")).scalar_one(),
            "transcription": conn.execute(text("SELECT COUNT(*) FROM transcription")).scalar_one(),
            "meetings": conn.execute(text("SELECT COUNT(*) FROM meetings")).scalar_one(),
            "tasks": conn.execute(text("SELECT COUNT(*) FROM tasks")).scalar_one(),
        }
        seeded_members = conn.execute(
            text("SELECT member_id, member_name, designation FROM members ORDER BY member_id")
        ).fetchall()
        seeded_meetings = conn.execute(
            text("SELECT meeting_id, meeting_date, transcription_id FROM meetings ORDER BY meeting_id")
        ).fetchall()
        seeded_tasks = conn.execute(
            text("SELECT task_id, member_id, deadline FROM tasks ORDER BY task_id")
        ).fetchall()

    print("Seeding complete:", counts)
    print("Members:", seeded_members)
    print("Meetings:", seeded_meetings)
    print("Tasks:", seeded_tasks)


if __name__ == "__main__":
    main()
