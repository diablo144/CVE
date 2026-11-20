"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createGraph, colourFor, rng } = require("../src/graph.js");

function event(id, type, data, parent_id = null, depth = 0) {
  return { id, type, data, module: "test", depth, parent_id, pretty: {} };
}

test("graph deduplicates events and links children to known parents", () => {
  const graph = createGraph();
  assert.equal(graph.addEvent(event("root", "DNS_NAME", "acme.test")), true);
  assert.equal(graph.addEvent(event("root", "DNS_NAME", "acme.test")), false);
  assert.equal(graph.addEvent(event("child", "URL", "https://acme.test/", "root", 1)), true);
  assert.equal(graph.nodes.size, 2);
  assert.deepEqual(graph.edges, [{ source: "root", target: "child" }]);
  assert.equal(graph.nodes.get("root").edges, 1);
});

test("graph enforces node cap", () => {
  const graph = createGraph({ maxNodes: 2 });
  assert.equal(graph.addEvents([
    event("a", "DNS_NAME", "a.test"),
    event("b", "DNS_NAME", "b.test"),
    event("c", "DNS_NAME", "c.test"),
  ]), 2);
  assert.equal(graph.nodes.size, 2);
});

test("force layout and projection stay finite for overlapping nodes", () => {
  const graph = createGraph();
  graph.addEvents([
    event("a", "DNS_NAME", "a.test"),
    event("b", "DNS_NAME", "b.test", "a", 1),
    event("c", "URL", "https://a.test/x", "a", 1),
    event("d", "IP_ADDRESS", "192.0.2.1", "b", 2),
  ]);
  for (const node of graph.nodes.values()) {
    node.x = 0;
    node.y = 0;
  }
  graph.relax(80);
  graph.project(640, 480);
  for (const node of graph.nodes.values()) {
    for (const key of ["x", "y", "sx", "sy", "r"]) assert.ok(Number.isFinite(node[key]));
  }
  const first = graph.nodes.get("a");
  assert.equal(graph.nodeAt(first.sx, first.sy)?.id, "a");
  assert.deepEqual(graph.counts(), { DNS_NAME: 2, URL: 1, IP_ADDRESS: 1 });
});

test("colour fallback and seeded random are deterministic", () => {
  assert.match(colourFor("DNS_NAME"), /^#[0-9a-f]{6}$/i);
  assert.equal(colourFor("UNKNOWN"), "#bc8cff");
  const a = rng(42);
  const b = rng(42);
  assert.deepEqual([a(), a(), a()], [b(), b(), b()]);
});
