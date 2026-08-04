-- Purpose: normalize NetSuite customers returned by the simulator-validated SuiteQL contract.
-- Grain: one row per NetSuite customer internal ID returned by the current full read.
WITH source AS (
  SELECT
    id AS customer_id,
    entity_id AS customer_code,
    company_name,
    email,
    phone,
    PARSE_TIMESTAMP('%FT%T', date_created_at) AS created_at,
    PARSE_TIMESTAMP('%FT%T', last_modified_at) AS updated_at,
    CASE
      WHEN UPPER(is_inactive) = 'T' THEN TRUE
      WHEN UPPER(is_inactive) = 'F' THEN FALSE
      ELSE NULL
    END AS is_inactive
  FROM {{ ref('raw_netsuite_customers') }}
)

SELECT
  customer_id,
  customer_code,
  company_name,
  email,
  phone,
  created_at,
  updated_at,
  is_inactive
FROM source
