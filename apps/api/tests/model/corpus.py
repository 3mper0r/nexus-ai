"""A small bilingual (English/German) retrieval golden set.

Each topic has one English and one German passage that are *not* translations of
each other word for word, so retrieval must work on meaning. Queries are phrased
differently from the passages (no copied keywords where avoidable).
"""

from __future__ import annotations

PASSAGES: dict[str, str] = {
    "retention-en": (
        "Customer records are kept for a maximum of three years after the contract ends. "
        "After that period the data is deleted automatically."
    ),
    "retention-de": (
        "Personenbezogene Kundendaten werden spätestens drei Jahre nach Vertragsende "
        "gelöscht. Die Löschung erfolgt automatisiert."
    ),
    "vacation-en": (
        "Full-time employees receive 28 days of paid annual leave. Unused days may be "
        "carried over until the end of March of the following year."
    ),
    "vacation-de": (
        "Vollzeitbeschäftigte haben Anspruch auf 28 Urlaubstage pro Jahr. Resturlaub "
        "verfällt am 31. März des Folgejahres."
    ),
    "password-en": (
        "Passwords must contain at least fourteen characters and are rotated only when "
        "a compromise is suspected. Multi-factor authentication is mandatory."
    ),
    "password-de": (
        "Kennwörter müssen mindestens vierzehn Zeichen lang sein. Die Anmeldung erfolgt "
        "zusätzlich über einen zweiten Faktor, zum Beispiel eine Authenticator-App."
    ),
    "travel-en": (
        "Train travel in second class is reimbursed for business trips. Flights require "
        "prior approval from the department head."
    ),
    "travel-de": (
        "Dienstreisen mit der Bahn werden in der zweiten Klasse erstattet. Für Flüge ist "
        "vorab eine Genehmigung der Abteilungsleitung erforderlich."
    ),
    "fire-en": (
        "In case of fire, leave the building via the marked escape routes and gather at "
        "the assembly point in the north car park. Do not use the elevators."
    ),
    "fire-de": (
        "Im Brandfall das Gebäude über die gekennzeichneten Fluchtwege verlassen und sich "
        "am Sammelplatz auf dem Nordparkplatz einfinden. Aufzüge nicht benutzen."
    ),
}

# (query, topic). A hit is any passage of that topic, in either language.
QUERIES: list[tuple[str, str]] = [
    ("How long do we store client data?", "retention"),
    ("Wie lange werden Kundendaten aufbewahrt?", "retention"),
    ("How many holidays do I get per year?", "vacation"),
    ("Wann verfallen nicht genommene Urlaubstage?", "vacation"),
    ("What is the minimum password length?", "password"),
    ("Brauche ich eine Zwei-Faktor-Anmeldung?", "password"),
    ("Can I book a flight for a work trip?", "travel"),
    ("Werden Bahnfahrten erstattet?", "travel"),
    ("Where should I go when the fire alarm rings?", "fire"),
    ("Darf ich im Notfall den Aufzug nehmen?", "fire"),
]

# Cross-lingual pairs: query language differs from the only passage offered.
CROSS_LINGUAL: list[tuple[str, str]] = [
    ("How long do we store client data?", "retention-de"),
    ("Wie viele Urlaubstage habe ich?", "vacation-en"),
    ("What is the minimum password length?", "password-de"),
    ("Wo ist der Sammelplatz bei einem Feuer?", "fire-en"),
]
