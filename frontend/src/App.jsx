import { useState, useEffect, useRef, useCallback } from "react";

// ─── CONFIG ───────────────────────────────────────────────────────────────────
const API_BASE = "http://localhost:8000";
const WS_URL   = "ws://localhost:8000/ws";

const PHASE_COLORS = {
  NS_GREEN: "#22c55e",
  EW_GREEN: "#3b82f6",
  NS_LEFT:  "#f59e0b",
  EW_LEFT:  "#a855f7",
};

const PHASE_LABELS = {
  NS_GREEN: "N↕S",
  EW_GREEN: "E↔W",
  NS_LEFT:  "N↙ Left",
  EW_LEFT:  "E↙ Left",
};

// ─── HOOKS ────────────────────────────────────────────────────────────────────
function useWebSocket(url) {
  const [data, setData]     = useState(null);
  const [status, setStatus] = useState("connecting");
  const wsRef = useRef(null);
  const reconnectRef = useRef(null);

  const connect = useCallback(() => {
    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen    = () => setStatus("connected");
      ws.onmessage = (e) => setData(JSON.parse(e.data));
      ws.onerror   = () => setStatus("error");
      ws.onclose   = () => {
        setStatus("disconnected");
        reconnectRef.current = setTimeout(connect, 3000);
      };
    } catch {
      setStatus("error");
      reconnectRef.current = setTimeout(connect, 3000);
    }
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { data, status };
}

// ─── SUB-COMPONENTS ───────────────────────────────────────────────────────────
function KPICard({ label, value, sub, color = "#f0c419", icon }) {
  return (
    <div style={{
      background: "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)",
      border: `1px solid ${color}33`,
      borderLeft: `3px solid ${color}`,
      borderRadius: 10, padding: "14px 18px", flex: 1, minWidth: 140,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ fontSize: 22, color, fontWeight: 800, letterSpacing: -1 }}>{value}</div>
        <span style={{ fontSize: 20 }}>{icon}</span>
      </div>
      <div style={{ color: "#e2e8f0", fontSize: 12, fontWeight: 600, marginTop: 2 }}>{label}</div>
      {sub && <div style={{ color: "#64748b", fontSize: 11, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function TrafficLight({ phase, size = 16 }) {
  const isNS = phase?.includes("NS") || phase === "NS_GREEN";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "center" }}>
      {["red", "yellow", "green"].map((c, i) => {
        const active = (i === 2 && isNS) || (i === 0 && !isNS);
        return (
          <div key={c} style={{
            width: size, height: size, borderRadius: "50%",
            background: active
              ? (c === "green" ? "#22c55e" : "#ef4444")
              : "#1e293b",
            boxShadow: active ? `0 0 ${size/2}px ${c === "green" ? "#22c55e" : "#ef4444"}` : "none",
            transition: "all 0.4s",
          }} />
        );
      })}
    </div>
  );
}

function IntersectionNode({ state, isCorridorActive, gridX, gridY }) {
  const color = PHASE_COLORS[state.phase] || "#22c55e";
  const queueLevel = (state.queue_ns + state.queue_ew) / 2;
  const isHighLoad = queueLevel > 0.6;

  return (
    <div style={{
      position: "absolute",
      left: gridX - 34, top: gridY - 34,
      width: 68, height: 68,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      cursor: "pointer",
    }}>
      {/* Pulsing ring for emergency */}
      {state.emergency && (
        <div style={{
          position: "absolute", inset: -8,
          borderRadius: "50%", border: "3px solid #ef4444",
          animation: "pulse 0.8s infinite",
          opacity: 0.8,
        }} />
      )}

      {/* Heatmap background */}
      <div style={{
        position: "absolute", inset: 0, borderRadius: 10,
        background: isCorridorActive
          ? "#ef444422"
          : `rgba(${Math.round(255 * queueLevel)}, ${Math.round(255 * (1 - queueLevel))}, 0, 0.15)`,
        border: `2px solid ${state.emergency ? "#ef4444" : color}`,
        transition: "all 0.5s",
      }} />

      {/* Phase indicator circle */}
      <div style={{
        width: 28, height: 28, borderRadius: "50%",
        background: color,
        boxShadow: `0 0 12px ${color}88`,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 10, fontWeight: 800, color: "#000",
        zIndex: 1, transition: "all 0.4s",
      }}>
        {PHASE_LABELS[state.phase]?.split(" ")[0] || "●"}
      </div>

      {/* ID label */}
      <div style={{ color: "#94a3b8", fontSize: 9, marginTop: 2, zIndex: 1 }}>{state.id}</div>

      {/* Queue bar */}
      <div style={{
        position: "absolute", bottom: 4, left: 8, right: 8,
        height: 3, background: "#1e293b", borderRadius: 2, overflow: "hidden",
      }}>
        <div style={{
          height: "100%", width: `${queueLevel * 100}%`,
          background: isHighLoad ? "#ef4444" : "#22c55e",
          transition: "width 0.5s",
        }} />
      </div>
    </div>
  );
}

function RoadGrid({ intersections, emergency }) {
  const CELL = 150;
  const PAD  = 80;
  const GRID = 4;
  const SIZE = GRID * CELL + PAD * 2;

  const corridorSet = new Set(emergency?.corridor || []);

  return (
    <div style={{
      position: "relative",
      width: SIZE, height: SIZE,
      background: "#0a0f1e",
      borderRadius: 12, overflow: "hidden",
      border: "1px solid #1e293b",
      flexShrink: 0,
    }}>
      {/* Road grid lines */}
      <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
        {Array.from({ length: GRID }).map((_, i) => (
          <g key={i}>
            {/* Horizontal road */}
            <rect
              x={PAD / 2} y={PAD + i * CELL - 6}
              width={SIZE - PAD} height={12}
              fill={emergency?.active && emergency?.corridor?.some(id => parseInt(id[1]) === i)
                ? "#ef444411" : "#1e293b"}
            />
            {/* Dashed center line */}
            <line
              x1={PAD / 2} y1={PAD + i * CELL}
              x2={SIZE - PAD / 2} y2={PAD + i * CELL}
              stroke="#374151" strokeWidth={1} strokeDasharray="12,8"
            />
            {/* Vertical road */}
            <rect
              x={PAD + i * CELL - 6} y={PAD / 2}
              width={12} height={SIZE - PAD}
              fill={emergency?.active && emergency?.corridor?.some(id => parseInt(id[2]) === i)
                ? "#ef444411" : "#1e293b"}
            />
            <line
              x1={PAD + i * CELL} y1={PAD / 2}
              x2={PAD + i * CELL} y2={SIZE - PAD / 2}
              stroke="#374151" strokeWidth={1} strokeDasharray="12,8"
            />
          </g>
        ))}

        {/* Emergency corridor highlight */}
        {emergency?.active && emergency.corridor?.length > 1 && (() => {
          const ids = emergency.corridor;
          const first = ids[0], last = ids[ids.length - 1];
          const r0 = parseInt(first[1]), c0 = parseInt(first[2]);
          const r1 = parseInt(last[1]),  c1 = parseInt(last[2]);
          return (
            <rect
              x={PAD + Math.min(c0, c1) * CELL - 10}
              y={PAD + Math.min(r0, r1) * CELL - 10}
              width={(Math.abs(c1 - c0) * CELL) + 20}
              height={(Math.abs(r1 - r0) * CELL) + 20}
              fill="none"
              stroke="#ef4444"
              strokeWidth={2}
              strokeDasharray="8,4"
              rx={8}
              opacity={0.6}
            />
          );
        })()}
      </svg>

      {/* Intersection nodes */}
      {intersections.map(state => {
        const row = parseInt(state.id[1]);
        const col = parseInt(state.id[2]);
        return (
          <IntersectionNode
            key={state.id}
            state={state}
            isCorridorActive={corridorSet.has(state.id)}
            gridX={PAD + col * CELL}
            gridY={PAD + row * CELL}
          />
        );
      })}

      {/* Emergency label */}
      {emergency?.active && (
        <div style={{
          position: "absolute", top: 10, left: 10, right: 10,
          background: "#ef444422", border: "1px solid #ef4444",
          borderRadius: 8, padding: "6px 12px",
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <span style={{ fontSize: 16 }}>🚨</span>
          <div>
            <div style={{ color: "#ef4444", fontWeight: 700, fontSize: 12 }}>
              EMERGENCY CORRIDOR ACTIVE
            </div>
            <div style={{ color: "#fca5a5", fontSize: 11 }}>
              {emergency.vehicle_type} — {emergency.corridor?.join(" → ")}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CostProjectionTable() {
  const rows = [
    [10,     0.00,    "0.000000", "Free Tier"],
    [100,    0.08,    "0.000026", "Near-Free"],
    [1000,   185,     "0.006169", "Cost-Efficient"],
    [10000,  1720,    "0.005733", "Economies of Scale ↓"],
  ];
  return (
    <div style={{ background: "#0f172a", borderRadius: 10, padding: 16, border: "1px solid #1e293b" }}>
      <h3 style={{ color: "#f0c419", margin: "0 0 12px", fontSize: 13, fontWeight: 700, textTransform: "uppercase" }}>
        💰 Cost at Scale vs Competitors
      </h3>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr>
            {["Intersections", "Monthly (USD)", "Per Node/Day", "Status"].map(h => (
              <th key={h} style={{ color: "#64748b", padding: "4px 8px", textAlign: "left", fontWeight: 600 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([n, m, p, s]) => (
            <tr key={n} style={{ borderTop: "1px solid #1e293b" }}>
              <td style={{ padding: "6px 8px", color: "#e2e8f0", fontWeight: 600 }}>{n.toLocaleString()}</td>
              <td style={{ padding: "6px 8px", color: "#4ade80", fontWeight: 700 }}>${m}</td>
              <td style={{ padding: "6px 8px", color: "#94a3b8" }}>${p}</td>
              <td style={{ padding: "6px 8px" }}>
                <span style={{
                  background: "#22c55e22", color: "#4ade80",
                  border: "1px solid #22c55e44", borderRadius: 4,
                  padding: "1px 6px", fontSize: 10, fontWeight: 600,
                }}>{s}</span>
              </td>
            </tr>
          ))}
          <tr style={{ borderTop: "2px solid #ef444433" }}>
            <td style={{ padding: "6px 8px", color: "#ef4444" }}>SCATS (Competitor)</td>
            <td style={{ padding: "6px 8px", color: "#ef4444", fontWeight: 700 }}>₹66,000+</td>
            <td style={{ padding: "6px 8px", color: "#ef4444" }}>₹2,191/day</td>
            <td style={{ padding: "6px 8px" }}>
              <span style={{ background: "#ef444422", color: "#ef4444", border: "1px solid #ef444444", borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 600 }}>99% More Expensive</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function QueueChart({ intersections }) {
  if (!intersections?.length) return null;
  const sample = intersections.slice(0, 8);
  const maxQ = 1.0;
  return (
    <div style={{ background: "#0f172a", borderRadius: 10, padding: 16, border: "1px solid #1e293b" }}>
      <h3 style={{ color: "#22d3ee", margin: "0 0 12px", fontSize: 13, fontWeight: 700, textTransform: "uppercase" }}>
        📊 Live Queue Levels
      </h3>
      <div style={{ display: "flex", gap: 6, alignItems: "flex-end", height: 80 }}>
        {sample.map(s => {
          const h = Math.round((s.queue_ns + s.queue_ew) / 2 * 70);
          const high = (s.queue_ns + s.queue_ew) / 2 > 0.6;
          return (
            <div key={s.id} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
              <div style={{
                width: "100%", height: Math.max(h, 4),
                background: high ? "#ef4444" : "#22c55e",
                borderRadius: "3px 3px 0 0",
                boxShadow: `0 0 6px ${high ? "#ef4444" : "#22c55e"}66`,
                transition: "height 0.5s",
              }} />
              <div style={{ color: "#64748b", fontSize: 9 }}>{s.id}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── MAIN APP ─────────────────────────────────────────────────────────────────
export default function App() {
  const { data, status } = useWebSocket(WS_URL);
  const [metrics, setMetrics]       = useState(null);
  const [costs, setCosts]           = useState(null);
  const [decisionLog, setLog]       = useState([]);

  // Poll REST endpoints
  useEffect(() => {
    const fetchExtra = async () => {
      try {
        const [mRes, cRes] = await Promise.all([
          fetch(`${API_BASE}/metrics`),
          fetch(`${API_BASE}/costs`),
        ]);
        if (mRes.ok) setMetrics(await mRes.json());
        if (cRes.ok) setCosts(await cRes.json());
      } catch { /* backend not connected */ }
    };
    fetchExtra();
    const id = setInterval(fetchExtra, 3000);
    return () => clearInterval(id);
  }, []);

  // Decision log
  useEffect(() => {
    if (!data) return;
    if (data.emergency?.active && data.emergency?.corridor?.length) {
      const entry = {
        time: new Date().toLocaleTimeString(),
        msg: `🚨 ${data.emergency.vehicle_type} — corridor cleared: ${data.emergency.corridor.join("→")}`,
        type: "emergency",
      };
      setLog(prev => [entry, ...prev].slice(0, 20));
    }
  }, [data?.emergency?.active]);

  const intersections = data?.intersections || [];
  const emergency     = data?.emergency || {};
  const liveMetrics   = data?.metrics || {};

  const avgQueue   = (liveMetrics.avg_queue * 100 || 0).toFixed(1);
  const decisions  = (liveMetrics.total_decisions || 0).toLocaleString();
  const cost       = (liveMetrics.total_cost_usd || 0).toFixed(6);

  const triggerEmergency = async () => {
    try { await fetch(`${API_BASE}/emergency/trigger`, { method: "POST" }); }
    catch { /* offline */ }
  };

  return (
    <div style={{
      background: "#020817", minHeight: "100vh", color: "#e2e8f0",
      fontFamily: "'Segoe UI', system-ui, sans-serif", padding: 20,
    }}>
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(1.3); }
        }
        @keyframes blink {
          0%, 100% { opacity: 1; } 50% { opacity: 0.3; }
        }
        * { box-sizing: border-box; }
      `}</style>

      {/* ── Header ── */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginBottom: 20, background: "linear-gradient(90deg, #0f172a, #1a0533)",
        borderRadius: 12, padding: "14px 20px", border: "1px solid #1e293b",
      }}>
        <div>
          <div style={{ color: "#f0c419", fontSize: 10, letterSpacing: "0.15em", fontWeight: 700, textTransform: "uppercase" }}>
            DAKSH AI HACKATHON 2026 — CYBER-PHYSICAL SYSTEMS
          </div>
          <h1 style={{ margin: "2px 0 0", fontSize: 22, fontWeight: 800, color: "#fff", letterSpacing: -0.5 }}>
            🚦 NEXUS — Intelligent Traffic Orchestrator
          </h1>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button
            onClick={triggerEmergency}
            style={{
              background: "#ef444422", color: "#ef4444",
              border: "1px solid #ef444466", borderRadius: 8,
              padding: "8px 16px", cursor: "pointer",
              fontSize: 13, fontWeight: 700,
              animation: emergency.active ? "blink 1s infinite" : "none",
            }}
          >
            🚨 {emergency.active ? "EMERGENCY ACTIVE" : "Trigger Emergency"}
          </button>
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            background: status === "connected" ? "#22c55e22" : "#ef444422",
            border: `1px solid ${status === "connected" ? "#22c55e55" : "#ef444455"}`,
            borderRadius: 20, padding: "4px 12px",
          }}>
            <div style={{
              width: 8, height: 8, borderRadius: "50%",
              background: status === "connected" ? "#22c55e" : "#ef4444",
              boxShadow: status === "connected" ? "0 0 6px #22c55e" : "none",
            }} />
            <span style={{ fontSize: 12, fontWeight: 600,
              color: status === "connected" ? "#22c55e" : "#ef4444" }}>
              {status === "connected" ? "LIVE" : status.toUpperCase()}
            </span>
          </div>
        </div>
      </div>

      {/* ── KPI Row ── */}
      <div style={{ display: "flex", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
        <KPICard label="Active Intersections" value={intersections.length || 16} icon="🗺️" color="#22d3ee" sub="4×4 grid" />
        <KPICard label="Avg Queue Level" value={`${avgQueue}%`} icon="🚗" color="#f0c419" sub="↓ vs fixed-time baseline" />
        <KPICard label="AI Decisions Made" value={decisions} icon="🧠" color="#4ade80" sub="RL agent actions" />
        <KPICard label="Total Cost (USD)" value={`$${cost}`} icon="💰" color="#a78bfa" sub="~$0 on free tier" />
        <KPICard label="Emergency Status" value={emergency.active ? "ACTIVE 🚨" : "Clear ✅"} icon="🚑"
          color={emergency.active ? "#ef4444" : "#22c55e"}
          sub={emergency.active ? emergency.vehicle_type : "All signals normal"} />
      </div>

      {/* ── Main Grid ── */}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {/* Road Grid */}
        <div style={{ flexShrink: 0 }}>
          <RoadGrid intersections={intersections} emergency={emergency} />
          {/* Legend */}
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            {Object.entries(PHASE_COLORS).map(([phase, color]) => (
              <div key={phase} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <div style={{ width: 10, height: 10, borderRadius: 2, background: color }} />
                <span style={{ color: "#64748b", fontSize: 11 }}>{PHASE_LABELS[phase]}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Right Panel */}
        <div style={{ flex: 1, minWidth: 300, display: "flex", flexDirection: "column", gap: 14 }}>
          <QueueChart intersections={intersections} />
          <CostProjectionTable />

          {/* Architecture summary */}
          <div style={{
            background: "#0f172a", borderRadius: 10, padding: 16,
            border: "1px solid #1e293b", flex: 1,
          }}>
            <h3 style={{ color: "#a78bfa", margin: "0 0 10px", fontSize: 13, fontWeight: 700, textTransform: "uppercase" }}>
              🧠 System Architecture
            </h3>
            {[
              ["👁️ Sense", "YOLOv8 counts vehicles per lane in real-time", "#22d3ee"],
              ["🧠 Think", "DQN agent decides optimal signal phase per intersection", "#4ade80"],
              ["📡 Coordinate", "Agents share queue state with 4 neighbors", "#f0c419"],
              ["🚦 Act", "Phase commands sent to physical signals via API", "#f97316"],
              ["🚨 Emergency", "Detected vehicle → full corridor pre-emptied in <1s", "#ef4444"],
              ["🛡️ Fail-safe", "Agent crash → auto-revert to fixed-time mode", "#94a3b8"],
            ].map(([t, d, c]) => (
              <div key={t} style={{ display: "flex", gap: 10, marginBottom: 8 }}>
                <div style={{ color: c, fontWeight: 700, fontSize: 12, minWidth: 100 }}>{t}</div>
                <div style={{ color: "#64748b", fontSize: 12 }}>{d}</div>
              </div>
            ))}
          </div>

          {/* Event log */}
          {decisionLog.length > 0 && (
            <div style={{
              background: "#0f172a", borderRadius: 10, padding: 14,
              border: "1px solid #ef444433", maxHeight: 140, overflowY: "auto",
            }}>
              <h3 style={{ color: "#ef4444", margin: "0 0 8px", fontSize: 12, fontWeight: 700 }}>📋 EVENT LOG</h3>
              {decisionLog.map((e, i) => (
                <div key={i} style={{ color: "#fca5a5", fontSize: 11, marginBottom: 4 }}>
                  <span style={{ color: "#64748b" }}>{e.time}</span> — {e.msg}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div style={{ textAlign: "center", marginTop: 20, color: "#334155", fontSize: 11 }}>
        NEXUS Traffic Orchestrator · Multi-Agent RL · Built for DAKSH AI Hackathon 2026 · TCS Foundation × SASTRA
      </div>
    </div>
  );
}
