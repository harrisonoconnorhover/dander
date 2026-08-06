# Morning Handoff

## Finished

- Added a copyable Salesforce CRM project with Accounts, Contacts, Opportunities, and Users.
- Added five governed staging/fact models with tests, metrics, relationships, and Dataplex publication.
- Packaged the Salesforce project overlay in every generated source-free Dander project.
- Updated Salesforce documentation for personal data, soft deletion, replay, and custom fields.
- Corrected final review blockers and prepared the `0.6.0rc1` package metadata.

## Try It

Generate a project, then copy `examples/salesforce/dander.yaml`, `connectors/`, and `models/`
from the packaged Salesforce overlay into the project root. Install the exact plugin pin from the
example manifest, validate, and run `dander run salesforce_crm --dry-run --project PROJECT_ID`.

## Checks

- Full Dander suite passed: `764 passed`.
- Ruff formatting/lint and strict mypy passed.
- Main and stage-zero Terraform initialization and validation passed with backends disabled.
- Local Linux container build passed with the packaged Salesforce example in its build context.
- Wheel/sdist inspection and external, source-free scaffold generation passed.
- Fresh Dander/plugin wheel installation, four-endpoint validation, and dry-run passed.

## Decisions

- New examples use `salesforce_crm`; the retained project keeps its existing pipeline ID.
- Contact email and phone remain enabled and are explicitly documented as personal data.
- Beta classification remains gated on published-candidate live acceptance.

## Remaining

- Publish the Dander and Salesforce candidates only after explicit approval.
- Run the fresh-project live proof before changing Alpha to Beta or publishing stable releases.
- Retained-project deployment, schedule changes, and the seven-day soak remain separately gated.

## Review First

- `examples/salesforce/dander.yaml`
- `models/marts/fct_salesforce__opportunities.sql`
- `src/dander/project/scaffold.py`
