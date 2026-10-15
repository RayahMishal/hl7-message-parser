"""
HL7 v2 Message Parser & Interpreter
===================================
Turns a raw HL7 v2 message into structured, human-readable clinical information:
patient demographics, encounter details, lab orders and observations (with abnormal flags),
allergies, diagnoses and next of kin.

    python hl7_parser.py message.hl7          # readable report
    python hl7_parser.py message.hl7 --json   # structured JSON
    python hl7_parser.py message.hl7 --fields # every field with its HL7 name
    cat message.hl7 | python hl7_parser.py -  # read from stdin

Standard library only. Sample data is FAKE.
"""
import json
import re
import sys
from datetime import date, datetime

# ---------------------------------------------------------------------------------
# Code tables (HL7 v2 tables, shortened to the values used here)
# ---------------------------------------------------------------------------------
SEX = {"M": "Male", "F": "Female", "O": "Other", "U": "Unknown", "A": "Ambiguous", "N": "Not applicable"}
PATIENT_CLASS = {"I": "Inpatient", "O": "Outpatient", "E": "Emergency", "P": "Pre-admission",
                 "R": "Recurring patient", "B": "Obstetrics"}
ABNORMAL = {"N": "Normal", "L": "Low", "H": "High", "LL": "Critically low", "HH": "Critically high",
            "A": "Abnormal", "AA": "Critically abnormal", "<": "Below absolute low",
            ">": "Above absolute high", "S": "Susceptible", "R": "Resistant", "I": "Intermediate"}
RESULT_STATUS = {"F": "Final", "P": "Preliminary", "C": "Corrected", "X": "Cannot be obtained",
                 "R": "Entered, not verified", "I": "Pending", "D": "Deleted"}
ORDER_CONTROL = {"NW": "New order", "CA": "Cancel order", "OK": "Order accepted", "SC": "Status changed",
                 "RE": "Observations to follow", "DC": "Discontinue order", "HD": "Hold order"}
MESSAGE_EVENTS = {
    "ADT^A01": "Admit a patient", "ADT^A02": "Transfer a patient", "ADT^A03": "Discharge a patient",
    "ADT^A04": "Register a patient", "ADT^A08": "Update patient information",
    "ORM^O01": "General order", "ORU^R01": "Unsolicited observation result",
    "ACK": "General acknowledgement", "SIU^S12": "Notification of new appointment booking"}
SEVERITY = {"SV": "Severe", "MO": "Moderate", "MI": "Mild", "U": "Unknown"}

# Field names for the segments this tool interprets (index = field number)
FIELD_NAMES = {
    "MSH": {3: "Sending Application", 4: "Sending Facility", 5: "Receiving Application",
            6: "Receiving Facility", 7: "Message Date/Time", 9: "Message Type",
            10: "Message Control ID", 11: "Processing ID", 12: "Version"},
    "EVN": {1: "Event Type Code", 2: "Recorded Date/Time"},
    "PID": {3: "Patient Identifier List", 5: "Patient Name", 7: "Date of Birth", 8: "Administrative Sex",
            11: "Address", 13: "Home Phone", 14: "Business Phone", 18: "Account Number"},
    "NK1": {2: "Name", 3: "Relationship", 4: "Address", 5: "Phone"},
    "PV1": {2: "Patient Class", 3: "Assigned Location", 7: "Attending Doctor", 19: "Visit Number",
            44: "Admit Date/Time", 45: "Discharge Date/Time"},
    "AL1": {3: "Allergen", 4: "Severity", 5: "Reaction"},
    "DG1": {3: "Diagnosis Code", 4: "Diagnosis Description", 6: "Diagnosis Type"},
    "ORC": {1: "Order Control", 2: "Placer Order Number", 3: "Filler Order Number", 5: "Order Status",
            9: "Date/Time of Transaction", 12: "Ordering Provider"},
    "OBR": {2: "Placer Order Number", 3: "Filler Order Number", 4: "Universal Service ID",
            7: "Observation Date/Time", 16: "Ordering Provider", 25: "Result Status"},
    "OBX": {2: "Value Type", 3: "Observation Identifier", 5: "Observation Value", 6: "Units",
            7: "Reference Range", 8: "Abnormal Flags", 11: "Result Status", 14: "Observation Date/Time"},
}


# ---------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------
class Segment:
    def __init__(self, raw, enc):
        self.raw, self.enc = raw, enc
        self.parts = raw.split(enc["field"])
        self.name = self.parts[0]

    def field(self, n):
        i = n - 1 if self.name == "MSH" else n  # MSH-1 is the separator itself
        return self.parts[i] if 0 <= i < len(self.parts) else ""

    def components(self, n, rep=0):
        reps = self.field(n).split(self.enc["rep"])
        if rep >= len(reps):
            return []
        return [self.unescape(c) for c in reps[rep].split(self.enc["comp"])]

    def repeats(self, n):
        raw = self.field(n)
        return [r for r in raw.split(self.enc["rep"]) if r] if raw else []

    def comp(self, n, c, rep=0):
        parts = self.components(n, rep)
        return parts[c - 1].strip() if len(parts) >= c else ""

    def unescape(self, text):
        e, f = self.enc["esc"], self.enc
        table = {"F": f["field"], "S": f["comp"], "T": f["sub"], "R": f["rep"], "E": e, "X0D": "\r", "X0A": "\n"}
        text = re.sub(re.escape(e) + r"\.br" + re.escape(e), "\n", text)
        return re.sub(re.escape(e) + r"(F|S|T|R|E|X0D|X0A)" + re.escape(e),
                      lambda m: table[m.group(1)], text)


class Message:
    def __init__(self, text):
        lines = [l for l in re.split(r"\r\n|\r|\n", text.strip()) if l.strip()]
        if not lines or not lines[0].startswith("MSH") or len(lines[0]) < 8:
            raise ValueError("Not an HL7 v2 message: it must start with an MSH segment.")
        fld = lines[0][3]
        chars = lines[0].split(fld)[1]
        if len(chars) < 4:
            raise ValueError("MSH-2 (encoding characters) is incomplete.")
        self.enc = {"field": fld, "comp": chars[0], "rep": chars[1], "esc": chars[2], "sub": chars[3]}
        self.segments = [Segment(l, self.enc) for l in lines]

    def all(self, name):
        return [s for s in self.segments if s.name == name]

    def first(self, name):
        found = self.all(name)
        return found[0] if found else None


# ---------------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------------
def fmt_ts(value):
    """HL7 timestamp -> readable text, keeping the precision that was sent."""
    m = re.match(r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", (value or "").strip())
    if not m or not m.group(1):
        return value or ""
    y, mo, d, h, mi, s = m.groups()
    try:
        if not mo:
            return y
        if not d:
            return datetime(int(y), int(mo), 1).strftime("%b %Y")
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return f"{value} (invalid date)"
    return dt.strftime("%d %b %Y") + (f", {h}:{mi or '00'}" if h else "")


def parse_date(value):
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", (value or "").strip())
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        return None


def person_name(seg, n, rep=0):
    p = seg.components(n, rep)
    family = p[0].split(seg.enc["sub"])[0] if p else ""
    given = " ".join(x for x in (p[1:3] if len(p) > 1 else []) if x)
    prefix = p[4] if len(p) > 4 else ""
    return " ".join(x for x in (prefix, given, family) if x).strip()


def coded(seg, n, rep=0):
    code, text = seg.comp(n, 1, rep), seg.comp(n, 2, rep)
    system = seg.comp(n, 3, rep)
    return {"code": code, "text": text or code, "system": system}


def address(seg, n):
    a = seg.components(n)
    parts = [a[i] for i in (0, 2, 3, 4, 5) if len(a) > i and a[i]]
    return ", ".join(parts)


def location(seg, n):
    bits = [seg.comp(n, i) for i in (1, 2, 3) if seg.comp(n, i)]
    return " / ".join(bits)


TITLES = {"dr", "prof", "mr", "mrs", "ms", "miss"}


def provider(seg, n):
    """XCN: id^family^given^middle^suffix^prefix^degree. A title such as 'Dr.' is accepted in the
    prefix (XCN-6) or, as many senders put it, in the degree slot (XCN-7); real degrees (MD) follow the name."""
    p = seg.components(n)
    if len(p) < 3:
        return ""
    prefix = p[5] if len(p) > 5 else ""
    degree = p[6] if len(p) > 6 else ""
    if not prefix and degree.rstrip(".").lower() in TITLES:
        prefix, degree = degree, ""
    name = " ".join(x for x in (prefix, p[2], p[1]) if x)
    return f"{name}, {degree}" if degree else name


# ---------------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------------
def interpret(msg):
    msh = msg.first("MSH")
    mtype = msh.comp(9, 1) + ("^" + msh.comp(9, 2) if msh.comp(9, 2) else "")
    out = {"message": {
        "type": mtype, "description": MESSAGE_EVENTS.get(mtype, MESSAGE_EVENTS.get(msh.comp(9, 1), "")),
        "controlId": msh.field(10), "version": msh.comp(12, 1), "sentAt": fmt_ts(msh.field(7)),
        "from": " @ ".join(x for x in (msh.comp(3, 1), msh.comp(4, 1)) if x),
        "to": " @ ".join(x for x in (msh.comp(5, 1), msh.comp(6, 1)) if x)},
        "warnings": []}
    warn = out["warnings"].append
    if not msh.field(10):
        warn("MSH-10 Message Control ID is empty (acknowledgements cannot be matched).")
    if not msh.comp(12, 1):
        warn("MSH-12 Version is empty.")

    pid = msg.first("PID")
    if pid:
        ids = []
        for i in range(len(pid.repeats(3))):
            ids.append({"id": pid.comp(3, 1, i), "authority": pid.comp(3, 4, i), "type": pid.comp(3, 5, i)})
        sent = parse_date(msh.field(7)) or date.today()
        dob = parse_date(pid.field(7))
        age = None
        if dob:
            age = sent.year - dob.year - ((sent.month, sent.day) < (dob.month, dob.day))
            if age < 0:
                warn("PID-7 Date of Birth is after the message date.")
                age = None
        elif pid.field(7):
            warn(f"PID-7 Date of Birth '{pid.field(7)}' is not a valid date.")
        sex_raw = pid.comp(8, 1)
        out["patient"] = {
            "identifiers": ids, "name": person_name(pid, 5), "dateOfBirth": fmt_ts(pid.field(7)),
            "ageAtMessage": age, "sex": SEX.get(sex_raw, sex_raw or ""), "address": address(pid, 11),
            "phone": pid.comp(13, 1) or pid.comp(13, 12)}
        if sex_raw and sex_raw not in SEX:
            warn(f"PID-8 '{sex_raw}' is not a standard administrative-sex code.")
        if not ids:
            warn("PID-3 Patient Identifier is empty.")
        out["patient"]["nameAliases"] = [person_name(pid, 5, i) for i in range(1, len(pid.repeats(5)))]
    else:
        warn("No PID segment: demographics unavailable.")

    nok = [{"name": person_name(s, 2), "relationship": s.comp(3, 2) or s.comp(3, 1), "phone": s.comp(5, 1)}
           for s in msg.all("NK1")]
    if nok:
        out["nextOfKin"] = nok

    pv1 = msg.first("PV1")
    if pv1:
        cls = pv1.comp(2, 1)
        out["encounter"] = {
            "class": PATIENT_CLASS.get(cls, cls), "location": location(pv1, 3),
            "attending": provider(pv1, 7), "visitNumber": pv1.comp(19, 1),
            "admitted": fmt_ts(pv1.field(44)), "discharged": fmt_ts(pv1.field(45))}
        if cls and cls not in PATIENT_CLASS:
            warn(f"PV1-2 '{cls}' is not a recognised patient class.")

    al1 = [{"allergen": s.comp(3, 2) or s.comp(3, 1), "severity": SEVERITY.get(s.comp(4, 1), s.comp(4, 1)),
            "reaction": s.comp(5, 1)} for s in msg.all("AL1")]
    if al1:
        out["allergies"] = al1
    dg1 = [{"code": s.comp(3, 1), "description": s.comp(4, 1) or s.comp(3, 2), "type": s.comp(6, 1)}
           for s in msg.all("DG1")]
    if dg1:
        out["diagnoses"] = dg1

    # Orders and results: OBX belongs to the nearest preceding OBR
    orders, current = [], None
    pending_orc = None
    for s in msg.segments:
        if s.name == "ORC":
            pending_orc = {"control": ORDER_CONTROL.get(s.comp(1, 1), s.comp(1, 1)),
                           "placerOrder": s.comp(2, 1), "fillerOrder": s.comp(3, 1),
                           "orderedBy": provider(s, 12), "at": fmt_ts(s.field(9))}
        elif s.name == "OBR":
            svc = coded(s, 4)
            current = {"service": svc["text"], "serviceCode": svc["code"],
                       "placerOrder": s.comp(2, 1), "fillerOrder": s.comp(3, 1),
                       "observedAt": fmt_ts(s.field(7)), "orderedBy": provider(s, 16),
                       "resultStatus": RESULT_STATUS.get(s.comp(25, 1), s.comp(25, 1)),
                       "order": pending_orc, "observations": []}
            pending_orc = None
            orders.append(current)
        elif s.name == "OBX":
            if current is None:
                current = {"service": "(no OBR segment)", "observations": []}
                orders.append(current)
                warn("OBX segment found without a preceding OBR.")
            vtype = s.comp(2, 1)
            if vtype == "SN":
                value = (s.comp(5, 1) + s.comp(5, 2)).strip()
            elif vtype in ("CE", "CWE", "CNE"):
                value = s.comp(5, 2) or s.comp(5, 1)
            else:
                value = s.unescape(s.field(5))
            flag = s.comp(8, 1)
            obs = {"test": coded(s, 3)["text"], "code": coded(s, 3)["code"], "value": value,
                   "units": s.comp(6, 2) or s.comp(6, 1), "referenceRange": s.field(7),
                   "flag": flag, "flagMeaning": ABNORMAL.get(flag, flag), "valueType": vtype,
                   "status": RESULT_STATUS.get(s.comp(11, 1), s.comp(11, 1)), "at": fmt_ts(s.field(14))}
            if vtype == "NM" and value and not re.match(r"^[+-]?\d*\.?\d+$", value):
                warn(f"OBX-5 '{value}' is not numeric but OBX-2 says NM.")
            current["observations"].append(obs)
    if orders:
        out["orders"] = orders
    return out


def critical_results(interp):
    return [(o["service"], ob) for o in interp.get("orders", []) for ob in o["observations"]
            if ob["flag"] in ("LL", "HH", "AA")]


# ---------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------
def report(interp):
    L = []
    m = interp["message"]
    L.append(f"MESSAGE   {m['type']}  {m['description']}".rstrip())
    L.append(f"          control id {m['controlId'] or '-'} | HL7 v{m['version'] or '?'} | sent {m['sentAt']}")
    L.append(f"          {m['from'] or '?'}  ->  {m['to'] or '?'}")
    p = interp.get("patient")
    if p:
        L.append("")
        L.append(f"PATIENT   {p['name'] or '(no name)'}")
        age = f" (age {p['ageAtMessage']})" if p["ageAtMessage"] is not None else ""
        L.append(f"          {p['sex'] or 'sex unknown'} | born {p['dateOfBirth'] or '?'}{age}")
        for i in p["identifiers"]:
            L.append(f"          ID {i['id']} {('(' + i['type'] + ') ') if i['type'] else ''}"
                     f"{('at ' + i['authority']) if i['authority'] else ''}".rstrip())
        for extra in ("address", "phone"):
            if p[extra]:
                L.append(f"          {extra}: {p[extra]}")
        if p["nameAliases"]:
            L.append(f"          also known as: {', '.join(p['nameAliases'])}")
    for k in interp.get("nextOfKin", []):
        L.append(f"NEXT OF KIN  {k['name']} ({k['relationship']}) {k['phone']}".rstrip())
    e = interp.get("encounter")
    if e:
        L.append("")
        L.append(f"ENCOUNTER {e['class'] or '?'} | location {e['location'] or '-'}")
        L.append(f"          visit {e['visitNumber'] or '-'} | attending {e['attending'] or '-'}")
        L.append(f"          admitted {e['admitted'] or '-'} | discharged {e['discharged'] or '-'}")
    for a in interp.get("allergies", []):
        L.append(f"ALLERGY   {a['allergen']} - {a['severity'] or 'severity unknown'}"
                 f"{(' - ' + a['reaction']) if a['reaction'] else ''}")
    for d in interp.get("diagnoses", []):
        L.append(f"DIAGNOSIS {d['code']} {d['description']}".rstrip())
    for o in interp.get("orders", []):
        L.append("")
        L.append(f"ORDER     {o['service']}  [{o.get('resultStatus') or 'status n/a'}]")
        if o.get("order"):
            L.append(f"          {o['order']['control']} | placer {o['order']['placerOrder'] or '-'}"
                     f" | ordered by {o['order']['orderedBy'] or '-'}")
        if o.get("observedAt"):
            L.append(f"          observed {o['observedAt']}")
        for ob in o["observations"]:
            flag = f"  <-- {ob['flagMeaning'].upper()}" if ob["flag"] and ob["flag"] != "N" else ""
            rng = f" (ref {ob['referenceRange']})" if ob["referenceRange"] else ""
            value = ob["value"].replace("\n", " / ")
            L.append(f"   - {ob['test']}: {value} {ob['units']}{rng}{flag}".replace("  (", " (").rstrip())
    crit = critical_results(interp)
    if crit:
        L.append("")
        L.append("!! CRITICAL RESULTS: " + "; ".join(f"{ob['test']} {ob['value']} {ob['units']}" for _, ob in crit))
    if interp["warnings"]:
        L.append("")
        L.append("WARNINGS")
        L.extend(f"   ! {w}" for w in interp["warnings"])
    return "\n".join(L)


def field_table(msg):
    """Every populated field of the interpreted segments, with its HL7 name."""
    rows = []
    for s in msg.segments:
        names = FIELD_NAMES.get(s.name, {})
        total = len(s.parts) - (0 if s.name != "MSH" else -1)
        for n in range(1, total + 1):
            v = s.field(n)
            if v and not (s.name == "MSH" and n == 1):
                rows.append((f"{s.name}-{n}", names.get(n, ""), v))
    return rows


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    text = sys.stdin.read() if argv[1] == "-" else open(argv[1], encoding="utf-8").read()
    try:
        msg = Message(text)
    except ValueError as exc:
        print("Error:", exc)
        return 2
    interp = interpret(msg)
    if "--json" in argv:
        print(json.dumps(interp, indent=2, ensure_ascii=False))
    elif "--fields" in argv:
        for ref, name, value in field_table(msg):
            print(f"{ref:8} {name:26} {value}")
    else:
        print(report(interp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
