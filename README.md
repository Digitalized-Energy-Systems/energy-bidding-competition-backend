# Getting Started

This is the backend of a market and unit simulation environment. This environment simulates a simple market and the units of all market participants.

## Install dependencies
```bash
pip install poetry
poetry install
```

## Start server (might need to used chmod +x before)
```bash
poetry shell
uvicorn hackathon_backend.main:app --host 0.0.0.0 --port 8000 --reload --log-config=log_conf.yaml
```

After the server has been started you can check the REST APIs under (default): http://localhost:8000/docs.

## Config
There is a configuration file for the backend config.json. This files include several options:
* participants: List of participant IDs, which will be accepted on register
* rt_step_duration_s: Real-time duration per simulated time step
* rt_step_init_delay_s: Real-time delay before the first step is simulated
* pause: True if you want to pause the server (no restart required)
* max_steps: Number of time steps to simulate
* test_mode: Special test mode, this will allow to call register multiple times for the same participant, and it will allow registration for the whole duration

