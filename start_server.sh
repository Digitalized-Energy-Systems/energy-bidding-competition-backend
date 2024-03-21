#!/bin/bash

uvicorn hackathon_backend.main:app --reload --log-config=log_conf.yaml
