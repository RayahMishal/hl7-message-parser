import unittest
from hl7_parser import Message, interpret, report, critical_results, fmt_ts


def load(name):
    with open(name, encoding="utf-8", newline="") as fh:
        return interpret(Message(fh.read()))


class ParserTests(unittest.TestCase):
    def test_adt_demographics_and_encounter(self):
        r = load("sample_adt.hl7")
        self.assertEqual(r["message"]["description"], "Admit a patient")
        p = r["patient"]
        self.assertEqual((p["name"], p["sex"], p["ageAtMessage"]), ("Jane Marie Doe", "Female", 36))
        self.assertEqual(len(p["identifiers"]), 2)            # repeating field PID-3
        self.assertEqual(p["nameAliases"], ["Janie Doe"])     # repeating field PID-5
        self.assertEqual(r["encounter"]["class"], "Inpatient")
        self.assertEqual(r["encounter"]["attending"], "Dr Alan Smith")
        self.assertEqual(r["allergies"][0]["severity"], "Severe")
        self.assertEqual(r["diagnoses"][0]["code"], "I21.9")
        self.assertEqual(r["nextOfKin"][0]["relationship"], "Spouse")
        self.assertEqual(r["warnings"], [])

    def test_oru_observations_and_critical_flags(self):
        r = load("sample_oru.hl7")
        obs = r["orders"][0]["observations"]
        self.assertEqual([o["test"] for o in obs], ["Sodium", "Potassium", "Glucose", "Comment"])
        self.assertEqual(obs[1]["flagMeaning"], "Critically high")
        crit = critical_results(r)
        self.assertEqual([ob["test"] for _, ob in crit], ["Potassium"])
        self.assertIn("Sample slightly haemolysed\nrepeat advised", obs[3]["value"])  # \.br\ escape
        self.assertIn("CRITICAL RESULTS", report(r))

    def test_cardiac_oru_and_title_in_degree_slot(self):
        r = load("sample_oru_cardiac.hl7")
        self.assertEqual(r["encounter"]["attending"], "Dr. Khalid Ahmed")   # 'Dr.' sent in XCN-7
        self.assertEqual(r["encounter"]["visitNumber"], "VN100001")
        self.assertEqual([o["placerOrder"] for o in r["orders"]], ["ORD10001", "ORD10002", "ORD10003"])
        self.assertEqual(r["orders"][0]["observations"][0]["flagMeaning"], "High")
        self.assertEqual(r["warnings"], [])

    def test_order_message(self):
        r = load("sample_orm.hl7")
        o = r["orders"][0]
        self.assertEqual(o["service"], "Complete blood count")
        self.assertEqual(o["order"]["control"], "New order")

    def test_bad_message_produces_warnings(self):
        w = " ".join(load("sample_bad.hl7")["warnings"])
        for needle in ("MSH-10", "PID-7", "PID-8", "PID-3", "OBX segment", "not numeric"):
            self.assertIn(needle, w)

    def test_timestamps_keep_precision(self):
        self.assertEqual(fmt_ts("2026"), "2026")
        self.assertEqual(fmt_ts("202610"), "Oct 2026")
        self.assertEqual(fmt_ts("20261004"), "04 Oct 2026")
        self.assertEqual(fmt_ts("202610040815"), "04 Oct 2026, 08:15")
        self.assertIn("invalid", fmt_ts("20260231"))

    def test_rejects_non_hl7(self):
        with self.assertRaises(ValueError):
            Message("hello world")


if __name__ == "__main__":
    unittest.main()
