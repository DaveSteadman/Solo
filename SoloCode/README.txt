SoloCode
========

SoloCode is the local coding-agent surface for Solo.

Run:

    python .\SoloCode\main.py

Then open:

    http://127.0.0.1:9760/ui

Configuration:

- Port: Config/factory-default.json -> services.solocode.port
- Workspace root: Config/factory-default.json -> paths.soloCodeWorkspaceRoot
- Runtime data/logs: Data/SoloCode

The UI is defined by ui/page.json and rendered with SoloCommonWebUI controls.
The chat panel submits prompts into SoloChat using a dedicated external
conversation id for the selected workspace root.
