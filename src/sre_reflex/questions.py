from sre_reflex.models.base import Question

# Bump Q_VERSION whenever wording or options change.
Q_VERSION = 1

QUESTIONS = [
    Question(id="actionable", type="noul", text="A human needs to take action on this alert."),
    Question(
        id="severity",
        type="score",
        text="How severe is the impact of this alert?",
        options=["1 cosmetic", "2 minor", "3 degraded", "4 major", "5 outage"],
    ),
    Question(
        id="self_resolving",
        type="noul",
        text="This alert will resolve on its own without intervention.",
    ),
]
