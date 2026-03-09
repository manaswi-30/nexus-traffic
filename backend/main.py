
import os, time, json, random, asyncio, math
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import numpy as np

app = FastAPI(title="NEXUS Traffic API v2.0", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PHASE_NAMES  = {0: "NS_GREEN", 1: "EW_GREEN", 2: "NS_LEFT", 3: "EW_LEFT"}
PHASE_COLORS = {"NS_GREEN": "#22c55e", "EW_GREEN": "#3b82f6", "NS_LEFT": "#f59e0b", "EW_LEFT": "#a855f7"}
GRID_SIZE    = 4
CO2_PER_VEHICLE_PER_MIN = 0.003

WEATHER_CONDITIONS = {
    "Clear":  {"timing_multiplier": 1.0, "speed_reduction": 0.0,  "icon": "☀️"},
    "Cloudy": {"timing_multiplier": 1.0, "speed_reduction": 0.05, "icon": "⛅"},
    "Rain":   {"timing_multiplier": 1.3, "speed_reduction": 0.20, "icon": "🌧️"},
    "Fog":    {"timing_multiplier": 1.5, "speed_reduction": 0.35, "icon": "🌫️"},
    "Storm":  {"timing_multiplier": 1.8, "speed_reduction": 0.50, "icon": "⛈️"},
}

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
    pedestrian_count: int = 0
    bus_waiting: bool = False
    incident_detected: bool = False
    incident_type: Optional[str] = None
    co2_saved_kg: float = 0.0
    vehicles_served: int = 0

class SensorUpdate(BaseModel):
    intersection_id: str
    lane_densities: List[float]
    lane_queues: List[float]
    emergency_detected: bool = False
    pedestrian_count: int = 0
    bus_detected: bool = False
    weather: Optional[str] = None

INTERSECTIONS: Dict[str, IntersectionState] = {}
for row in range(GRID_SIZE):
    for col in range(GRID_SIZE):
        iid = f"I{row}{col}"
        INTERSECTIONS[iid] = IntersectionState(
            id=iid, x=col*150+75, y=row*150+75,
            phase=random.choice(list(PHASE_NAMES.values())),
            queue_ns=random.uniform(0, 0.4),
            queue_ew=random.uniform(0, 0.4),
        )

WEATHER = {
    "condition": "Clear", "temperature": 28.0,
    "timing_multiplier": 1.0, "speed_reduction": 0.0,
    "icon": "☀️", "last_updated": time.time()
}

EMISSIONS = {
    "total_co2_saved_kg": 0.0, "total_vehicles_served": 0,
    "equivalent_trees": 0.0, "fuel_saved_liters": 0.0
}

EMERGENCY_STATE = {
    "active": False, "corridor": [], "vehicle_type": None,
    "detected_at": None, "direction": None,
}

INCIDENTS: List[dict] = []

COST_STATE = {
    "total_decisions": 0, "total_cost_usd": 0.0,
    "cost_per_inference_usd": 0.000001, "start_time": time.time(),
}

METRICS_HISTORY = []

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
            print(f"⚠️  Using rule-based fallback: {e}")
            _model = "fallback"
    return _model

def rl_action(queues, densities, phase, phase_duration,
              pedestrians=0, bus_waiting=False, weather_multiplier=1.0):
    model = get_model()
    min_green = int(10 * weather_multiplier)

    if bus_waiting:
        ns = queues[0] + queues[1] if len(queues) > 1 else 0
        ew = queues[2] + queues[3] if len(queues) > 3 else 0
        return 0 if ns >= ew else 1

    if pedestrians > 5 and phase_duration > 20:
        phase_idx = list(PHASE_NAMES.values()).index(phase) if phase in PHASE_NAMES.values() else 0
        return (phase_idx + 1) % 4

    if model == "fallback":
        ns = queues[0] + queues[1] if len(queues) > 1 else 0
        ew = queues[2] + queues[3] if len(queues) > 3 else 0
        if phase_duration < min_green:
            return list(PHASE_NAMES.values()).index(phase) if phase in PHASE_NAMES.values() else 0
        return 0 if ns >= ew else 1

    try:
        phase_idx = list(PHASE_NAMES.values()).index(phase) if phase in PHASE_NAMES.values() else 0
        obs = np.array([
            *queues[:4], *densities[:4],
            phase_idx / 3.0, min(phase_duration / 60.0, 1.0),
            min(pedestrians / 10.0, 1.0), 1.0 if bus_waiting else 0.0,
            weather_multiplier / 2.0, *([0.0] * 7)
        ], dtype=np.float32)[:20]
        action, _ = model.predict(obs, deterministic=True)
        return int(action)
    except:
        return 0

def detect_incident(state: IntersectionState):
    if state.queue_ns > 0.85 or state.queue_ew > 0.85:
        return {"id": f"INC_{state.id}_{int(time.time())}", "intersection_id": state.id,
                "type": "Severe Congestion", "severity": "HIGH",
                "timestamp": time.time(), "resolved": False}
    if state.phase == "NS_GREEN" and state.queue_ns > 0.7 and state.phase_duration > 45:
        return {"id": f"INC_{state.id}_{int(time.time())}", "intersection_id": state.id,
                "type": "Possible Vehicle Breakdown", "severity": "MEDIUM",
                "timestamp": time.time(), "resolved": False}
    return None

async def simulation_loop():
    phase_timers = {iid: 0 for iid in INTERSECTIONS}
    step = 0
    weather_step = 0

    while True:
        step += 1
        weather_step += 1
        w_mult = WEATHER["timing_multiplier"]

        if weather_step >= 300:
            weather_step = 0
            cond = random.choices(
                list(WEATHER_CONDITIONS.keys()), weights=[0.4, 0.25, 0.20, 0.10, 0.05])[0]
            WEATHER.update({
                "condition": cond, "icon": WEATHER_CONDITIONS[cond]["icon"],
                "timing_multiplier": WEATHER_CONDITIONS[cond]["timing_multiplier"],
                "speed_reduction": WEATHER_CONDITIONS[cond]["speed_reduction"],
                "temperature": round(random.uniform(18, 42), 1),
                "last_updated": time.time()
            })

        for iid, state in INTERSECTIONS.items():
            INTERSECTIONS[iid].pedestrian_count = random.randint(0, 12)
            INTERSECTIONS[iid].bus_waiting      = random.random() < 0.15

            speed_factor = 1.0 - WEATHER["speed_reduction"]
            arrival      = random.uniform(0.02, 0.15) * (1.0 + (1.0 - speed_factor) * 0.3)
            phase_timers[iid] += 1

            if phase_timers[iid] >= 5:
                queues    = [state.queue_ns, state.queue_ns*0.9, state.queue_ew, state.queue_ew*0.9]
                densities = [state.density_ns, state.density_ns*0.85, state.density_ew, state.density_ew*0.85]
                action    = rl_action(queues, densities, state.phase, phase_timers[iid],
                                      state.pedestrian_count, state.bus_waiting, w_mult)
                new_phase = PHASE_NAMES[action]
                if new_phase != state.phase:
                    phase_timers[iid] = 0
                INTERSECTIONS[iid].phase          = new_phase
                INTERSECTIONS[iid].phase_duration = phase_timers[iid]
                COST_STATE["total_decisions"]    += 1
                COST_STATE["total_cost_usd"]     += COST_STATE["cost_per_inference_usd"]

            phase   = INTERSECTIONS[iid].phase
            prev_ns = state.queue_ns
            prev_ew = state.queue_ew

            if "NS" in phase:
                INTERSECTIONS[iid].queue_ns = max(0, state.queue_ns - 0.08*speed_factor + arrival)
                INTERSECTIONS[iid].queue_ew = min(1, state.queue_ew + arrival * 0.7)
            else:
                INTERSECTIONS[iid].queue_ew = max(0, state.queue_ew - 0.08*speed_factor + arrival)
                INTERSECTIONS[iid].queue_ns = min(1, state.queue_ns + arrival * 0.7)

            INTERSECTIONS[iid].density_ns = min(1, state.queue_ns * 1.2)
            INTERSECTIONS[iid].density_ew = min(1, state.queue_ew * 1.2)
            INTERSECTIONS[iid].wait_time  = (state.queue_ns + state.queue_ew) * 60

            vehicles_in_q  = int((state.queue_ns + state.queue_ew) * 20)
            wait_reduction = max(0, (prev_ns + prev_ew - state.queue_ns - state.queue_ew) * 30)
            co2_saved      = vehicles_in_q * CO2_PER_VEHICLE_PER_MIN * (wait_reduction / 60.0)
            INTERSECTIONS[iid].co2_saved_kg   += co2_saved
            INTERSECTIONS[iid].vehicles_served += max(0, int((prev_ns - state.queue_ns + prev_ew - state.queue_ew) * 20))
            EMISSIONS["total_co2_saved_kg"]   += co2_saved
            EMISSIONS["total_vehicles_served"] = sum(s.vehicles_served for s in INTERSECTIONS.values())
            EMISSIONS["equivalent_trees"]      = round(EMISSIONS["total_co2_saved_kg"] / 21.77, 3)
            EMISSIONS["fuel_saved_liters"]     = round(EMISSIONS["total_co2_saved_kg"] / 2.31, 3)

            incident = detect_incident(INTERSECTIONS[iid])
            if incident:
                INTERSECTIONS[iid].incident_detected = True
                INTERSECTIONS[iid].incident_type     = incident["type"]
                recent = [i["intersection_id"] for i in INCIDENTS[-5:]] if INCIDENTS else []
                if iid not in recent:
                    INCIDENTS.append(incident)
                    if len(INCIDENTS) > 50:
                        INCIDENTS.pop(0)
            else:
                INTERSECTIONS[iid].incident_detected = False
                INTERSECTIONS[iid].incident_type     = None

        if step % 120 == 0 and not EMERGENCY_STATE["active"]:
            _trigger_emergency()
        if EMERGENCY_STATE["active"] and step % 30 == 0:
            _clear_emergency()

        if step % 10 == 0:
            avg_q = float(np.mean([s.queue_ns + s.queue_ew for s in INTERSECTIONS.values()]))
            METRICS_HISTORY.append({
                "time": time.time(), "avg_queue": round(avg_q, 3),
                "weather": WEATHER["condition"],
                "co2_saved": round(EMISSIONS["total_co2_saved_kg"], 4),
            })
            if len(METRICS_HISTORY) > 20:
                METRICS_HISTORY.pop(0)

        await asyncio.sleep(1)

def _trigger_emergency():
    vehicle = random.choice(["Ambulance 🚑", "Fire Truck 🚒", "Police Car 🚔"])
    start   = random.choice(list(INTERSECTIONS.keys())[:8])
    row, col = int(start[1]), int(start[2])
    corridor = ([f"I{row}{c}" for c in range(col, min(col+4, GRID_SIZE))]
                if random.random() > 0.5 else
                [f"I{r}{col}" for r in range(row, min(row+4, GRID_SIZE))])
    EMERGENCY_STATE.update({"active": True, "corridor": corridor,
                             "vehicle_type": vehicle, "detected_at": time.time()})
    for iid in corridor:
        if iid in INTERSECTIONS:
            INTERSECTIONS[iid].emergency = True
            INTERSECTIONS[iid].phase     = "NS_GREEN"

def _clear_emergency():
    for iid in EMERGENCY_STATE["corridor"]:
        if iid in INTERSECTIONS:
            INTERSECTIONS[iid].emergency = False
    EMERGENCY_STATE.update({"active": False, "corridor": []})

@app.on_event("startup")
async def startup():
    asyncio.create_task(simulation_loop())
    print("🚦 NEXUS v2.0 started — All features active")

@app.get("/")
def root():
    return {"status": "NEXUS v2.0", "features": [
        "multi_agent_rl", "weather_adaptive", "emissions_tracking",
        "pedestrian_detection", "bus_priority", "incident_detection", "emergency_preemption"
    ]}

@app.get("/intersections")
def get_intersections():
    return {"intersections": [i.dict() for i in INTERSECTIONS.values()], "phase_colors": PHASE_COLORS}

@app.get("/weather")
def get_weather():
    return WEATHER

@app.get("/emissions")
def get_emissions():
    return EMISSIONS

@app.get("/incidents")
def get_incidents():
    active = [i for i in INCIDENTS if not i["resolved"]]
    return {"incidents": active, "total": len(INCIDENTS), "active": len(active)}

@app.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str):
    for inc in INCIDENTS:
        if inc["id"] == incident_id:
            inc["resolved"] = True
            return {"status": "resolved"}
    raise HTTPException(404, "Incident not found")

@app.get("/emergency")
def get_emergency():
    return EMERGENCY_STATE

@app.post("/emergency/trigger")
def trigger_emergency():
    _trigger_emergency()
    return {"status": "triggered", "corridor": EMERGENCY_STATE["corridor"]}

@app.post("/emergency/clear")
def clear_emergency():
    _clear_emergency()
    return {"status": "cleared"}

@app.post("/sensor_update")
def sensor_update(data: SensorUpdate):
    iid = data.intersection_id
    if iid not in INTERSECTIONS:
        raise HTTPException(404, f"Intersection {iid} not found")
    w_mult = WEATHER["timing_multiplier"]
    action = rl_action(data.lane_densities, data.lane_queues,
                       INTERSECTIONS[iid].phase, INTERSECTIONS[iid].phase_duration,
                       data.pedestrian_count, data.bus_detected, w_mult)
    if data.weather and data.weather in WEATHER_CONDITIONS:
        WEATHER.update({"condition": data.weather,
                        **WEATHER_CONDITIONS[data.weather]})
    INTERSECTIONS[iid].phase            = PHASE_NAMES[action]
    INTERSECTIONS[iid].pedestrian_count = data.pedestrian_count
    INTERSECTIONS[iid].bus_waiting      = data.bus_detected
    COST_STATE["total_decisions"]       += 1
    COST_STATE["total_cost_usd"]        += COST_STATE["cost_per_inference_usd"]
    return {"action": action, "phase": PHASE_NAMES[action]}

@app.get("/metrics")
def get_metrics():
    states = list(INTERSECTIONS.values())
    return {
        "avg_queue_length":          round(float(np.mean([s.queue_ns + s.queue_ew for s in states])), 3),
        "avg_wait_time_seconds":     round(float(np.mean([s.wait_time for s in states])), 1),
        "active_intersections":      len(states),
        "emergency_active":          EMERGENCY_STATE["active"],
        "total_decisions":           COST_STATE["total_decisions"],
        "weather":                   WEATHER["condition"],
        "weather_timing_multiplier": WEATHER["timing_multiplier"],
        "active_incidents":          len([i for i in INCIDENTS if not i["resolved"]]),
        "buses_waiting":             sum(1 for s in states if s.bus_waiting),
        "total_pedestrians":         sum(s.pedestrian_count for s in states),
        "co2_saved_kg":              round(EMISSIONS["total_co2_saved_kg"], 4),
        "equivalent_trees":          round(EMISSIONS["total_co2_saved_kg"] / 21.77, 3),
    }

@app.get("/costs")
def get_costs():
    elapsed  = max((time.time() - COST_STATE["start_time"]) / 3600, 0.001)
    decisions = max(COST_STATE["total_decisions"], 1)
    return {
        "total_decisions":        decisions,
        "total_cost_usd":         round(COST_STATE["total_cost_usd"], 8),
        "cost_per_decision_usd":  round(COST_STATE["total_cost_usd"] / decisions, 8),
        "monthly_projection_usd": round((COST_STATE["total_cost_usd"] / elapsed) * 730, 4),
        "intersections_count":    len(INTERSECTIONS),
    }

@app.get("/metrics/history")
def get_metrics_history():
    return {"history": METRICS_HISTORY}

active_connections: List[WebSocket] = []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            states = list(INTERSECTIONS.values())
            await websocket.send_json({
                "intersections": [i.dict() for i in states],
                "emergency":     EMERGENCY_STATE,
                "weather":       WEATHER,
                "emissions":     EMISSIONS,
                "incidents":     [i for i in INCIDENTS if not i["resolved"]][-5:],
                "metrics": {
                    "avg_queue":         round(float(np.mean([s.queue_ns + s.queue_ew for s in states])), 3),
                    "total_decisions":   COST_STATE["total_decisions"],
                    "total_cost_usd":    round(COST_STATE["total_cost_usd"], 6),
                    "buses_waiting":     sum(1 for s in states if s.bus_waiting),
                    "total_pedestrians": sum(s.pedestrian_count for s in states),
                },
                "timestamp": time.time(),
            })
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        active_connections.remove(websocket)
































