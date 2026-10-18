# Support Bot That Learns

**Graph Hacks: Context for AI Agents (WeMakeDevs x FalkorDB). Track 2: Agent Memory and Coordination.**

**Live demo:** https://YOUR-RENDER-URL.onrender.com

Three small agents handle IT support tickets. They never pass data to each other directly: everything they know and everything they hand over lives in a **FalkorDB graph**, one graph per customer. The first time a kind of problem shows up, the agents work it out by trial and error. The winning steps are saved as a path in the graph, and the next similar ticket replays that path instead of guessing.

## The problem

Agents lose track of what happened in earlier sessions, and when they do make a choice they can't say why. Here, every ticket leaves a trail in the graph: which agent did what, in what order, what it read, and what it wrote. Memory survives restarts, and each customer's memory is its own graph.

## Demo

Customer `acme` sends the same kind of problem three times:

| Ticket | Source | Steps |
|---|---|---|
| wifi not working | trial and error | 5 |
| my wifi is down again | memory | 2 |
| wifi keeps disconnecting | memory | 2 |

Customer `zeta` then sends the same ticket and starts from zero (5 steps), because its memory is a different graph. In the web page you watch the agent walk the graph: on a new problem it tries five fixes and strikes out the dead ends, on a remembered one it goes straight down the saved path.

## How it maps to the brief

| What the track asks for | How it is done here | Status |
|---|---|---|
| Procedural memory | The steps that worked are saved as a chain of `Step` nodes linked by `NEXT`, and replayed on later tickets | Built |
| Episodic memory | Every ticket stays in the graph with its outcome and the trail of agents that handled it | Built |
| Semantic memory | The learner distills one `Fact` per topic from the path, with an evidence count (how many tickets back it) | Built, kept simple: one sentence per topic |
| Persistent history | Tickets and trails are stored in FalkorDB per tenant; restart the app and they are all still there | Built for ticket history. Not a multi-turn chat history |
| Shared state and handoffs between agents | Triage, resolver and learner share nothing except the graph. Each reads what the previous one wrote to the `Ticket` node and records its own work as a `WORKED_ON` edge with a sequence number | Built |
| High-concurrency reads and writes | Writes use `MERGE`, so simultaneous tickets cannot create duplicate paths. `load_test.py` sends tickets from many threads across several tenants and then checks the graphs for duplicates | Built, tested at small scale |
| Separate memory per user or tenant | One graph per tenant (`tenant_acme`, `tenant_zeta`) | Built |
| Memory updates over time | Each recall bumps a usage counter, and the fact's evidence count is refreshed after every ticket | Partial: paths get stronger, but a path is not yet retired when it stops working |
| Follow connections across data | Ticket to topic to steps | Partial: a shallow graph |
| Show the path behind an answer | The board shows the walk, and each ticket has an agent-by-agent trail you can open | Built |

## How the agents coordinate

```
ticket text
    |
 Triage agent      reads the text, finds the topic
    |              writes (:Ticket)-[:ABOUT]->(:Topic)
    v
 Resolver agent    reads the Topic's saved path from the graph
    |              replays it, or explores by trial and error
    |              writes the outcome onto the Ticket
    v
 Learner agent     reads the outcome from the graph
                   saves the winning path, refreshes the Fact, updates counters
```

Each agent logs its work as `(:Agent)-[:WORKED_ON {seq, note}]->(:Ticket)`, which is both the handoff record and the explanation shown in the page.

## Graph model

```
(:Topic {name, uses})-[:HAS_STEP]->(:Step {pos, action})
(:Step)-[:NEXT]->(:Step)
(:Ticket {text, status, source, steps_used, trail, worked})-[:ABOUT]->(:Topic)
(:Agent {name})-[:WORKED_ON {seq, note}]->(:Ticket)
(:Topic)-[:KNOWS]->(:Fact {text, evidence})
```

One graph per tenant, named `tenant_<name>`.

## Key Cypher queries

Read the saved path for a topic:

```cypher
MATCH (:Topic {name:$topic})-[:HAS_STEP]->(s:Step)
RETURN s.action ORDER BY s.pos
```

Save a path without ever creating duplicates:

```cypher
MATCH (t:Topic {name:$topic})
MERGE (t)-[:HAS_STEP]->(s:Step {pos:$pos})
SET s.action = $action
```

Triage creates the ticket and links it to its topic:

```cypher
MERGE (t:Topic {name:$topic})
CREATE (k:Ticket {text:$text, status:'triaged'})-[:ABOUT]->(t)
RETURN id(k)
```

Record a handoff:

```cypher
MERGE (a:Agent {name:$agent})
WITH a MATCH (k:Ticket) WHERE id(k) = $tid
CREATE (a)-[:WORKED_ON {seq:$seq, note:$note}]->(k)
```

The trail behind one answer:

```cypher
MATCH (a:Agent)-[w:WORKED_ON]->(k:Ticket) WHERE id(k) = $tid
RETURN a.name, w.seq, w.note ORDER BY w.seq
```

See a whole memory graph in FalkorDB Browser:

```cypher
MATCH (a)-[r]->(b) RETURN a, r, b
```

## Configuration

The app reads its FalkorDB connection from four environment variables, so no password ever lives in the repository:

| Variable | Meaning |
|---|---|
| `FALKORDB_HOST` | Host name of your FalkorDB instance |
| `FALKORDB_PORT` | Port number |
| `FALKORDB_USERNAME` | Username (usually `falkordb` on FalkorDB Cloud) |
| `FALKORDB_PASSWORD` | Password |

## Run it yourself

Requirements: Python 3.10+ and a FalkorDB instance (FalkorDB Cloud or local Docker).

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Set the four environment variables.

   Windows PowerShell:
   ```
   $env:FALKORDB_HOST="your-host"
   $env:FALKORDB_PORT="12345"
   $env:FALKORDB_USERNAME="falkordb"
   $env:FALKORDB_PASSWORD="your-password"
   ```
   macOS or Linux:
   ```
   export FALKORDB_HOST="your-host"
   export FALKORDB_PORT="12345"
   export FALKORDB_USERNAME="falkordb"
   export FALKORDB_PASSWORD="your-password"
   ```
3. Start the web demo and open http://127.0.0.1:5000:
   ```
   python app.py
   ```
4. Or run the terminal demo:
   ```
   python memory_agent.py
   ```
5. Check concurrent writes (uses its own throwaway tenants and deletes them; needs the same four variables):
   ```
   python load_test.py
   ```

## Deployment

The live demo runs as a Flask app served by Gunicorn on a Render Web Service connected to this GitHub repository.

- Render installs the dependencies from `requirements.txt` (which includes `gunicorn`).
- Start command:
  ```
  gunicorn app:app --bind 0.0.0.0:$PORT
  ```
- The four FalkorDB variables above are set in the Render service's environment settings, never in the code.

Why environment variables: the first deploy failed with `ModuleNotFoundError: No module named 'config'`. The code used to import a local `config.py` that holds the database password, which was deliberately never committed, so Render could not find it. Moving the connection details into environment variables fixed it and keeps the secrets out of the repository.

## Limitations

- **The solver is simulated.** Trying fixes by trial and error (`explore()` in `memory_agent.py`) follows a fixed list so the demo runs without any API key. It is one function and is meant to be replaced by a model call. The memory, coordination and graph work are real.
- **Optional model triage.** If you set `ANTHROPIC_API_KEY` (and `pip install anthropic`), the triage agent asks Claude to pick the topic. If the call fails or no key is set, it falls back to keyword matching.
- **Paths are not retired yet.** A saved path gets stronger each time it is used, but if a fix stops working the graph does not yet replace it.
- **Two first tickets at the same instant.** If two tickets for a brand new topic arrive simultaneously, both explore; their saves merge into the same path, so memory stays clean but the work is done twice.
- **The live demo is shared.** Everyone using it works on the same two demo customers, and the Clear memory button resets that customer's graph. If the page is slow to load the first time, the hosting service may be waking the app up.
- **Free-tier data is temporary.** FalkorDB Cloud's free instances are not backed up and are removed after inactivity, so treat the demo data as disposable.

## Files

- `memory_agent.py`: the three agents, the graph memory functions, the simulated solver
- `app.py`: Flask server and a small JSON API
- `index.html`: the demo page (a live graph board plus the agent trail)
- `load_test.py`: concurrency check
- `requirements.txt`: Python dependencies

## Built with

Python, Flask, Gunicorn, Render, and [FalkorDB](https://www.falkordb.com/) through the `falkordb` Python client.
