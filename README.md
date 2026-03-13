# TD Auto Finance - Assisted Lending Workbench

Internal-demo Gradio MVP for manual-review auto finance adjudication workflows.

## Important

- Synthetic data only.
- All AI outputs are deterministic mocks.
- No API calls, no real LLM usage.
- Recommendation panel and copilot chat are simulation components for workflow demonstration.

## Project Files

- `app.py` - Gradio UI and interaction wiring
- `calculations.py` - deterministic financial/risk calculations
- `mock_ai.py` - deterministic recommendation engine + mock copilot response routing
- `data/applications.json` - synthetic review queue applications (15 cases)
- `data/mock_chat_responses.json` - canned copilot responses by application and intent
- `assets/styles.css` - TD-inspired visual styling
- `requirements.txt` - Python dependencies

## Run Locally

1. Create and activate a Python environment (Python 3.11+ recommended).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
python app.py
```

4. Open the local Gradio URL shown in terminal.

## Features Included

- Three-pane desktop-style underwriting workbench
- Left queue with search + filters + selectable review table
- Center analysis panel with:
  - summary strip
  - applicant/dealer/vehicle/credit cards
  - policy threshold matrix with pass/fail chips
  - financial/risk metric cards
  - detailed tabs for applicant, dealer, collateral, policy/docs, similar cases
  - mini application timeline accordion
- Right recommendation rail with:
  - recommendation badge (`approve`, `conditional_approval`, `decline`)
  - confidence, executive summary, drivers, conditions
  - alternate structures shown as secondary output
  - draft adjudication memo
  - memo export button
- Deterministic what-if condition simulator:
  - down payment
  - term
  - amount financed
  - live before-vs-after deltas
- Mock copilot chat panel:
  - prebuilt prompt chips
  - free-text input with keyword-to-intent matching
  - canned deterministic responses tied to selected application

## Mock AI Behavior

`mock_ai.py` uses deterministic rule logic based on:

- policy threshold results
- affordability and risk metrics
- dealer/watchlist and document signals
- exception severity

Same input scenario always returns the same recommendation object and chat output.

## Notes for Demo Use

- Use queue filters to switch between clean approves, conditional files, and declines/refers.
- Use **Apply Sample Condition** and sliders to show scenario sensitivity.
- Use **Refresh AI Recommendation** to demonstrate stable deterministic updates.
- Use copilot prompt chips to show where future assistant interactions would live in the workflow.
