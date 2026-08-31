/* Decision inspector.
 *
 * This file renders. It decides nothing. Every classification, every gate
 * verdict and every rendered request on screen came from the Python engine
 * over /api/diagnose - the same call path as `python -m chukta.trace`. If a
 * rule ever appears in this file, it is a bug: the dashboard would then be a
 * second source of truth, and the two would drift the first time one is edited.
 */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const rs = (n) => "Rs " + Math.round(n).toLocaleString("en-IN");

let VOCAB = null;

/* ---------------- tabs ---------------- */

$("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-on", t === btn));
  document.querySelectorAll(".panel").forEach((p) =>
    p.classList.toggle("is-on", p.id === "panel-" + btn.dataset.panel)
  );
  if (btn.dataset.panel === "results") loadResults();
  if (btn.dataset.panel === "policy") loadPolicy();
  if (btn.dataset.panel === "audit") loadAudit();
});

/* ---------------- form ---------------- */

const INPUTS = [
  "reason", "source", "step", "amount", "payment_type", "mandate_category",
  "notice", "afa_completed", "mandate_revoked", "dnd_registered", "opted_out",
  "attach_offer", "send_now",
  "ist_hour",
];

async function boot() {
  VOCAB = await (await fetch("/api/vocab")).json();

  VOCAB.sources.forEach((s) => $("source").append(new Option(s, s)));
  VOCAB.steps.forEach((s) => $("step").append(new Option(s, s)));
  VOCAB.categories.forEach((c) => $("mandate_category").append(new Option(c, c)));
  $("source").value = "issuer";
  $("step").value = "payment_authorization";

  const dl = $("reasons");
  VOCAB.reasons.forEach((r) => dl.append(new Option(r, r)));

  INPUTS.forEach((id) => {
    const node = $(id);
    node.addEventListener(node.tagName === "SELECT" ? "change" : "input", onChange);
  });
  onChange();
}

function onChange() {
  const rail = $("payment_type").value;
  $("mandate-box").hidden = rail !== "mandate";
  $("hour-label").textContent =
    String($("ist_hour").value).padStart(2, "0") + ":00 IST";

  // Show whether the typed slug is one tier 1 knows, without deciding
  // anything: the server still classifies.
  const slug = $("reason").value.trim().toLowerCase();
  const known = VOCAB && VOCAB.reason_classes[slug];
  $("reason-note").textContent = !slug
    ? ""
    : known
    ? "tier 1 → " + known
    : "not in tier 1 — will fall back to source × step";

  diagnose();
}

let pending = null;
async function diagnose() {
  const notice = $("notice").value;
  const body = {
    source: $("source").value,
    step: $("step").value,
    reason: $("reason").value.trim(),
    amount_rupees: Number($("amount").value) || 1,
    payment_type: $("payment_type").value,
    afa_completed: $("afa_completed").checked,
    mandate_category: $("mandate_category").value,
    mandate_revoked: $("mandate_revoked").checked,
    pre_debit_notified_hours_ago: notice === "" ? null : Number(notice),
    dnd_registered: $("dnd_registered").checked,
    opted_out: $("opted_out").checked,
    attach_offer: $("attach_offer").checked,
    send_now: $("send_now").checked,
    ist_hour: Number($("ist_hour").value),
  };

  // Coalesce keystrokes so dragging the slider does not queue 20 requests.
  clearTimeout(pending);
  pending = setTimeout(async () => {
    const res = await fetch("/api/diagnose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return;
    render(await res.json());
  }, 90);
}

/* ---------------- render ---------------- */

function render(data) {
  const d = data.diagnosis;
  const dx = $("diagnosis");
  dx.replaceChildren();

  const top = el("div", "dx-top");
  top.append(el("span", "dx-class", d.klass));
  top.append(el("span", "pill " + d.confidence, d.confidence + " confidence"));
  top.append(el("span", "pill", "tier: " + d.tier));
  if (d.hard_decline) top.append(el("span", "pill hard", "hard decline"));
  dx.append(top);

  const why = el("p", "dx-why");
  why.append(document.createTextNode("matched on "));
  why.append(el("b", null, d.matched || "nothing"));
  why.append(
    document.createTextNode(
      "  ·  " + data.context.rail + "  ·  " +
      rs(data.context.amount_rupees) + "  ·  " + data.context.ist
    )
  );
  dx.append(why);
  if (d.rationale) dx.append(el("p", "dx-why", d.rationale));
  if (d.note) dx.append(el("p", "dx-note", d.note));

  const lad = $("ladder");
  lad.replaceChildren();

  if (!data.steps.length) {
    lad.append(el("p", "empty", "No action defined for this class — case closed."));
    return;
  }

  data.steps.forEach((s) => {
    const box = el("div", "step");

    const head = el("div", "step-head");
    head.append(el("span", "step-n", "STEP " + s.index));
    head.append(el("span", "step-action", s.action));
    if (s.channel !== "none") head.append(el("span", "pill", s.channel));
    if (s.message_class !== "none") head.append(el("span", "pill", s.message_class));
    if (s.frame) head.append(el("span", "pill", s.frame));
    head.append(el("span", "step-when", s.scheduled_ist));
    box.append(head);

    const gates = el("div", "gates");
    s.gates.forEach((g) => {
      const row = el("div", "gate " + (g.passed ? "pass" : "block"));
      row.append(el("span", "v", g.passed ? "pass" : "BLOCK"));
      row.append(el("span", "id", g.rule_id));
      if (g.detail) row.append(el("span", "d", g.detail));
      box.append(row);
      gates.append(row);
    });
    box.append(gates);

    if (s.message) {
      const m = el("div", "msg" + (s.message.blocked ? " blocked" : ""));
      m.append(el("div", "msg-head",
        "MESSAGE — " + s.message.source.replace(/_/g, " ")));
      m.append(el("p", null, s.message.text));
      if (s.message.blocked) {
        m.append(el("div", "msg-head degraded",
          "guard blocked the generated copy: " + s.message.guard_findings.join(", ")));
      }
      box.append(m);
    }

    if (s.blocked_by.length) {
      box.append(
        el("div", "verdict blocked", "NOT EXECUTED — blocked by " + s.blocked_by.join(", "))
      );
    } else if (!s.executed) {
      box.append(el("div", "verdict ok", "deliberate no-op"));
    } else {
      const r = el("div", "req");
      r.append(
        el("div", "req-head", "WOULD SEND — " + s.rendered.call.endpoint +
          "  ·  idem " + s.rendered.idempotency_key.slice(0, 16) + "…")
      );
      if (s.rendered.call.degraded_from) {
        r.append(
          el("div", "req-head degraded",
            "DEGRADED from " + s.rendered.call.degraded_from +
            " — " + s.rendered.call.degraded_because)
        );
      }
      r.append(el("pre", null, JSON.stringify(s.rendered.call.payload, null, 2)));
      box.append(r);
    }
    lad.append(box);
  });
}

/* ---------------- results ---------------- */

let resultsLoaded = false;
async function loadResults() {
  if (resultsLoaded) return;
  const data = await (await fetch("/api/results")).json();
  const wrap = $("results-body");
  wrap.replaceChildren();

  if (data.sweep) {
    const s = data.sweep;
    wrap.append(el("h2", null, "Headline"));
    const h = el("div", "headline");
    h.append(el("div", "big", "+" + rs(s.mean) + " mean incremental"));
    h.append(
      el("div", "meta",
        s.seeds.length + " seeds × n=" + s.n +
        "  ·  95% CI [" + rs(s.ci95[0]) + ", " + rs(s.ci95[1]) + "]" +
        "  ·  positive in " + s.positive + " of " + s.seeds.length +
        "  ·  range [" + rs(s.min) + ", " + rs(s.max) + "]")
    );
    wrap.append(h);

    if (s.sensitivity) {
      wrap.append(el("h2", null, "Sensitivity — one belief perturbed at a time"));
      const t = el("table");
      t.innerHTML =
        "<thead><tr><th>Scenario</th><th class='num'>Mean incremental</th>" +
        "<th class='num'>Seeds positive</th></tr></thead>";
      const tb = el("tbody");
      s.sensitivity.forEach((r) => {
        const tr = el("tr", r.positive < r.of ? "bad" : null);
        tr.append(el("td", null, r.scenario));
        tr.append(el("td", "num", rs(r.mean)));
        tr.append(el("td", "num", r.positive + " / " + r.of));
        tb.append(tr);
      });
      t.append(tb);
      wrap.append(t);

      const bad = s.sensitivity.filter((r) => r.positive < r.of);
      if (bad.length) {
        const c = el("div", "caveat");
        c.append(el("b", null, "The result is not robust. "));
        c.append(document.createTextNode(
          "It depends on " + bad.map((b) => "“" + b.scenario + "”").join(" and ") +
          ". If the behavioural message frames carry no lift in a payments " +
          "context, this policy is net negative. Those frames are transplanted " +
          "from tax-compliance RCTs and nothing here tests whether they transfer."
        ));
        wrap.append(c);
      }
    }
  }

  if (data.qini) {
    wrap.append(el("h2", null, "Qini — incremental revenue vs fraction targeted"));
    const box = el("div", "qini");
    box.append(qiniSvg(data.qini));
    wrap.append(box);
  }

  if (data.run) {
    wrap.append(el("h2", null, "One run in detail — seed " + data.run.seed));
    const a = data.run.arms;
    const t = el("table");
    t.innerHTML =
      "<thead><tr><th></th><th class='num'>control</th><th class='num'>chukta</th></tr></thead>";
    const tb = el("tbody");
    const rows = [
      ["gross recovery rate", (x) => (x.recovery_rate * 100).toFixed(1) + "%"],
      ["  self-recovered", (x) => x.recovered_by_self],
      ["  policy-driven", (x) => x.recovered_by_action],
      ["recovered", (x) => rs(x.recovered_rupees)],
      ["charge attempts", (x) => x.attempts],
      ["customer contacts", (x) => x.contacts],
      ["outreach-induced churn", (x) => x.churned],
      ["actions blocked by gates", (x) => x.blocked_actions],
    ];
    rows.forEach(([label, f]) => {
      const tr = el("tr");
      tr.append(el("td", null, label));
      tr.append(el("td", "num", String(f(a.control))));
      tr.append(el("td", "num", String(f(a.chukta))));
      tb.append(tr);
    });
    t.append(tb);
    wrap.append(t);
  }

  if (!data.run && !data.sweep) {
    wrap.append(el("p", "empty", "No results yet. Run: python -m sim.run && python -m eval.sweep --seeds 12 --sensitivity --json"));
  }
  resultsLoaded = true;
}

function qiniSvg(q) {
  const W = 640, H = 260, P = 34;
  const max = Math.max(...q.oracle_cumulative, ...q.cumulative);
  const x = (f) => P + f * (W - P * 2);
  const y = (v) => H - P - (v / max) * (H - P * 2);
  const path = (arr) =>
    arr.map((v, i) => (i ? "L" : "M") + x(q.fractions[i]).toFixed(1) + " " + y(v).toFixed(1)).join(" ");

  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label",
    `Qini curve. Model ranking reaches ${Math.round(q.final)} rupees; oracle ceiling higher.`);

  const line = (x1, y1, x2, y2, stroke, dash) => {
    const l = document.createElementNS(ns, "line");
    l.setAttribute("x1", x1); l.setAttribute("y1", y1);
    l.setAttribute("x2", x2); l.setAttribute("y2", y2);
    l.setAttribute("stroke", stroke);
    if (dash) l.setAttribute("stroke-dasharray", dash);
    svg.append(l);
  };
  const text = (tx, ty, s, fill, anchor) => {
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", tx); t.setAttribute("y", ty);
    t.setAttribute("fill", fill || "#5f7375");
    t.setAttribute("font-size", "10");
    t.setAttribute("font-family", "ui-monospace, monospace");
    if (anchor) t.setAttribute("text-anchor", anchor);
    t.textContent = s;
    svg.append(t);
  };

  line(P, H - P, W - P, H - P, "#243237");
  line(P, P, P, H - P, "#243237");
  line(x(0), y(0), x(1), y(q.final), "#3a4a4e", "3 3");  // random targeting

  const add = (arr, stroke, width) => {
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", path(arr));
    p.setAttribute("fill", "none");
    p.setAttribute("stroke", stroke);
    p.setAttribute("stroke-width", width);
    svg.append(p);
  };
  add(q.oracle_cumulative, "#2c6b61", 1.5);
  add(q.cumulative, "#54b7a5", 2);

  text(P, P - 10, "cumulative incremental revenue", "#8fa3a3");
  text(W - P, H - P + 16, "100% of file", null, "end");
  text(P, H - P + 16, "0%");
  text(x(0.55), y(q.final) - 8, "model  qini " + q.coefficient.toFixed(3), "#54b7a5");
  text(x(0.3), y(q.oracle_cumulative[Math.round(q.oracle_cumulative.length * 0.3)]) - 8,
    "oracle ceiling", "#2c6b61");
  return svg;
}

/* ---------------- policy / audit ---------------- */

let policyLoaded = false;
async function loadPolicy() {
  if (policyLoaded) return;
  $("policy-body").textContent = await (await fetch("/api/policy")).text();
  policyLoaded = true;
}

async function loadAudit() {
  const data = await (await fetch("/api/audit?limit=80")).json();
  const head = $("audit-head");
  const body = $("audit-body");
  body.replaceChildren();

  if (!data.path) {
    head.textContent = "No audit file yet. Run: python -m sim.run --n 300 --seed 20260829";
    return;
  }
  head.textContent =
    `${data.path} — showing the last ${data.rows.length} of ${data.total} rows. ` +
    `Append-only: a correction is a new row, never an edit.`;

  data.rows.forEach((r) => {
    const blocked = r.blocking_rules && r.blocking_rules.length;
    const row = el("div", "arow " + (blocked ? "blocked" : r.executed ? "exec" : ""));
    const put = (k, v) => {
      row.append(el("span", "k", k));
      row.append(el("span", null, String(v)));
    };
    if (r.kind) {
      put("note", r.kind);
      body.append(row);
      return;
    }
    put("", r.arm);
    put("class", r.klass);
    put("action", r.action && r.action.type);
    if (blocked) put("blocked", r.blocking_rules.join(","));
    else put("exec", r.executed);
    body.append(row);
  });
}

boot();
