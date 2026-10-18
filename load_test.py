"""
Concurrency check: many tickets at once, across several tenants.

Run:  python load_test.py

It fires tickets from many threads, then checks the graphs for duplicate
nodes (which would show that simultaneous writes corrupted memory) and prints
throughput and latency. Uses its own throwaway tenants, then deletes them.
"""

import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import memory_agent as m

TENANTS = ["load_a", "load_b", "load_c", "load_d"]
TICKETS = ["wifi not working", "printer not printing", "forgot my password",
           "wifi is flaky today", "printer jammed again"]
TOTAL = 80
WORKERS = 16


def one(i):
    tenant = TENANTS[i % len(TENANTS)]
    text = TICKETS[i % len(TICKETS)]
    start = time.perf_counter()
    out = m.handle_ticket(tenant, text)
    return out["source"], time.perf_counter() - start


def duplicates(tenant):
    g = m.graph_for(tenant)
    steps = g.query("MATCH (t:Topic)-[:HAS_STEP]->(s:Step) "
                    "RETURN t.name, s.pos, count(s)").result_set
    topics = g.query("MATCH (t:Topic) RETURN t.name, count(t)").result_set
    facts = g.query("MATCH (t:Topic)-[:KNOWS]->(f:Fact) RETURN t.name, count(f)").result_set
    return sum(1 for r in steps if r[2] > 1) + sum(1 for r in topics if r[1] > 1) \
         + sum(1 for r in facts if r[1] > 1)


if __name__ == "__main__":
    for t in TENANTS:
        m.reset(t)

    print("Sending " + str(TOTAL) + " tickets from " + str(WORKERS) + " threads across "
          + str(len(TENANTS)) + " tenants...")
    begin = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, range(TOTAL)))
    elapsed = time.perf_counter() - begin

    lat = sorted(r[1] for r in results)
    from_memory = sum(1 for r in results if r[0] == "memory")
    dupes = sum(duplicates(t) for t in TENANTS)

    print("")
    print("tickets handled      : " + str(len(results)))
    print("total time           : " + format(elapsed, ".2f") + " s")
    print("throughput           : " + format(len(results) / elapsed, ".1f") + " tickets/s")
    print("latency median / p95 : " + format(statistics.median(lat) * 1000, ".0f") + " ms / "
          + format(lat[int(len(lat) * 0.95) - 1] * 1000, ".0f") + " ms")
    print("answered from memory : " + str(from_memory) + " of " + str(len(results)))
    print("duplicate nodes found: " + str(dupes) + "   (0 means concurrent writes stayed clean)")

    for t in TENANTS:
        m.reset(t)
