from email_me.models import Founder
from email_me.utils import normalize_name


def generate_permutations(founder: Founder, domain: str) -> list[str]:
    f = normalize_name(founder.first_name)
    l = normalize_name(founder.last_name)
    fi = f[0] if f else ""
    li = l[0] if l else ""

    def make(local: str) -> str | None:
        return f"{local}@{domain}" if local else None

    def patterns_for(last: str, last_initial: str) -> list[str | None]:
        return [
            make(f),
            make(f"{f}.{last}"),
            make(f"{fi}.{last}"),
            make(f"{f}{last}"),
            make(f"{fi}{last}"),
            make(f"{f}_{last}"),
            make(f"{f}-{last}"),
            make(last),
            make(f"{last}.{f}"),
            make(f"{last}{f}"),
            make(fi),
            make(f"{fi}{last_initial}"),
        ]

    seen: set[str] = set()
    result: list[str] = []

    def add_patterns(pats: list[str | None]) -> None:
        for email in pats:
            if email and email not in seen:
                seen.add(email)
                result.append(email)

    add_patterns(patterns_for(l, li))

    if " " in l:
        l_compact = l.replace(" ", "")
        l_token = l.rsplit(" ", 1)[-1]
        add_patterns(patterns_for(l_compact, l_compact[0] if l_compact else ""))
        add_patterns(patterns_for(l_token, l_token[0] if l_token else ""))

    return result
