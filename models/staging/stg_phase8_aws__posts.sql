-- Portable Phase 8 AWS qualification model over the immutable flat fixture.
SELECT
  id AS post_id,
  title
FROM {{ ref('raw_phase8_aws_fixture_posts') }}
