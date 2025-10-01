# parse_cesmm_pgsql.py
import pdfplumber, re, csv, unicodedata
from cesmm_structure import CESMM_STRUCTURE

PDF_PATH = "CESMM3.pdf"
OUTPUT_CSV = "cesmm_clean.csv"


# --------------------------
# Helpers
# --------------------------
def clean_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("-\n", "")  # join hyphenated words
    s = s.replace("\n", " ")  # join wrapped lines
    s = re.sub(r"[\u00A0]", " ", s)  # non-breaking spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


row_counter = {}
results = []

# Track for validation
found_rules = set()


def emit_row(
    class_code,
    class_title,
    division_level=None,
    division_text=None,
    rule_type=None,
    rule_code=None,
    rule_text=None,
    force_id=None,
):
    """Emit structured row for CSV."""
    row_counter.setdefault(class_code, 0)
    row_counter[class_code] += 1
    order_in_class = row_counter[class_code]

    # Build synthetic ID
    if force_id:
        uid = force_id
    elif rule_code:
        uid = f"{class_code}_{rule_code}"
        found_rules.add(uid)
    elif division_text:
        uid = f"{class_code}_DIV{division_level}_{order_in_class}"
    else:
        uid = f"{class_code}_{order_in_class}"

    results.append(
        {
            "id": uid,
            "class_code": class_code,
            "class_title": class_title,
            "division_level": division_level,
            "division_text": division_text,
            "rule_type": rule_type,
            "rule_code": rule_code,
            "rule_text": rule_text,
            "order_in_class": order_in_class,
        }
    )


# --------------------------
# Regex patterns
# --------------------------
CLASS_HEADING = re.compile(r"^CLASS\s+([A-Z])[:\s-]+(.+)$", re.IGNORECASE)
RULES_HEADER = re.compile(
    r"^(MEASUREMENT RULES|DEFINITION RULES|COVERAGE RULES|ADDITIONAL DESCRIPTION RULES)",
    re.IGNORECASE,
)
RULE_LINE = re.compile(r"^([MDCA]\d+)\s+(.*)$")  # e.g. M1 text, D3 text

# --------------------------
# Step 1: Emit structure from cesmm_structure.py
# --------------------------
for class_code, content in CESMM_STRUCTURE.items():
    title = content["title"]
    row_counter[class_code] = 0
    # Emit the class header as a "division_level 0" for clarity
    emit_row(
        class_code,
        title,
        division_level=0,
        division_text=title,
        force_id=f"{class_code}_HEADER",
    )

    for level, items in content.get("divisions", {}).items():
        for div in items:
            emit_row(class_code, title, division_level=level, division_text=div)

# --------------------------
# Step 2: Parse PDF for rules only
# --------------------------
cur_class = None
cur_title = None
cur_rule_type = None

with pdfplumber.open(PDF_PATH) as pdf:
    for page in pdf.pages:
        text = page.extract_text() or ""
        for line in text.split("\n"):
            s = clean_text(line)
            if not s:
                continue

            # Detect Class heading (reset state)
            m = CLASS_HEADING.match(s)
            if m:
                cur_class = m.group(1).upper()
                cur_title = CESMM_STRUCTURE.get(cur_class, {}).get(
                    "title", m.group(2).strip()
                )
                cur_rule_type = None
                continue

            # Detect Rule Section header
            m2 = RULES_HEADER.match(s)
            if m2:
                cur_rule_type = m2.group(1).lower().split()[0]
                continue

            # Detect Rule line
            m3 = RULE_LINE.match(s)
            if m3 and cur_class:
                rule_code = m3.group(1)  # e.g. M1, D3
                rule_text = m3.group(2)
                emit_row(
                    cur_class,
                    cur_title,
                    rule_type=cur_rule_type,
                    rule_code=rule_code,
                    rule_text=rule_text,
                )
                continue

# --------------------------
# Step 3: Write CSV
# --------------------------
with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "id",
            "class_code",
            "class_title",
            "division_level",
            "division_text",
            "rule_type",
            "rule_code",
            "rule_text",
            "order_in_class",
        ],
    )
    writer.writeheader()
    writer.writerows(results)

# --------------------------
# Step 4: Validation
# --------------------------
print(f"✅ Parsed {len(results)} rows into {OUTPUT_CSV}")
print(f"📑 Classes scaffolded: {len(CESMM_STRUCTURE)}")
print(f"📘 Rules captured: {len(found_rules)}")
if not found_rules:
    print("⚠️ No rules detected. Check regex or PDF formatting.")
