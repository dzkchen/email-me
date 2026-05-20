from email_me.models import Founder
from email_me.utils import normalize_name

_NICKNAMES: dict[str, list[str]] = {
    "alexander": ["alex", "xander"],
    "alexandra": ["alex", "allie", "sasha"],
    "andrew": ["andy", "drew"],
    "anthony": ["tony"],
    "benjamin": ["ben"],
    "catherine": ["cat", "cathy", "kate", "katie"],
    "charles": ["charlie", "chuck"],
    "christine": ["chris", "christie", "tina"],
    "christina": ["chris", "christie", "tina"],
    "christopher": ["chris"],
    "daniel": ["dan", "danny"],
    "david": ["dave"],
    "deborah": ["deb", "debbie"],
    "donald": ["don"],
    "edward": ["ed", "eddie", "ted"],
    "elizabeth": ["beth", "eliza", "liz", "lizzie"],
    "frederick": ["fred"],
    "gregory": ["greg"],
    "jacob": ["jake"],
    "james": ["jim", "jamie"],
    "jennifer": ["jen", "jenny"],
    "jessica": ["jess"],
    "jonathan": ["jon", "jonny"],
    "joseph": ["joe", "joey"],
    "joshua": ["josh"],
    "katherine": ["kate", "katie", "kathy"],
    "kenneth": ["ken"],
    "margaret": ["maggie", "meg", "peggy"],
    "matthew": ["matt"],
    "michael": ["mike"],
    "nicholas": ["nick"],
    "patricia": ["pat", "patty", "tricia"],
    "patrick": ["pat"],
    "rebecca": ["becca", "becky"],
    "richard": ["rich", "rick", "dick"],
    "robert": ["rob", "bob"],
    "ronald": ["ron"],
    "samuel": ["sam"],
    "stephanie": ["steph"],
    "stephen": ["steve"],
    "steven": ["steve"],
    "susan": ["sue"],
    "theodore": ["theo", "ted"],
    "thomas": ["tom"],
    "timothy": ["tim"],
    "victoria": ["vicky", "tori"],
    "vincent": ["vince"],
    "william": ["will", "bill", "billy"],
    "zachary": ["zach"],
}


def _build_variants_map(nicknames: dict[str, list[str]]) -> dict[str, list[str]]:
    variants: dict[str, list[str]] = {}

    def _add(key: str, value: str) -> None:
        if key == value:
            return
        bucket = variants.setdefault(key, [])
        if value not in bucket:
            bucket.append(value)

    for canonical, nicks in nicknames.items():
        for nick in nicks:
            _add(canonical, nick)
            _add(nick, canonical)
    return variants


_VARIANTS = _build_variants_map(_NICKNAMES)


def _patterns_for_first(f: str, l: str, domain: str) -> list[str]:
    if not l or l == f:
        result = []
        if f:
            result.append(f"{f}@{domain}")
        fi = f[0] if f else ""
        if fi and fi != f:
            result.append(f"{fi}@{domain}")
        return result

    fi = f[0] if f else ""
    li = l[0] if l else ""

    def make(local: str) -> str | None:
        if not local or " " in local:
            return None
        return f"{local}@{domain}"

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


def generate_permutations(founder: Founder, domain: str) -> list[tuple[str, int]]:
    f = normalize_name(founder.first_name)
    l = normalize_name(founder.last_name)

    first_variants = [f]
    for variant in _VARIANTS.get(f, []):
        if variant and variant not in first_variants:
            first_variants.append(variant)

    seen: set[str] = set()
    ordered: list[str] = []
    for variant in first_variants:
        for email in _patterns_for_first(variant, l, domain):
            if email not in seen:
                seen.add(email)
                ordered.append(email)

    return [(email, rank) for rank, email in enumerate(ordered, start=1)]
