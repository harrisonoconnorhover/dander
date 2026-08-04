-- Purpose: normalize Odoo contacts and companies for CRM analysis.
-- Grain: one row per Odoo res.partner id.
WITH source AS (
  SELECT
    id AS partner_id,
    name AS partner_name,
    email,
    phone,
    city,
    country_code,
    is_company,
    active AS is_active,
    SAFE.PARSE_TIMESTAMP('%F %H:%M:%S', create_date) AS created_at,
    PARSE_TIMESTAMP('%F %H:%M:%S', write_date) AS updated_at
  FROM {{ ref('raw_odoo_partners') }}
)

SELECT
  partner_id,
  partner_name,
  email,
  phone,
  city,
  country_code,
  is_company,
  is_active,
  created_at,
  updated_at
FROM source
