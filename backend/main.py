"""
NEXUS Traffic - FastAPI Backend
Handles real-time state, RL decisions, emergency pre-emption, cost tracking.
Usage: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import os, time, json, random, asyncio
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import numpy as np

# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────
app = FastAPI(title="NEXUS Traffic API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# MODELS + STATE
# ─────────────────────────────────────────────
class IntersectionState(BaseModel):
    id: str
    x: float
    y: float
    phase: str = "NS_GREEN"
    queue_ns: float = 0.0
    queue_ew: float = 0.0
    density_ns: float = 0.0
    density_ew: float = 0.0
    wait_time: float = 0.0
    emergency: bool = False
    phase_duration: int = 0

class SensorUpdate(BaseModel):
    intersection_id: str
    lane_densities: List[float]  # [N, S, E, W]
    lane_queues: List[float]     # [N, S, E, W]
    emergency_detected: bool = False
    emergency_direction: Optional[int] = None  # 0=N,1=S,2=E,3=W

class CostMetrics(BaseModel):
    total_decisions: int
    total_cost_usd: float
    cost_per_decision_usd: float
    monthly_projection_usd: float
    intersections_count: int

PHASE_NAMES = {0: "NS_GREEN", 1: "EW_GREEN", 2: "NS_LEFT", 3: "EW_LEFT"}
PHASE_COLORS = {
    "NS_GREEN": "#22c55e",
    "EW_GREEN": "#3b82f6",
    "NS_LEFT": "#f59e0b",
    "EW_LEFT": "#a855f7"
}

# Grid of 16 intersections (4x4)
GRID_SIZE = 4
INTERSECTIONS: Dict[str, IntersectionState] = {}
for row in range(GRID_SIZE):
    for col in range(GRID_SIZE):
        iid = f"I{row}{col}"
        INTERSECTIONS[iid] = IntersectionState(
            id=iid,
            x=col * 150 + 75,
            y=row * 150 + 75,
            phase=random.choice(list(PHASE_NAMES.values())),
            queue_ns=random.uniform(0, 0.4),
            queue_ew=random.uniform(0, 0.4),
        )

# Cost tracking
COST_STATE = {
    "total_decisions": 0,
    "total_cost_usd": 0.0,
    "cost_per_inference_usd": 0.000001,
    "start_time": time.time(),
}

EMERGENCY_STATE = {
    "active": False,
    "corridor": [],
    "vehicle_type": None,
    "detected_at": None,
    "direction": None,
}

# RL model (lazy load)
_model = None

def get_model():
    global _model
    if _model is None:
        try:
            from stable_baselines3 import DQN
            model_path = os.path.join(os.path.dirname(__file__), "../models/nexus_agent")
            _model = DQN.load(model_path)
            print("✅ RL model loaded")
        except Exception as e:
            print(f"⚠️  RL model not found, using rule-based fallback: {e}")
            _model = "fallback"
    return _model


def rule_based_action(queues: List[float]) -> int:
    """Fallback: serve highest-queue direction."""
    ns_total = queues[0] + queues[1] if len(queues) > 1 else 0
    ew_total = queues[2] + queues[3] if len(queues) > 3 else 0
    return 0 if ns_total >= ew_total else 1


def rl_action(densities: List[float], queues: List[float],
              phase: str, phase_duration: int) -> int:
    model = get_model()
    if model == "fallback":
        return rule_based_action(queues)
    try:
        phase_idx = list(PHASE_NAMES.values()).index(phase) if phase in PHASE_NAMES.values() else 0
        obs = np.array([
            *queues[:4],
            *densities[:4],
            phase_idx / 3.0,
            min(phase_duration / 60.0, 1.0)
        ], dtype=np.float32)
        # Pad/trim to 20 dims (multi-agent obs)
        if len(obs) < 20:
            obs = np.pad(obs, (0, 20 - len(obs)))
        action, _ = model.predict(obs, deterministic=True)
        return int(action)
    except Exception:
        return rule_based_action(queues)


# ─────────────────────────────────────────────
# SIMULATION LOOP (runs in background)
# ─────────────────────────────────────────────
async def simulation_loop():
    """Continuously update intersection states with simulated traffic."""
    phase_timers: Dict[str, int] = {iid: 0 for iid in INTERSECTIONS}
    step = 0
    while True:
        step += 1
        for iid, state in INTERSECTIONS.items():
            # Simulate arrivals
            arrival = random.uniform(0.02, 0.12)
            phase_timers[iid] += 1

            # RL or rule-based decision every 5 ticks
            if phase_timers[iid] >= 5:
                queues = [state.queue_ns, state.queue_ns * 0.9,
                          state.queue_ew, state.queue_ew * 0.9]
                densities = [state.density_ns, state.density_ns * 0.85,
                             state.density_ew, state.density_ew * 0.85]
                action = rl_action(densities, queues, state.phase, phase_timers[iid])
                new_phase = PHASE_NAMES[action]

                if new_phase != state.phase:
                    phase_timers[iid] = 0
                INTERSECTIONS[iid].phase = new_phase
                INTERSECTIONS[iid].phase_duration = phase_timers[iid]
                COST_STATE["total_decisions"] += 1
                COST_STATE["total_cost_usd"] += COST_STATE["cost_per_inference_usd"]

            # Update queues based on phase
            phase = INTERSECTIONS[iid].phase
            if "NS" in phase:
                INTERSECTIONS[iid].queue_ns = max(0, state.queue_ns - 0.08 + arrival)
                INTERSECTIONS[iid].queue_ew = min(1, state.queue_ew + arrival * 0.7)
            else:
                INTERSECTIONS[iid].queue_ew = max(0, state.queue_ew - 0.08 + arrival)
                INTERSECTIONS[iid].queue_ns = min(1, state.queue_ns + arrival * 0.7)

            INTERSECTIONS[iid].density_ns = min(1, state.queue_ns * 1.2)
            INTERSECTIONS[iid].density_ew = min(1, state.queue_ew * 1.2)
            INTERSECTIONS[iid].wait_time = (state.queue_ns + state.queue_ew) * 60

        # Random emergency every ~120 steps
        if step % 120 == 0 and not EMERGENCY_STATE["active"]:
            _trigger_emergency()

        # Clear emergency after 30 steps
        if EMERGENCY_STATE["active"] and step % 30 == 0:
            _clear_emergency()

        await asyncio.sleep(1)  # 1 second per tick


def _trigger_emergency():
    vehicles = ["Ambulance 🚑", "Fire Truck 🚒", "Police Car 🚔"]
    vehicle = random.choice(vehicles)
    # Pick a random intersection as starting point
    start_ids = list(INTERSECTIONS.keys())
    start = random.choice(start_ids[:8])  # start from first 8
    row, col = int(start[1]), int(start[2])

    # Build corridor (horizontal or vertical, 4 intersections)
    if random.random() > 0.5:
        corridor = [f"I{row}{c}" for c in range(col, min(col + 4, GRID_SIZE))]
    else:
        corridor = [f"I{r}{col}" for r in range(row, min(row + 4, GRID_SIZE))]

    EMERGENCY_STATE.update({
        "active": True,
        "corridor": corridor,
        "vehicle_type": vehicle,
        "detected_at": time.time(),
        "direction": "horizontal" if len(corridor) > 1 and corridor[0][1] == corridor[1][1] else "vertical",
    })

    # Override phases to clear corridor
    for iid in corridor:
        if iid in INTERSECTIONS:
            INTERSECTIONS[iid].emergency = True
            INTERSECTIONS[iid].phase = "NS_GREEN" if "horizontal" in EMERGENCY_STATE["direction"] else "EW_GREEN"

    print(f"🚨 EMERGENCY: {vehicle} corridor {' → '.join(corridor)}")


def _clear_emergency():
    for iid in EMERGENCY_STATE["corridor"]:
        if iid in INTERSECTIONS:
            INTERSECTIONS[iid].emergency = False
    EMERGENCY_STATE["active"] = False
    EMERGENCY_STATE["corridor"] = []
    print("✅ Emergency corridor cleared")


# ─────────────────────────────────────────────
# REST ENDPOINTS
# ─────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    asyncio.create_task(simulation_loop())
    print("🚦 NEXUS Traffic API started. Simulation running.")


@app.get("/")
def root():
    return {"status": "NEXUS Traffic API running", "intersections": len(INTERSECTIONS)}


@app.get("/intersections")
def get_intersections():
    return {
        "intersections": [i.dict() for i in INTERSECTIONS.values()],
        "phase_colors": PHASE_COLORS,
    }


@app.get("/emergency")
def get_emergency():
    return EMERGENCY_STATE


@app.post("/emergency/trigger")
def trigger_emergency():
    """Manually trigger for demo purposes."""
    _trigger_emergency()
    return {"status": "triggered", "corridor": EMERGENCY_STATE["corridor"]}


@app.post("/emergency/clear")
def clear_emergency():
    _clear_emergency()
    return {"status": "cleared"}


@app.post("/sensor_update")
def sensor_update(data: SensorUpdate):
    """Receive real sensor data and update state."""
    iid = data.intersection_id
    if iid not in INTERSECTIONS:
        raise HTTPException(404, f"Intersection {iid} not found")

    action = rl_action(data.lane_densities, data.lane_queues,
                       INTERSECTIONS[iid].phase, INTERSECTIONS[iid].phase_duration)
    INTERSECTIONS[iid].phase = PHASE_NAMES[action]
    COST_STATE["total_decisions"] += 1
    COST_STATE["total_cost_usd"] += COST_STATE["cost_per_inference_usd"]

    return {"action": action, "phase": PHASE_NAMES[action]}


@app.get("/costs")
def get_costs():
    elapsed_hours = (time.time() - COST_STATE["start_time"]) / 3600
    decisions = max(COST_STATE["total_decisions"], 1)
    monthly = (COST_STATE["total_cost_usd"] / max(elapsed_hours, 0.001)) * 730
    return CostMetrics(
        total_decisions=decisions,
        total_cost_usd=round(COST_STATE["total_cost_usd"], 8),
        cost_per_decision_usd=round(COST_STATE["total_cost_usd"] / decisions, 8),
        monthly_projection_usd=round(monthly, 4),
        intersections_count=len(INTERSECTIONS),
    )


@app.get("/metrics")
def get_metrics():
    states = list(INTERSECTIONS.values())
    avg_queue = np.mean([s.queue_ns + s.queue_ew for s in states])
    avg_wait = np.mean([s.wait_time for s in states])
    throughput = sum(
        (1 - s.queue_ns) * 20 + (1 - s.queue_ew) * 20 for s in states
    ) / len(states)
    return {
        "avg_queue_length": round(float(avg_queue), 3),
        "avg_wait_time_seconds": round(float(avg_wait), 1),
        "throughput_vehicles_per_hour": round(throughput, 1),
        "active_intersections": len(states),
        "emergency_active": EMERGENCY_STATE["active"],
        "total_decisions": COST_STATE["total_decisions"],
    }


# ─────────────────────────────────────────────
# WEBSOCKET — streams live state to dashboard
# ─────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            payload = {
                "intersections": [i.dict() for i in INTERSECTIONS.values()],
                "emergency": EMERGENCY_STATE,
                "metrics": {
                    "avg_queue": round(float(np.mean([s.queue_ns + s.queue_ew for s in INTERSECTIONS.values()])), 3),
                    "total_decisions": COST_STATE["total_decisions"],
                    "total_cost_usd": round(COST_STATE["total_cost_usd"], 6),
                },
                "timestamp": time.time(),
            }
            await websocket.send_json(payload)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
