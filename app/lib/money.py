"""Money. Stored as integer kobo everywhere; naira only at the display edge."""


def naira_to_kobo(naira: float) -> int:
    return round(naira * 100)


def kobo_to_naira(kobo: int) -> float:
    return kobo / 100


def fmt_naira(kobo: int) -> str:
    if kobo % 100 == 0:
        return f"₦{kobo // 100:,}"
    return f"₦{kobo / 100:,.2f}"