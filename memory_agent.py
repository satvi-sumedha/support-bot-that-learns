"""
Support Bot That Learns  -  Graph Hacks, Track 2 (Agent Memory and Coordination)

Three small agents handle IT support tickets. They never pass data to each
other directly. All shared state lives in the customer's FalkorDB graph:

  Triage agent   reads the ticket text, finds the topic, creates the Ticket node
  Resolver agent reads the Ticket from the graph, replays a saved path (or
                 explores by trial and error), writes the outcome back
  Learner agent  reads the outcome from the graph and updates memory:
                 procedural (the path), semantic (a distilled Fact), and
                 keeps the episode (the Ticket and who worked on it)

Each tenant (customer) gets its own graph, so memory is never shared.

Graph model:
  (:Topic {name, uses})-[:HAS_STEP]->(:Step {pos, action})
  (:Step)-[:NEXT]->(:Step)
  (:Ticket {text, status, source, steps_used, trail, worked})-[:ABOUT]->(:Topic)
  (:Agent {name})-[:WORKED_ON {seq, note}]->(:Ticket)      the handoff trail
  (:Topic)-[:KNOWS]->(:Fact {text, evidence})              semantic memory
"""

import json
import os

from falkordb import FalkorDB
import config

db = FalkorDB(
    host=config.HOST,
    port=config.PORT,
    username=config.USERNAME,
    password=config.PASSWORD,
)


def graph_for(tenant):
    """One separate graph per tenant = separate memory per customer."""
    return db.select_graph("tenant_" + tenant)


def reset(tenant):
    """Wipe a tenant's graph (so the demo can start fresh)."""
    graph_for(tenant).query("MATCH (n) DETACH DELETE n")


# ---------------------------------------------------------------
# PROCEDURAL MEMORY: saved paths
# ---------------------------------------------------------------

def get_path(tenant, topic):
    """Return the saved winning steps for this topic (empty list if none)."""
    g = graph_for(tenant)
    result = g.query(
        "MATCH (:Topic {name:$topic})-[:HAS_STEP]->(s:Step) "
        "RETURN s.action ORDER BY s.pos",
        {"topic": topic},
    )
    return [row[0] for row in result.result_set]


def save_path(tenant, topic, actions):
    """Save the winning steps as a chain of Step nodes.
    Uses MERGE, so saving the same path twice (for example from two tickets
    arriving at the same moment) never creates duplicate nodes."""
    g = graph_for(tenant)
    g.query("MERGE (:Topic {name:$topic})", {"topic": topic})
    for pos, action in enumerate(actions):
        g.query(
            "MATCH (t:Topic {name:$topic}) "
            "MERGE (t)-[:HAS_STEP]->(s:Step {pos:$pos}) "
            "SET s.action = $action",
            {"topic": topic, "pos": pos, "action": action},
        )
    for pos in range(len(actions) - 1):
        g.query(
            "MATCH (t:Topic {name:$topic})-[:HAS_STEP]->(a:Step {pos:$p1}), "
            "(t)-[:HAS_STEP]->(b:Step {pos:$p2}) "
            "MERGE (a)-[:NEXT]->(b)",
            {"topic": topic, "p1": pos, "p2": pos + 1},
        )


# ---------------------------------------------------------------
# THE WORLD THE AGENTS WORK IN (simulated solver for now)
# ---------------------------------------------------------------
# CANDIDATES = everything an agent might try, in the order it would guess.
# WORKING    = the steps that actually fix the problem (the "secret").
# explore() is the only simulated part. Swap it for an LLM call to go live.

CANDIDATES = {
    "wifi": ["restart browser", "clear cache", "restart laptop",
             "restart router", "forget network and reconnect"],
    "printer": ["restart printer", "check paper", "reconnect usb cable",
                "clear print queue", "reinstall driver"],
    "password": ["clear cache", "try another browser",
                 "reset via email", "log in with new password"],
}

WORKING = {
    "wifi": ["restart router", "forget network and reconnect"],
    "printer": ["clear print queue", "reinstall driver"],
    "password": ["reset via email", "log in with new password"],
}


def explore(topic):
    """Trial and error. Returns (everything tried, the steps that worked)."""
    tried, useful = [], []
    for action in CANDIDATES[topic]:
        tried.append(action)
        if action in WORKING[topic]:
            useful.append(action)
        if len(useful) == len(WORKING[topic]):
            break
    return tried, useful


def classify(text):
    """Find the ticket's topic. If ANTHROPIC_API_KEY is set, Claude reads the
    ticket; otherwise (or if that call fails) a keyword match is used.
    Returns (topic or None, short explanation)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            msg = client.messages.create(
                model="claude-haiku-5-5",
                max_tokens=10,
                messages=[{"role": "user", "content":
                    "Classify this IT support ticket as exactly one of: "
                    + ", ".join(CANDIDATES)
                    + ". Reply with only that one word, or none if it fits none.\n\nTicket: "
                    + text}],
            )
            word = msg.content[0].text.strip().lower()
            if word in CANDIDATES:
                return word, "Claude read the ticket and chose the topic '" + word + "'."
            if word == "none":
                return None, ""
        except Exception:
            pass  # fall back to keywords below
    lowered = text.lower()
    for topic in CANDIDATES:
        if topic in lowered:
            return topic, "Matched the keyword '" + topic + "' in the ticket."
    return None, ""


# ---------------------------------------------------------------
# THE THREE AGENTS (they share state only through the graph)
# ---------------------------------------------------------------

def _log_work(g, agent, ticket_id, seq, note):
    """Record who worked on a ticket, in what order, and what they did."""
    g.query(
        "MERGE (a:Agent {name:$agent}) "
        "WITH a MATCH (k:Ticket) WHERE id(k) = $tid "
        "CREATE (a)-[:WORKED_ON {seq:$seq, note:$note}]->(k)",
        {"agent": agent, "tid": ticket_id, "seq": seq, "note": note},
    )


def triage(tenant, text):
    """Agent 1: turn raw text into a Ticket node linked to its Topic."""
    topic, how = classify(text)
    if topic is None:
        return {"error": "I only know these topics: " + ", ".join(CANDIDATES)}
    g = graph_for(tenant)
    result = g.query(
        "MERGE (t:Topic {name:$topic}) "
        "CREATE (k:Ticket {text:$text, status:'triaged'})-[:ABOUT]->(t) "
        "RETURN id(k)",
        {"topic": topic, "text": text},
    )
    ticket_id = result.result_set[0][0]
    _log_work(g, "triage", ticket_id, 1,
              how + " Created the ticket and handed it to the resolver.")
    return {"ticket_id": ticket_id, "topic": topic}


def resolver(tenant, ticket_id):
    """Agent 2: read the ticket from the graph, solve it, write the result back."""
    g = graph_for(tenant)
    topic = g.query(
        "MATCH (k:Ticket)-[:ABOUT]->(t:Topic) WHERE id(k) = $tid RETURN t.name",
        {"tid": ticket_id},
    ).result_set[0][0]

    saved = get_path(tenant, topic)
    if saved:
        steps, worked, source = saved, saved, "memory"
        g.query("MATCH (t:Topic {name:$topic}) SET t.uses = coalesce(t.uses, 0) + 1",
                {"topic": topic})
        note = ("Found a saved path for '" + topic + "' in this customer's graph and replayed its "
                + str(len(steps)) + " steps. No guessing needed.")
    else:
        tried, worked = explore(topic)
        steps, source = tried, "exploring"
        note = ("No saved path for '" + topic + "' yet, so it tried " + str(len(tried))
                + " fixes. " + str(len(worked)) + " of them worked.")

    g.query(
        "MATCH (k:Ticket) WHERE id(k) = $tid "
        "SET k.status = 'resolved', k.source = $source, k.steps_used = $n, "
        "k.trail = $trail, k.worked = $worked",
        {"tid": ticket_id, "source": source, "n": len(steps),
         "trail": json.dumps(steps), "worked": json.dumps(worked)},
    )
    _log_work(g, "resolver", ticket_id, 2, note + " Handed the result to the learner.")
    return {"topic": topic, "steps": steps, "source": source, "count": len(steps)}


def learner(tenant, ticket_id):
    """Agent 3: read the outcome from the graph and update long-term memory."""
    g = graph_for(tenant)
    topic, source, worked_json = g.query(
        "MATCH (k:Ticket)-[:ABOUT]->(t:Topic) WHERE id(k) = $tid "
        "RETURN t.name, k.source, k.worked",
        {"tid": ticket_id},
    ).result_set[0]
    worked = json.loads(worked_json)

    if source == "exploring":
        save_path(tenant, topic, worked)
        note = ("Saved the " + str(len(worked)) + " steps that worked as a path, "
                "so the next '" + topic + "' ticket can skip the rest.")
    else:
        uses = g.query("MATCH (t:Topic {name:$topic}) RETURN t.uses",
                       {"topic": topic}).result_set[0][0]
        note = "The saved path worked again. It has now been replayed " + str(uses) + " time(s)."

    # semantic memory: a short fact distilled from the episodes
    evidence = g.query(
        "MATCH (k:Ticket {status:'resolved'})-[:ABOUT]->(t:Topic {name:$topic}) RETURN count(k)",
        {"topic": topic},
    ).result_set[0][0]
    fact = topic + " problems are fixed by: " + ", then ".join(worked) + "."
    g.query(
        "MATCH (t:Topic {name:$topic}) "
        "MERGE (t)-[:KNOWS]->(f:Fact) SET f.text = $text, f.evidence = $ev",
        {"topic": topic, "text": fact, "ev": evidence},
    )
    _log_work(g, "learner", ticket_id, 3,
              note + " Updated the fact it knows about '" + topic + "' (based on "
              + str(evidence) + " ticket(s)).")


def get_chain(tenant, ticket_id):
    """The handoff trail for one ticket: which agent did what, in order."""
    g = graph_for(tenant)
    result = g.query(
        "MATCH (a:Agent)-[w:WORKED_ON]->(k:Ticket) WHERE id(k) = $tid "
        "RETURN a.name, w.seq, w.note ORDER BY w.seq",
        {"tid": ticket_id},
    )
    return [{"agent": r[0], "seq": r[1], "note": r[2]} for r in result.result_set]


def handle_ticket(tenant, text):
    """Run one ticket through triage -> resolver -> learner."""
    t = triage(tenant, text)
    if "error" in t:
        print("  [" + tenant + "] '" + text + "' -> unknown topic, skipping")
        return t
    r = resolver(tenant, t["ticket_id"])
    learner(tenant, t["ticket_id"])
    chain = get_chain(tenant, t["ticket_id"])

    print("  [" + tenant + "] '" + text + "'")
    print("      " + r["source"] + ": " + str(r["count"]) + " steps -> " + ", ".join(r["steps"]))
    r["ticket_id"] = t["ticket_id"]
    r["chain"] = chain
    return r


# ---------------------------------------------------------------
# WHAT THE WEB PAGE READS
# ---------------------------------------------------------------

def get_state(tenant):
    """Everything the web page needs: paths, usage, facts, tickets and agent trails."""
    g = graph_for(tenant)
    paths = {}
    for name, action in g.query(
        "MATCH (t:Topic)-[:HAS_STEP]->(s:Step) RETURN t.name, s.action ORDER BY t.name, s.pos"
    ).result_set:
        paths.setdefault(name, []).append(action)

    uses = {name: n for name, n in g.query(
        "MATCH (t:Topic) RETURN t.name, coalesce(t.uses, 0)").result_set}

    facts = {name: {"text": text, "evidence": ev} for name, text, ev in g.query(
        "MATCH (t:Topic)-[:KNOWS]->(f:Fact) RETURN t.name, f.text, f.evidence").result_set}

    chains = {}
    for tid, agent, seq, note in g.query(
        "MATCH (a:Agent)-[w:WORKED_ON]->(k:Ticket) RETURN id(k), a.name, w.seq, w.note "
        "ORDER BY id(k), w.seq"
    ).result_set:
        chains.setdefault(tid, []).append({"agent": agent, "seq": seq, "note": note})

    tickets = []
    for tid, text, topic, count, source in g.query(
        "MATCH (k:Ticket)-[:ABOUT]->(t:Topic) WHERE k.steps_used IS NOT NULL "
        "RETURN id(k), k.text, t.name, k.steps_used, k.source ORDER BY id(k)"
    ).result_set:
        tickets.append({"id": tid, "text": text, "topic": topic, "count": count,
                        "source": source, "chain": chains.get(tid, [])})

    return {"paths": paths, "uses": uses, "facts": facts, "tickets": tickets}


# ---------------------------------------------------------------
# TERMINAL DEMO
# ---------------------------------------------------------------

if __name__ == "__main__":
    reset("acme")
    reset("zeta")

    print("\nTENANT ACME: same kind of problem, three times")
    handle_ticket("acme", "wifi not working")
    handle_ticket("acme", "my wifi is down again")
    handle_ticket("acme", "wifi keeps disconnecting")

    print("\nTENANT ZETA: a different customer starts with NO memory")
    handle_ticket("zeta", "wifi not working")

    print("\nDone! Open FalkorDB Browser and look at graphs: tenant_acme, tenant_zeta")
