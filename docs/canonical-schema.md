# Canonical relation and schema contract

Dander schema contract v1 keeps portable meaning separate from provider SQL and SDK objects.
`RelationRef` carries `catalog`, `namespace`, and `name` as unrendered coordinates. A warehouse
adapter's `RelationCodec` validates provider-specific limits and performs all quoting and rendering.

Canonical fields declare:

- boolean, signed integer, decimal, floating-point, string, binary, date, time, timestamp, JSON,
  array, and record types;
- bit width for integers and floating-point values;
- precision and scale for every decimal;
- timezone semantics and fractional-second precision for time/timestamp values;
- nullable or required cardinality; and
- ordered provider extensions for information that has no portable equivalent.

Arrays contain one canonical element type. Records contain recursively validated, uniquely named
fields. Lossy mappings are not automatic.

## BigQuery compatibility

Existing connector `RawField` and writer `WriteField` declarations remain the authored contract.
Their canonical view maps `NUMERIC` to exact decimal precision/scale, distinguishes `TIMESTAMP`
from timezone-free `DATETIME`, records BigQuery's microsecond precision, and maps `REPEATED` to a
required canonical array. Original BigQuery type and mode remain ordered extensions for
traceability.

Connector raw fields, graph fields, and model columns may also declare validated `extensions`.
These annotations retain their canonical provider/name/value identity through planning and
execution; only the matching warehouse adapter interprets them. Dander never silently turns an
extension into a portable guarantee. Existing declarations without extensions are unchanged.

`WriteTarget` retains the legacy BigQuery-shaped schema for compatibility while carrying the
validated canonical `RelationSchema` selected before extraction. Provider-neutral orchestration
does not reconstruct that schema after a provider has validated it.

`BIGNUMERIC` (whose documented precision includes a partial 77th digit), `GEOGRAPHY`, `INTERVAL`,
or a future BigQuery-only type fails mapping unless a caller explicitly provides a canonical
fallback. This is intentional: validation must not silently relabel a lossy conversion as
portable. This change does not alter BigQuery writes, transform SQL, or deployed schemas; provider
codecs and runtime bundles consume these contracts in later portability PRs.
