# Runtime delay bounds are owned by browser tools

Runtime delay bounds live on **Runtime Delay Markers** inside browser tools, not on actor realism profiles. Actor realism only scales delays through the **Human Delay Profile** multiplier, while marker-local bounds decide where a specific pause needs a runtime floor or cap. This keeps runtime waiting policy visible at the tool step that needs it and removes actor-level cap fields from execution traces and realism compiler output.
