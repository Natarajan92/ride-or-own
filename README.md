# RideOrOwn

A local Python application for comparing your cab travel with five years of owning a petrol automatic car in Bengaluru. Built with Streamlit, uv, SQLite, and deterministic financial calculations.

## Run locally

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/), then clone and start the app:

```powershell
git clone https://github.com/Natarajan92/ride-or-own.git
cd ride-or-own
uv sync --locked
uv run streamlit run app.py
```

Open `http://127.0.0.1:8501`. Python 3.12 and dependencies are pinned through `.python-version` and `uv.lock`. The app binds to localhost by default.

If port 8501 is occupied, run `uv run streamlit run app.py --server.port 8502` and open `http://127.0.0.1:8502`.

Data is saved under `data/`. To choose another location, set `RIDEOROWN_DATA_DIR` before starting the app:

```powershell
$env:RIDEOROWN_DATA_DIR = 'E:\rideorown-data'
uv run streamlit run app.py
```

## Follow the five steps

1. **Cab receipts:** import Rapido PDFs and check dates and available amounts. Selected Price is initial-price evidence; enter the final payment only when known. Tips remain unknown unless entered and are never added twice.
2. **Advanced travel settings:** create and edit travel groups, recurring journeys, cab or bus fares, driving distances, replacement choices and monthly attendance overrides. Receipt-only forecasts can continue without changing groups. Saving a group does not change your selected forecast source.
3. **Cab forecast:** choose receipt history or your saved travel groups and review the cab projection. For receipts, select six consecutive calendar months, confirm complete and representative spending, and enter the car kilometres that would replace those journeys. Empty months remain visible and require confirmation; missing dates or fares block forecasting. Annual India inflation is editable here. Travel groups are edited on page 2.
4. **New car estimation and purchase plan:** enter a dealer quote or choose a price reference, then work through financing, monthly running budgets, insurance and resale. Saved purchase cash, EMI and monthly fixed costs appear below the inputs.
5. **Comparison and decision:** compare continuing with cabs against buying a car, including cash paid today, ongoing costs, resale received and any loan settlement. Inspect Today plus five yearly totals, all 60 months, cumulative cash flows and downloadable reports. Back/Continue buttons connect all five pages; save each form before continuing.

The default forecast uses receipt spending directly:

```text
Monthly cab baseline = available receipt amounts in the six selected calendar months / 6
Cab cost in forecast year y = baseline × 12 × (1 + annual inflation / 100)^(y − 1)
Five-year cab spending = sum of forecast years 1–5
```

The denominator stays six even when one month has no receipts. Partial coverage produces a provisional result until explicitly confirmed; a missing fare is never counted as zero. New imports or changed receipt dates/payments clear the completeness confirmation. The suggested window ends at the latest receipt month before the forecast; current forecast-month receipts are excluded and their count is shown. An explicitly selected historical window must end before the first forecast month. Changing a saved window clears its confirmation so the newly displayed months can be reviewed. Forecast months repeat the observed monthly mean; six months do not establish annual seasonality or future changes in journey volume.

Inflation defaults to **4.82%**, the combined India CPI year-on-year reading for **August 2026**, provisional, published **14 September 2026** and checked **11 October 2026** ([MoSPI/PIB release](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2310058&lang=1&reg=19)). This is a dated, editable planning reference, not a guaranteed five-year inflation forecast. Year one uses the observed baseline; later years compound the assumption. It applies once to cab fares, petrol and recurring operating costs. Fixed EMI, explicit future insurance renewal budgets and year-five resale are not inflated again. The local app does not make a network request for live CPI.

Driving kilometres are independent inputs, never inferred from fares. Enter the kilometres you would actually drive after choosing which cab spending to retain. Retained cab spending appears in both alternatives; the entered driving kilometres are not reduced a second time. Saved manual historical entries are preserved for audit, but the receipt forecast does not add them or travel-group fares to its baseline.

Create and edit groups on **Advanced travel settings**, then choose **Travel groups (advanced)** as the source on **Cab forecast** to use their detailed recurrence, bus travel, hired drivers, per-trip tolls and existing scenarios. Choosing this source replaces the receipt forecast rather than adding another set of fares. In this mode, separate escalation rates remain in **New car plan**.

**Current comparison** is the default chart view and follows your saved receipt/group source and **Included journeys** selection. The selection updates the decision, forecast and exports and survives reload. New workspaces have no example groups. Older saved journeys remain available and visibly selected; deselect any that do not belong in your comparison.

Three broader exploration views remain available: **Cab forecasts**, **Cab + travel groups**, and **Travel group**. These have a separate, clearly labelled scope. Combined receipt/group totals can overlap and are not used for the buying decision. Choose **All five years** or a dated rolling twelve-month forecast year. The five-year total remains visible throughout.

Charts show annual or monthly spending, cumulative spending, and each group's contribution with scenario shares. Lower and upper fares are alternative estimates, shown separately. Cumulative spending in a selected year starts from that year's beginning. Receipt fares use the inflation assumption from Cab forecast; groups use the advanced fare escalation from New car plan, with both rates shown in combined mode. Fare-only charts work before car costs or replacement distances are entered. Unconfirmed receipt history is marked provisional; unavailable receipt history does not prevent group analysis. Tables and a CSV download expose the selected projection. Chart filters do not change the receipt/group source used by the buying decision, which is labelled beside the charts.

New workspaces start with no travel groups. Create groups for your own journeys and enter their fares, distances, and schedules. Older saved groups are preserved when reopening a workspace. Weekly visits use 52/12 weeks per month. Monthly overrides handle leave, skipped trips, and extra attendance. A linked group's visit count multiplies the parent's visits.

In **Advanced travel settings**, select a table row to edit that group's saved values in the form below, then choose **Save travel group**. Use **New travel group** to open a blank form. Saving keeps the edited or newly created group selected. Continue to **Cab forecast** to review the projections; the forecast page does not contain group creation or editing controls.

For commuting, create or select a group and choose **Cab** or **Public transport (bus)** under **Travel mode**, then save the group. The bus fare defaults to **₹200 per office day**, including both directions: three office days weekly projects to 13 days and ₹2,600 per average month. You can edit the bus fare and attendance. Switching back to Cab restores the saved cab fare range and separate tip; cab tips do not apply to bus travel. Projections and the car comparison use the selected transport. The ownership choice can also retain the bus for all or some visits.

Enter each group's driving kilometres before relying on an ownership conclusion, or choose to retain cabs for that group. The app never derives kilometres from fares. Self-driving, using your own car with a hired driver, retaining cabs, and replacing a fraction of visits are supported per group. Costs for hired drivers, tolls, and parking are entered separately.

## Receipts and saved historical data

The extractor supports selectable-text Rapido receipts. No personal receipts or saved workspaces are distributed with the repository; import your own PDFs. Tests generate synthetic receipts in memory. Scanned/image-only, encrypted, unreadable, unsupported, or more-than-five-page PDFs produce individual errors. OCR is deferred. Original PDFs and extracted values remain available alongside user corrections.

The receipt's **Selected Price** is the initial agreed price, not evidence of final payment or included tips. `final_paid` and `tip_paid` remain unknown (`null`) until the user enters them. The editor preserves Selected Price and original extraction separately. A user-entered final payment replaces the selected price in totals; a known tip is part of that final payment and is never added again. Otherwise the available selected price or legacy correction supports a clearly provisional forecast, even when history coverage is confirmed. Legacy final-payment corrections remain respected and old separate-tip estimates are ignored. Historical reconciliation separates known final payments from provisional amounts.

The main decision evaluates optional upfront and strict monthly cash limits separately. It shows the peak scheduled monthly outflow, its months, the difference from the cap, and every breached month. Gross outflows include insurance renewals and final loan settlement; resale inflows do not offset a strict cash-out cap. Average monthly spending is shown separately. Limits default to blank, not the fictional review constraints.

Sensitivity controls show active percentages and cost changes from the saved baseline, with a reset action. The decision, printable report and monthly CSV record effective inputs and sensitivity factors. In receipt mode the receipt inflation rate applies to fares, fuel and operations; the three stored advanced rates are inactive and explicitly labelled. In group mode those advanced rates apply and receipt settings are inactive.

Previously saved monthly entries remain available in historical summaries and backups, with two meanings:

- **Additional missing spending:** adds only expenditure absent from receipts and other entries.
- **Complete monthly total:** replaces receipts and additional entries for that month and travel group. Ungrouped entries reconcile within their category; ungrouped receipts belong to `Other travel`. Only one complete total is allowed per bucket. The documented receipt amount remains visible for audit.

In advanced mode, add or edit a future travel group for journeys that should continue. In receipt mode, the confirmed six-month average supplies future cab spending. Historical kilometres never silently become future driving assumptions.

Previously saved coverage statuses are preserved in historical summaries and backups. The receipt forecast displays six calendar months explicitly and asks for confirmation that they cover all cab spending and represent future travel. Unconfirmed history keeps the decision provisional.

## Financial assumptions

The model uses Decimal arithmetic and a nominal 60-month horizon. Purchase cash appears at month zero. Loan repayments use a fixed nominal annual rate with monthly reducing balance, rounded to paise. A loan may extend beyond five years; the remaining balance is included in the final comparison.

```text
Five-year net ownership cost =
  upfront cash + repayments + running costs + retained cabs
  + remaining loan balance − resale value
```

The full purchase price is not charged again alongside loan principal. Resale represents depreciation; there is no additional depreciation expense. The model excludes investment returns and discounting. Enter financing fees, accessories, and other upfront costs explicitly where relevant.

Amounts use INR and Indian comma grouping: ₹5,00,000 (five lakh), ₹1,00,00,000 (one crore). Monetary inputs accept ungrouped numbers, Indian commas, legacy Western commas, and optional ₹/INR/Rs. prefixes. They reject malformed grouping, negative amounts, scientific notation and fractional paise. Saved input amounts support at most two decimal places; calculated forecasts retain Decimal precision. Cards round half up to whole rupees; detailed monetary tables and purchase breakdowns show paise. CSV exports use ungrouped numeric amounts so spreadsheets can calculate with them; monthly cash-flow exports retain calculation precision.

Down payment is derived automatically. For a ₹9,03,000 on-road car funded with a ₹5,00,000 loan, it is ₹4,03,000. **Additional upfront costs not in quote** must be zero unless there are separately paid costs outside that price. Do not enter the down payment again. The saved preview, comparison and printable report expose purchase price minus loan, calculated down payment, separate extras and total cash needed. A loan above the adjusted purchase price is rejected when saving a complete purchase price.

The EMI estimator uses the **nominal annual reducing-balance interest rate**, not a flat rate or the all-inclusive APR. Monthly rate = annual percentage / 1200; tenure is in months (five years = 60). Instalments and monthly interest round half up to paise, and the final instalment clears the rounded balance. This is a fixed-rate monthly estimate, not a universal Indian bank repayment convention: [SBI's car-loan terms](https://sbi.bank.in/web/personal-banking/loans/auto-loans/sbi-new-car-loan-scheme) specify daily reducing interest. Match the actual rate, fees, applicable taxes and repayment schedule to your lender's [Key Facts Statement](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663). Enter separately paid fees including tax as extras only if excluded from the quote; costs already included in the on-road price must not be added again.

Cab fares remain lower/upper ranges. Retained cab spending occurs on both alternatives and cancels when deciding which is cheaper; opposite range endpoints are never compared as if they were independent. Missing driving distance or unconfirmed ownership assumptions make the conclusion provisional. Essential missing car inputs block calculation.

The decision leads with a verdict and the saving at each fare endpoint. If the cheaper choice changes within the range, it says **Buying has no clear cost advantage**, shows both outcomes and the spending threshold, and asks you to narrow the forecast before buying for savings. It does not choose an invented midpoint or assign probabilities. A tie at one endpoint is described explicitly; unresolved warnings keep the verdict provisional. The printable report uses the same verdict and evidence.

In receipt mode, cab fare, petrol price and operating budgets escalate at the shared inflation assumption from Cab forecast. Advanced groups use the separately editable planning defaults below, researched on **11 October 2026**. They compound once per forecast year, beginning with year two.

| Advanced assumption | Annual default | Research basis |
| --- | --- | --- |
| Cab fares | **3.39%** | August 2026 all-India combined passenger-transport CPI, provisional; a broad transport proxy, not measured Bengaluru cab-fare growth. [MoSPI, Annexure II, 07.3](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2310058&lang=1&reg=19) |
| Petrol prices | **5%** | Planning allowance informed by Bengaluru petrol rising from ₹77.71 on 1 January 2020 to ₹102.92 on 1 January 2025: about 5.78% annual compound growth between snapshots. [MoPNG/PPAC history](https://sansad.in/getFile/annex/268/AU1729_OerGJQ.pdf?source=pqars) |
| Operating budgets | **5%** | Rounded proxy from August 2026 headline India CPI of 4.82%, for nonfuel maintenance, parking, tolls and driver budgets. It does not measure extra repair needs as a car ages. [MoSPI](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2310058&lang=1&reg=19) |

These are editable budgeting assumptions, not official five-year forecasts. Petrol growth is uneven: Bengaluru was ₹102.96 on 11 March 2026, nearly unchanged from ₹102.92 on 1 April 2025 ([MoPNG/PPAC, 16 March 2026](https://sansad.in/getFile/annex/270/AU2689_st3pq5.pdf?source=pqars)). These dated reference prices do not replace your entered starting petrol price. The broader personal-transport operation CPI includes fuel, so it is not applied again to the separate operating budget. The app does not fetch live rates.

New scenarios receive these defaults. Existing saved overrides, including explicit zero rates, are preserved; **Use researched escalation defaults** on New car plan applies the reference rates and saves the form, clearing the budget confirmation for review. The dated references are also included in the printable report. The constants and source explanations live in `src/rideorown/domain/assumptions.py`.

Fixed EMI, four explicit future insurance renewal budgets, and future resale value are not escalated again. Demand sensitivity changes replaced travel and its kilometres together, while retained cabs and additional ownership-only driving stay fixed.

Cash-flow reports include Today (month zero) and months 1–60. The final month records resale as an inflow and outstanding loan settlement as an outflow. Cash paid minus cash received therefore reconciles with the displayed five-year net cost. Loan financing reduces upfront cash; purchase price and loan principal are not charged twice.

The monthly spending threshold answers: *at these entered car kilometres, what first-year monthly replaceable cab spending gives equal five-year cost?* It does not claim a precise break-even month. Comfort, waiting, driving effort, and parking preferences are saved separately without invented monetary scores.

## Car references and ACKO

Ten petrol automatic references are stored in `src/rideorown/resources/cars_bengaluru.json`, with the source URL, manufacturer URL, city, research date, available precision, and notes. They are approximate snapshots researched on 10 October 2026, not live dealer offers. An effective price date is not assumed when unavailable. Use an actual dealer quote before making a purchase decision.

ACKO is the only insurance provider represented. The app requires an actual premium or an explicitly chosen planning budget; it does not invent a provider quote. The initial coverage assumption is three years of third-party cover plus one year of own damage. Renewal budgets occur at the beginning of years 2–5: own damage/add-ons for years 2–3, then third party plus own damage/add-ons for years 4–5. Confirm the coverage against your policy.

When a reference on-road price contains generic insurance, acquisition is normalized as `on-road price − embedded insurance + initial ACKO premium`. The preset's embedded insurance component is unknown, so enter a documented component or a labelled estimate. Alternatively enter a quote already containing ACKO. An unknown component is never treated as zero silently.

Sources: [ACKO third-party cover](https://www.acko.com/third-party-car-insurance/), [own-damage cover](https://www.acko.com/car-insurance/own-damage-insurance/), [premium calculator](https://www.acko.com/car-insurance/premium-calculator/).

## Local storage, backups, and limits

`data/rideorown.sqlite3` stores the versioned workspace; original PDFs are stored as `data/receipts/<sha256>.pdf`. Identical PDF hashes and repeated ride IDs are checked to prevent duplicate spending. Each successful import is saved, so a later bad file does not discard earlier successes. Conflicting extracted values for an existing ride are reported for review.

Prepare and download a **ZIP backup** from the sidebar. It contains `workspace.json` and original PDFs, preserving corrections, groups, entries, scenarios, and personal notes. ZIP avoids inflating PDFs into base64 JSON. Restore checks version, references, duplicate identifiers, paths, PDF structure, and hashes before replacing the workspace. Missing originals block a complete backup. Restore supports backups up to 500 MB with additional decoded-size and entry-count limits.

Receipts and backups contain personal locations. They are processed locally and are not sent to an LLM, insurer, or analytics service. Car and insurance source links open external websites only when you follow them. `data/`, sample receipts, local environments, and test artifacts are excluded from Git.

Accepted import policy is **500 PDFs per batch, 5 MB per PDF, and 50 MB combined**. These limits are safeguards, not a benchmark or concurrent-user capacity claim. The Streamlit uploader may transfer selected files into server memory before count/aggregate-size validation; keep batches modest on low-memory machines. You may import more batches into the same workspace.

This MVP is intended for one local user. SQLite stores one workspace document and the UI keeps session state, so concurrent sessions editing the same data directory can overwrite one another. Do not expose this local deployment as a shared multiuser service. A hosted phase needs authentication, user-scoped relational records, PostgreSQL, object storage, bounded asynchronous extraction jobs, quotas, and load testing. The domain calculations and parser are independent of Streamlit to support that migration.

If saved data changes outside your current browser session, **Reload saved workspace** in the sidebar loads it and clears stale form values while keeping your current step. Save any pending edits first: reload discards unsaved form changes. Ordinary reruns preserve the session instead of silently replacing it.

## Project structure

```text
app.py                         Streamlit entry point
pyproject.toml / uv.lock        Package and reproducible environment
src/rideorown/
  config.py                    Local paths and import limits
  domain/                      Models, recurrence, loans, ownership, comparison
  receipts/                    PDF validation and Rapido extraction
  storage/                     SQLite, original PDFs, validation, ZIP backup
  services/                    Import workflow, receipt forecasting, cash-flow reports, catalog
  resources/                   Dated car references and ACKO coverage metadata
  ui/screens/                  Five connected steps: receipts, groups, forecast, car, decision
tests/
  unit/                        Financial, recurrence, receipt and baseline checks
  integration/                 Persistence, backup, and import workflow checks
  ui/                          Streamlit AppTest flows
.github/workflows/             Automated checks for pushes and pull requests
```

Local-only folders such as `data/`, `sample-data/`, `instructions/`, `reports/`, and temporary test artifacts are ignored by Git. Keep personal receipts, backups, and exports out of contributions.

## Verify

```powershell
uv sync --locked
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest -q
```

Tests use synthetic receipts and temporary workspaces and do not modify your runtime data. They cover receipt parsing, duplicate imports, reconciliation, loan and insurance timing, cash/net accounting, retained fares, recurrence, missing distances, backup integrity, and main UI flows. GitHub Actions runs these checks on Linux and Windows with Python 3.12. Batch policy has not been validated by a hosted concurrency/load benchmark.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and public contribution guidance. A software license has not been selected for this project.

Deferred: OCR, address-to-distance mapping, live car/insurance APIs, additional fuels, account-aggregator integrations, LLM recommendations, authentication, and hosted multiuser operation.
