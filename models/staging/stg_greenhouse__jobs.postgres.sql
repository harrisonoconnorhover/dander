-- PostgreSQL variant of the shared Greenhouse staging model.
-- Nested RECORD values use PostgreSQL's canonical JSONB fallback.
WITH source AS (
  SELECT
    id,
    internal_job_id,
    title,
    company_name,
    location ->> 'name' AS location_name,
    absolute_url,
    language,
    first_published,
    updated_at
  FROM {{ ref('raw_greenhouse_job_board_jobs') }}
)

SELECT
  CAST(id AS TEXT) AS job_id,
  CAST(internal_job_id AS TEXT) AS internal_job_id,
  title,
  company_name,
  location_name,
  absolute_url,
  language,
  first_published,
  updated_at
FROM source
