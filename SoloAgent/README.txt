SoloAgent
=========

SoloAgent is the local agent/orchestration service for the Solo suite.

Run:

    python .\SoloAgent\main.py

Open:

    http://127.0.0.1:9710/ui

Implementation notes:

- Skill and system_skill folders are copied from KoreAgent.
- LLM access goes through SoloLLM.
- UI is defined by ui/page.json and rendered using SoloCommonWebUI controls.
- Runtime data is stored under Data/SoloAgent.
