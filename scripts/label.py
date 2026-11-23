"""Serve a local editor for Mendeley labels."""

import argparse
import json
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from stat import S_IWUSR

from duebook.bench.real import annotation_sheet
from duebook.bench.real_labels import DRAFT_ANNOTATOR, STATES, UNCLEAR, validate
from duebook.bench.real_sources import digest, read_frozen, write

PAGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Support transcript labeling</title>
<style>
  body { font: 16px system-ui; margin: 2rem auto; padding: 0 1rem; max-width: 88rem }
  h1, h2 { margin-top: 0 } h2 { font-size: 1.25rem }
  main { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-top: 1rem }
  main[hidden] { display: none }
  label, textarea { display: block } label { margin: .4rem 0 }
  textarea { width: 100%; box-sizing: border-box }
  button, input, select, textarea { font: inherit } button { margin: .15rem .3rem .15rem 0 }
  article, fieldset { border: 1px solid #a4aab4; border-radius: .35rem; padding: .65rem;
    margin: 0 0 .65rem; min-width: 0 }
  .customer { border-left: 4px solid #2271a5 } .agent { border-left: 4px solid #46743a }
  .withheld { color: #4e5561; border-left: 4px solid #a4aab4 }
  .message { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: .4rem }
  #messages { max-height: 75vh; overflow: auto }
  #status { display: block; padding: .5rem 0 } #status.error { color: #ab1f21 }
  @media (max-width: 52rem) { main { display: block } #messages { max-height: 45vh } }
</style>
<h1>Support transcript labeling</h1>
<label>Thread <select id="thread" disabled></select></label>
<output id="status" aria-live="polite">Loading frozen transcripts…</output>
<main hidden>
  <section><h2>Messages</h2><div id="messages"></div></section>
  <section>
    <h2>Annotations</h2>
    <p>Highlight text in a message, then choose <strong>Use selection</strong> in a quote row.</p>
    <form id="editor">
      <label>Your name <input id="annotator" autocomplete="off"></label>
      <label><input id="complete" type="checkbox"> Mark thread reviewed</label>
      <div id="commitments"></div><button type="button" id="promise">Add promise</button>
      <div id="ambiguities"></div><button type="button" id="unclear">Add ambiguity</button>
      <p><button id="save">Save thread</button></p>
    </form>
  </section>
</main>
<template id="commitment">
  <fieldset>
    <legend>Promise</legend>
    <label>Action <input class="action" required></label>
    <label>Owing side
      <select class="side"><option>company</option><option>customer</option></select></label>
    <label>Owner actor ID, if known <input class="actor"></label>
    <fieldset><legend>Opening and restatements</legend><div class="opening"></div></fieldset>
    <fieldset>
      <legend>Deadline</legend>
      <label>Kind <select class="kind">
        <option>absent</option><option>ambiguous</option><option>exact</option>
      </select></label>
      <label>Exact instant, with UTC offset
        <input class="at" placeholder="2017-10-06T18:00:00Z"></label>
      <label>Deadline note <input class="deadline-note"></label>
      <div class="deadline"></div>
    </fieldset>
    <fieldset>
      <legend>Outcome</legend>
      <label>Observed state <select class="state"></select></label>
      <div class="outcome"></div>
    </fieldset>
    <button type="button" class="remove">Remove promise</button>
  </fieldset>
</template>
<template id="ambiguity">
  <fieldset>
    <legend>Ambiguity</legend>
    <label>Kind <select class="kind"></select></label>
    <label>Note <input class="note" required></label>
    <div class="evidence"></div>
    <button type="button" class="remove">Remove ambiguity</button>
  </fieldset>
</template>
<template id="evidence">
  <div>
    <label>Source message <select class="source" required></select></label>
    <label>Verbatim quote <textarea rows="2" required></textarea></label>
    <button type="button" class="selection">Use selection</button>
    <button type="button" class="remove">Remove quote</button>
  </div>
</template>
<script>
const byId = id => document.getElementById(id);
const clone = id => byId(id).content.firstElementChild.cloneNode(true);
const option = (value, label = value) => new Option(label, value);
let data, active, original, selected, dirty = false;

function status(message, error = false) {
  byId("status").textContent = message;
  byId("status").classList.toggle("error", error);
}

document.addEventListener("selectionchange", () => {
  const selection = window.getSelection();
  const {anchorNode, focusNode, anchorOffset, focusOffset} = selection;
  const message = anchorNode?.parentElement;
  if (selection.isCollapsed || anchorNode !== focusNode) return;
  if (message?.classList.contains("message") && message.dataset.id) {
    const start = Math.min(anchorOffset, focusOffset);
    const end = Math.max(anchorOffset, focusOffset);
    selected = {message_id: message.dataset.id, quote: anchorNode.data.slice(start, end)};
  }
});

function evidence(container, entries) {
  const add = document.createElement("button");
  add.type = "button";
  add.textContent = "Add quote";
  container.append(add);
  function row({message_id = "", quote = ""} = {}) {
    const item = clone("evidence");
    const source = item.querySelector("select");
    const box = item.querySelector("textarea");
    source.add(option("", "Choose a message"));
    for (const m of active.messages.filter(m => !m.withheld)) {
      source.add(option(m.id, m.id + " · " + m.role));
    }
    source.value = message_id;
    box.value = quote;
    item.querySelector(".selection").onclick = () => {
      if (!selected) return status("Highlight a quote in the transcript first.", true);
      source.value = selected.message_id;
      box.value = selected.quote;
      dirty = true;
    };
    item.querySelector(".remove").onclick = () => { item.remove(); dirty = true; };
    item.read = () => {
      const text = active.messages.find(m => m.id === source.value).text;
      // Textarea values normalize CRLF. Recover the exact bytes from the source.
      const normalized = text.replace(/\r\n?/g, "\n");
      const start = normalized.indexOf(box.value);
      if (start < 0) return {message_id: source.value, quote: box.value};
      const breaks = /\r\n?|[\s\S]/gu;
      const units = text.match(breaks);
      const from = Array.from(normalized.slice(0, start)).length;
      const length = Array.from(box.value).length;
      return {message_id: source.value, quote: units.slice(from, from + length).join("")};
    };
    add.before(item);
  }
  add.onclick = () => { row(); dirty = true; };
  entries.forEach(row);
  return () => Array.from(container.children).filter(item => item !== add).map(item => item.read());
}

function entry(container, kind, value) {
  const item = clone(kind);
  const field = (name, initial, choices) => {
    const control = item.querySelector("." + name);
    for (const choice of choices || []) control.add(option(choice, choice.replaceAll("_", " ")));
    control.value = initial;
    return () => control.value.trim();
  };
  const quotes = (name, entries) => evidence(item.querySelector("." + name), entries);
  if (kind === "commitment") {
    item.dataset.id = value.id;
    const action = field("action", value.action);
    const side = field("side", value.owner_side);
    const actor = field("actor", value.owner_actor_id || "");
    const opening = quotes("opening", value.opening.length ? value.opening : [{}]);
    const deadline = field("kind", value.deadline.kind);
    const at = field("at", value.deadline.at || "");
    const note = field("deadline-note", value.deadline.note || "");
    const dueEvidence = quotes("deadline", value.deadline.evidence);
    const state = field("state", value.fulfillment.state, data.states);
    const outcome = quotes("outcome", value.fulfillment.evidence);
    item.read = () => ({
      id: value.id, action: action(), owner_side: side(), owner_actor_id: actor() || null,
      opening: opening(),
      deadline: {kind: deadline(), at: deadline() === "exact" ? at() : null,
                 evidence: dueEvidence(), ...(note() ? {note: note()} : {})},
      fulfillment: {state: state(), evidence: outcome()},
    });
  } else {
    const kind = field("kind", value.kind, data.unclear);
    const note = field("note", value.note);
    const sources = quotes("evidence", value.evidence.length ? value.evidence : [{}]);
    item.read = () => ({kind: kind(), note: note(), evidence: sources()});
  }
  item.querySelector(":scope > .remove").onclick = () => { item.remove(); dirty = true; };
  container.append(item);
}

function show(id) {
  active = data.cases.find(item => item.id === id);
  original = data.labels.tickets.find(item => item.ticket_id === id);
  selected = null;
  byId("messages").replaceChildren();
  for (const m of active.messages) {
    const article = document.createElement("article");
    article.className = m.withheld ? "withheld" : m.role;
    const heading = document.createElement("strong");
    heading.textContent = m.id + " · " + (m.author_role || m.role) + " · " + m.actor_id
      + " · " + m.at + (m.withheld ? " · Role uncertain, review context only" : "");
    const body = document.createElement("div");
    body.className = "message";
    if (!m.withheld) body.dataset.id = m.id;
    body.textContent = m.text;
    article.append(heading, body);
    byId("messages").append(article);
  }
  byId("messages").scrollTop = 0;
  byId("annotator").value = original.annotator === data.draft_annotator ? "" : original.annotator;
  byId("complete").checked = original.review === "complete";
  for (const [id, kind, entries] of [
    ["commitments", "commitment", original.commitments],
    ["ambiguities", "ambiguity", original.ambiguous],
  ]) {
    const container = byId(id);
    container.replaceChildren();
    for (const item of entries) entry(container, kind, item);
  }
  dirty = false;
  status(data.labels.tickets.filter(t => t.review === "complete").length + " / " +
         data.cases.length + " reviewed");
}

byId("editor").oninput = () => { dirty = true; };
byId("thread").onchange = event => {
  if (dirty && !confirm("Discard unsaved changes to this thread?")) event.target.value = active.id;
  else show(event.target.value);
};
window.addEventListener("beforeunload", event => { if (dirty) event.preventDefault(); });
const rows = id => Array.from(byId(id).children).map(item => item.read());
byId("promise").onclick = () => {
  const saved = new Set(Array.from(byId("commitments").children, item => item.dataset.id));
  let index = 1;
  while (saved.has("P" + index)) index++;
  entry(byId("commitments"), "commitment", {
    id: "P" + index, action: "", owner_side: "company", opening: [],
    deadline: {kind: "absent", evidence: []}, fulfillment: {state: "unknown", evidence: []},
  });
  dirty = true;
};
byId("unclear").onclick = () => {
  entry(byId("ambiguities"), "ambiguity", {kind: "identity", note: "", evidence: []});
  dirty = true;
};

byId("editor").onsubmit = async event => {
  event.preventDefault();
  const ticket = {
    ...original, annotator: byId("annotator").value.trim() || original.annotator,
    review: byId("complete").checked ? "complete" : "pending",
    commitments: rows("commitments"), ambiguous: rows("ambiguities"),
  };
  const controls = Array.from(document.querySelectorAll("button, input, textarea, select"));
  controls.forEach(control => { control.disabled = true; });
  try {
    const response = await fetch("/save", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({original, ticket}),
    });
    if (!response.ok) throw Error(await response.text());
    data.labels.tickets[data.labels.tickets.indexOf(original)] = ticket;
    show(ticket.ticket_id);
    byId("status").textContent = "Saved · " + byId("status").textContent;
  } catch (error) {
    status(error.message, true);
  } finally {
    controls.forEach(control => { control.disabled = false; });
  }
};

fetch("/data").then(response => {
  if (!response.ok) throw Error("Could not load frozen transcripts.");
  return response.json();
}).then(payload => {
  data = payload;
  for (const c of data.cases) byId("thread").add(option(c.id));
  const first = data.labels.tickets.find(t => t.review !== "complete") || data.labels.tickets[0];
  byId("thread").value = first.ticket_id;
  show(first.ticket_id);
  byId("thread").disabled = false;
  document.querySelector("main").hidden = false;
}).catch(error => status(error.message, true));
</script>
</html>"""


class Labels:
    def __init__(self, bundle_path: Path, path: Path, source_review: Path | None = None):
        bundle, bundle_sha = read_frozen(bundle_path)
        self.cases = {case["id"]: case for case in bundle["cases"]}
        if bundle["schema"] != 1 or not self.cases or any(
            case["source"] != "mendeley" for case in self.cases.values()
        ):
            raise ValueError("a frozen Mendeley bundle is required")
        self.path = path
        self.context = {}
        if source_review is not None:
            release, _ = read_frozen(source_review)
            if release["bundle_sha256"] != bundle_sha:
                raise ValueError("source review file does not match the frozen bundle")
            self.context = {case["id"]: case["messages"] for case in release["cases"]}
        self.sheet = annotation_sheet(bundle, bundle_sha)
        self.saved_sha = None
        if path.exists():
            if not path.stat().st_mode & S_IWUSR:
                raise ValueError("This label file is read-only. Open the review copy.")
            sheet, self.saved_sha = read_frozen(path)
            if (
                sheet.keys() != self.sheet.keys()
                or sheet["bundle_sha256"] != bundle_sha
                or [t["ticket_id"] for t in sheet["tickets"]] != list(self.cases)
            ):
                raise ValueError("label file does not match the frozen bundle")
            self.sheet = sheet

    def messages(self, case):
        eligible = {m["id"]: m for m in case["messages"]}
        for message in self.context.get(case["id"], case["messages"]):
            source = eligible.get(message["id"], message)
            yield {k: source[k] for k in ("id", "text", "at", "actor_id")} | {
                "role": source.get("role"),
                "author_role": source.get("author_role"),
                "withheld": source["id"] not in eligible,
            }

    def data(self):
        return {
            "cases": [
                {
                    "id": case["id"],
                    "messages": list(self.messages(case)),
                }
                for case in self.cases.values()
            ],
            "labels": self.sheet,
            "states": sorted(STATES),
            "unclear": sorted(UNCLEAR),
            "draft_annotator": DRAFT_ANNOTATOR,
        }

    def save(self, original, ticket):
        case = self.cases[ticket["ticket_id"]]
        index = list(self.cases).index(case["id"])
        current = self.sheet["tickets"][index]
        current_sha = digest(self.path) if self.path.exists() else None
        if original != current or current_sha != self.saved_sha:
            raise ValueError("Labels changed on disk or in another tab. Reload before saving.")
        if ticket.keys() != current.keys() or ticket["review"] not in ("pending", "complete"):
            raise ValueError("Invalid thread review.")
        if ticket["review"] == "complete" and ticket["annotator"].strip() in ("", DRAFT_ANNOTATOR):
            raise ValueError("Enter your name before marking the thread reviewed.")
        validate(case, ticket)
        updated = self.sheet["tickets"].copy()
        updated[index] = ticket
        sheet = self.sheet | {"tickets": updated}
        write(self.path, sheet, overwrite=self.saved_sha is not None)
        self.saved_sha = digest(self.path)
        self.sheet = sheet


def run_server(labels, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, kind, content):
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if self.headers.get("Host") != host:
                self.send_error(403)
            elif self.path == "/":
                self.respond(200, "text/html", PAGE.encode())
            elif self.path == "/data":
                self.respond(200, "application/json", json.dumps(labels.data()).encode())
            else:
                self.send_error(404)

        def do_POST(self):
            if (
                self.path != "/save"
                or self.headers.get("Host") != host
                or self.headers.get("Origin") != f"http://{host}"
                or self.headers.get_content_type() != "application/json"
            ):
                self.send_error(403)
                return
            try:
                size = int(self.headers["Content-Length"])
                if not 0 < size <= 1_048_576:
                    raise ValueError("Invalid request size.")
                payload = json.loads(self.rfile.read(size))
                labels.save(payload["original"], payload["ticket"])
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                self.respond(400, "text/plain", str(error).encode())
            else:
                self.respond(200, "text/plain", b"Saved")

    with HTTPServer(("127.0.0.1", port), Handler) as server:
        host = f"127.0.0.1:{server.server_port}"
        print(f"Open http://{host} · labels: {labels.path}", flush=True)
        with suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--source-review", type=Path)
    args = parser.parse_args()
    try:
        run_server(Labels(args.bundle, args.labels, args.source_review))
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
