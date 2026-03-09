"""
NEXUS Traffic - RL Agent Training
Run this FIRST on Google Colab (free GPU) or locally.
Usage: python train_agent.py
"""

import os
import json
import numpy as np
import gymnasium as gym
from gymnasium import spaces


# ─────────────────────────────────────────────
# CUSTOM TRAFFIC ENVIRONMENT (no SUMO needed for demo)
# Works standalone for the hackathon demo
# ─────────────────────────────────────────────
class TrafficIntersectionEnv(gym.Env):
    """
    Simulates a 4-way intersection with 4 signal phases.
    State:  [lane_0_queue, lane_1_queue, lane_2_queue, lane_3_queue,
             lane_0_density, lane_1_density, lane_2_density, lane_3_density,
             current_phase, phase_duration]
    Action: 0=NS_GREEN, 1=EW_GREEN, 2=NS_LEFT, 3=EW_LEFT
    Reward: -(total_waiting_time) + emergency_bonus
    """
    metadata = {"render_modes": ["human"]}

    PHASE_NAMES = {0: "NS_GREEN", 1: "EW_GREEN", 2: "NS_LEFT", 3: "EW_LEFT"}
    MIN_GREEN = 10   # seconds
    MAX_GREEN = 60   # seconds

    def __init__(self, emergency_prob=0.02):
        super().__init__()
        self.emergency_prob = emergency_prob
        self.dt = 5  # seconds per step

        self.observation_space = spaces.Box(
            low=0.0, high=1.0, shape=(10,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(4)
        self.reset()

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.queues = np.random.uniform(0, 0.5, 4).astype(np.float32)
        self.densities = np.random.uniform(0, 0.5, 4).astype(np.float32)
        self.current_phase = 0
        self.phase_duration = 0
        self.total_wait = 0.0
        self.step_count = 0
        self.emergency_active = False
        self.emergency_direction = -1
        return self._get_obs(), {}

    def _get_obs(self):
        return np.concatenate([
            self.queues,
            self.densities,
            [self.current_phase / 3.0],
            [min(self.phase_duration / self.MAX_GREEN, 1.0)]
        ]).astype(np.float32)

    def _simulate_traffic(self, phase):
        """Simulate vehicle arrivals and departures."""
        arrival_rates = np.random.uniform(0.05, 0.25, 4)
        service_rates = np.zeros(4)

        # Serving directions based on phase
        if phase == 0:   service_rates[[0, 2]] = 0.35  # NS
        elif phase == 1: service_rates[[1, 3]] = 0.35  # EW
        elif phase == 2: service_rates[[0]] = 0.30      # NS left
        elif phase == 3: service_rates[[1]] = 0.30      # EW left

        self.queues = np.clip(self.queues + arrival_rates - service_rates, 0, 1)
        self.densities = np.clip(self.densities * 0.9 + arrival_rates * 0.1, 0, 1)

    def step(self, action):
        # Enforce minimum green time
        if action != self.current_phase and self.phase_duration < self.MIN_GREEN:
            action = self.current_phase  # hold current phase

        # Emergency vehicle override
        emergency_bonus = 0
        self.emergency_active = np.random.random() < self.emergency_prob
        if self.emergency_active:
            self.emergency_direction = np.random.randint(0, 4)
            # Override action to clear emergency direction
            action = self.emergency_direction % 2  # 0 or 1 (NS or EW)
            emergency_bonus = 50  # big reward for correct response

        if action == self.current_phase:
            self.phase_duration += self.dt
        else:
            self.current_phase = action
            self.phase_duration = self.dt

        self._simulate_traffic(action)

        wait_penalty = float(np.sum(self.queues) * 10)
        reward = -wait_penalty + emergency_bonus
        self.total_wait += wait_penalty
        self.step_count += 1

        truncated = self.step_count >= 720  # 1 hour
        terminated = False

        info = {
            "avg_queue": float(np.mean(self.queues)),
            "total_wait": self.total_wait,
            "phase": self.PHASE_NAMES[self.current_phase],
            "emergency": self.emergency_active,
        }
        return self._get_obs(), reward, terminated, truncated, info


# ─────────────────────────────────────────────
# MULTI-INTERSECTION ENVIRONMENT (4 intersections)
# ─────────────────────────────────────────────
class MultiIntersectionEnv(gym.Env):
    """
    4 intersections in a 2x2 grid.
    Agents share state with neighbors (cooperative RL).
    """
    def __init__(self, n_intersections=4):
        super().__init__()
        self.n = n_intersections
        self.envs = [TrafficIntersectionEnv() for _ in range(n_intersections)]

        # Observation: own state (10) + avg neighbor state (10)
        self.observation_space = spaces.Box(low=0, high=1, shape=(20,), dtype=np.float32)
        self.action_space = spaces.Discrete(4)
        self.current_agent = 0

    def reset(self, seed=None, options=None):
        obs_list = [e.reset(seed=seed)[0] for e in self.envs]
        self.obs_list = obs_list
        return self._get_combined_obs(0), {}

    def _get_combined_obs(self, agent_idx):
        own = self.obs_list[agent_idx]
        others = [self.obs_list[i] for i in range(self.n) if i != agent_idx]
        neighbor_avg = np.mean(others, axis=0)
        return np.concatenate([own, neighbor_avg]).astype(np.float32)

    def step(self, action):
        obs, reward, terminated, truncated, info = self.envs[self.current_agent].step(action)
        self.obs_list[self.current_agent] = obs

        # Cooperative reward: penalize if neighbors have high queues too
        neighbor_penalty = sum(
            np.mean(e.queues) for i, e in enumerate(self.envs)
            if i != self.current_agent
        ) * 2.0
        reward -= neighbor_penalty

        self.current_agent = (self.current_agent + 1) % self.n
        combined_obs = self._get_combined_obs(self.current_agent)
        return combined_obs, reward, terminated, truncated, info


# ─────────────────────────────────────────────
# TRAINING
# ─────────────────────────────────────────────
def train(use_multi=True, timesteps=200_000):
    try:
        from stable_baselines3 import DQN, PPO
        from stable_baselines3.common.callbacks import EvalCallback, CheckpointCallback
        from stable_baselines3.common.monitor import Monitor
    except ImportError:
        print("Install: pip install stable-baselines3")
        return

    os.makedirs("models", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    env = MultiIntersectionEnv() if use_multi else TrafficIntersectionEnv()
    env = Monitor(env, "logs/")

    model = DQN(
        "MlpPolicy", env,
        learning_rate=1e-3,
        buffer_size=50_000,
        learning_starts=1_000,
        batch_size=64,
        tau=1.0,
        gamma=0.99,
        train_freq=4,
        target_update_interval=1_000,
        exploration_fraction=0.1,
        exploration_final_eps=0.05,
        verbose=1,
        tensorboard_log="logs/tensorboard/"
    )

    checkpoint_cb = CheckpointCallback(
        save_freq=10_000,
        save_path="models/checkpoints/",
        name_prefix="nexus"
    )

    print(f"\n🚦 Training NEXUS RL agent ({'Multi' if use_multi else 'Single'}-intersection)")
    print(f"   Timesteps: {timesteps:,}\n")
    model.learn(total_timesteps=timesteps, callback=checkpoint_cb)
    model.save("models/nexus_agent")
    print("\n✅ Model saved to models/nexus_agent.zip")

    # Quick evaluation
    obs, _ = env.reset()
    total_reward = 0
    for _ in range(200):
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
        if terminated or truncated:
            break

    print(f"   Eval reward (200 steps): {total_reward:.1f}")
    print(f"   Avg queue length: {info['avg_queue']:.3f}")
    return model


if __name__ == "__main__":
    train(use_multi=True, timesteps=200_000)
