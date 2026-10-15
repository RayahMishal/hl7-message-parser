// Runs the browser interpreter (the <script id="core"> in index.html) against the sample files.
const fs = require("fs"), assert = require("assert");
const html = fs.readFileSync(__dirname + "/index.html", "utf8");
const core = /<script id="core">([\s\S]*?)<\/script>/.exec(html)[1];
const m = { exports: {} }; new Function("module", core)(m);
const { parseMessage, interpret, provider } = m.exports;
const load = f => interpret(parseMessage(fs.readFileSync(__dirname + "/" + f, "utf8")));
let n = 0; const t = (name, fn) => { fn(); n++; console.log("ok -", name); };

t("cardiac ORU: patient, visit, three orders", () => {
  const r = load("sample_oru_cardiac.hl7");
  assert.strictEqual(r.patient.name, "Sarah F Doe"); assert.strictEqual(r.patient.age, 54);
  assert.strictEqual(r.encounter.cls, "Inpatient"); assert.strictEqual(r.encounter.visit, "VN100001");
  assert.deepStrictEqual(r.orders.map(o => o.id), ["ORD10001", "ORD10002", "ORD10003"]);
  assert.deepStrictEqual(r.warnings, []);
});
t("title in the degree slot ('Dr.') is shown as a title", () => {
  assert.strictEqual(load("sample_oru_cardiac.hl7").encounter.doc, "Dr. Khalid Ahmed");
});
t("title in the prefix slot and next of kin", () => {
  const r = load("sample_adt.hl7");
  assert.strictEqual(r.encounter.doc, "Dr Alan Smith"); assert.strictEqual(r.kin[0].rel, "Spouse");
});
t("abnormal flags are carried through", () => {
  const o = load("sample_oru_cardiac.hl7").orders[0].obs[0];
  assert.deepStrictEqual([o.test, o.value, o.units, o.ref, o.flag], ["Troponin I", "5.82", "ng/mL", "0.00-0.04", "H"]);
  assert.strictEqual(load("sample_oru.hl7").orders[0].obs[1].meaning, "Critically high");
});
t("broken message produces the same warnings as the Python version", () => {
  const w = load("sample_bad.hl7").warnings.join(" ");
  for (const k of ["MSH-10", "PID-7", "PID-8", "PID-3", "OBX segment", "not numeric"]) assert.ok(w.includes(k), k);
});
t("non-HL7 text is rejected", () => assert.throws(() => parseMessage("hello world")));
console.log(`\n${n} tests passed`);
