/*
 * Force-directed graph model for the mini viewer.
 *
 * Pure math, no DOM: the same UMD trick as harvest.js so the panel can use it
 * and the Node test suite can exercise the layout deterministically.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RRGraph = factory();
  }
})(typeof self !== "undefined" ? self : globalThis, function () {
  "use strict";

  const COLOURS = {
    DNS_NAME: "#58a6ff",
    IP_ADDRESS: "#79c0ff",
    URL: "#a5d6ff",
    URL_UNVERIFIED: "#6e7681",
    HTTP_RESPONSE: "#3fb950",
    TECHNOLOGY: "#d29922",
    EMAIL_ADDRESS: "#e3b341",
    FINDING: "#f85149",
    OPEN_TCP_PORT: "#ff7b72",
    DNS_NAME_UNRESOLVED: "#6e7681",
  };

  function hash(text) {
    let value = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
      value ^= text.charCodeAt(i);
      value = Math.imul(value, 16777619);
    }
    return value >>> 0;
  }

  function colourFor(type) {
    return COLOURS[type] || "#bc8cff";
  }

  /** Small deterministic PRNG so a given set of events always lays out the same. */
  function rng(seed) {
    let state = seed || 1;
    return function next() {
      state ^= state << 13;
      state ^= state >>> 17;
      state ^= state << 5;
      state >>>= 0;
      return state / 4294967296;
    };
  }

  function createGraph({ maxNodes = 600 } = {}) {
    const nodes = new Map();
    const edges = [];
    const random = rng(0x5eed);

    function addEvent(event) {
      if (nodes.size >= maxNodes && !nodes.has(event.id)) return false;
      if (nodes.has(event.id)) return false; // dedupe backfill vs live tail

      const angle = random() * Math.PI * 2;
      const radius = 60 + event.depth * 70 + random() * 40;
      const node = {
        id: event.id,
        type: event.type,
        data: event.data,
        module: event.module,
        depth: event.depth,
        pretty: event.pretty || {},
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        edges: 0,
      };
      nodes.set(event.id, node);

      if (event.parent_id && nodes.has(event.parent_id)) {
        edges.push({ source: event.parent_id, target: event.id });
        node.edges += 1;
        nodes.get(event.parent_id).edges += 1;
      }
      return true;
    }

    function addEvents(events) {
      let added = 0;
      for (const event of events || []) if (addEvent(event)) added += 1;
      return added;
    }

    function radius(node) {
      return Math.min(14, 4 + node.edges * 1.5 + (node.depth === 0 ? 4 : 0));
    }

    function clear() {
      nodes.clear();
      edges.length = 0;
    }

    /**
     * One relaxation pass. O(n^2) repulsion is fine at this scale (a few
     * hundred nodes) and keeps the panel dependency-free.
     */
    function step({ repulsion = 900, spring = 0.02, springLength = 90, damping = 0.82, centre = 0.012 } = {}) {
      const list = Array.from(nodes.values());

      for (let i = 0; i < list.length; i += 1) {
        const a = list[i];
        for (let j = i + 1; j < list.length; j += 1) {
          const b = list[j];
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let distance = Math.sqrt(dx * dx + dy * dy);
          if (distance < 0.01) {
            dx = (i - j) * 0.1;
            dy = 0.1;
            distance = 0.1;
          }
          const force = repulsion / (distance * distance);
          const fx = (dx / distance) * force;
          const fy = (dy / distance) * force;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }
      }

      for (const edge of edges) {
        const a = nodes.get(edge.source);
        const b = nodes.get(edge.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distance = Math.max(0.01, Math.sqrt(dx * dx + dy * dy));
        const force = spring * (distance - springLength);
        const fx = (dx / distance) * force;
        const fy = (dy / distance) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }

      for (const node of list) {
        node.vx -= node.x * centre;
        node.vy -= node.y * centre;
        node.vx *= damping;
        node.vy *= damping;
        node.x += node.vx;
        node.y += node.vy;
      }
    }

    function relax(iterations = 60, options) {
      for (let i = 0; i < iterations; i += 1) step(options);
    }

    function bounds() {
      if (!nodes.size) return { minX: -1, minY: -1, maxX: 1, maxY: 1 };
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const node of nodes.values()) {
        minX = Math.min(minX, node.x);
        minY = Math.min(minY, node.y);
        maxX = Math.max(maxX, node.x);
        maxY = Math.max(maxY, node.y);
      }
      return { minX, minY, maxX, maxY };
    }

    function project(width, height, padding = 24) {
      const { minX, minY, maxX, maxY } = bounds();
      const spanX = Math.max(1, maxX - minX);
      const spanY = Math.max(1, maxY - minY);
      const scale = Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY);
      const offsetX = padding + ((width - padding * 2) - spanX * scale) / 2;
      const offsetY = padding + ((height - padding * 2) - spanY * scale) / 2;
      for (const node of nodes.values()) {
        node.sx = (node.x - minX) * scale + offsetX;
        node.sy = (node.y - minY) * scale + offsetY;
        node.r = radius(node);
      }
      return scale;
    }

    function nodeAt(x, y) {
      let best = null;
      let bestDistance = Infinity;
      for (const node of nodes.values()) {
        const dx = node.sx - x;
        const dy = node.sy - y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance <= Math.max(6, node.r || 4) && distance < bestDistance) {
          best = node;
          bestDistance = distance;
        }
      }
      return best;
    }

    function counts() {
      const out = {};
      for (const node of nodes.values()) out[node.type] = (out[node.type] || 0) + 1;
      return out;
    }

    return { nodes, edges, addEvent, addEvents, step, relax, bounds, project, nodeAt, radius, clear, counts };
  }

  return { createGraph, colourFor, COLOURS, hash, rng };
});
