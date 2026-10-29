-- Reconciliation bronze → silver: every receipt landed in bronze is in silver.
{{ config(enabled=(target.type == 'postgres')) }}
select b.org_id, b.business_key
from {{ source('bronze', 'bronze_pos_receipts') }} b
left join {{ ref('stg_receipts') }} s on s.org_id = b.org_id and s.receipt_id = b.business_key
where s.receipt_id is null
  and b.received_at < now() - interval '10 minutes'
