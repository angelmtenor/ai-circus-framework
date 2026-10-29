-- Every sales line's net equals its gross minus its discount (the contract's arithmetic,
-- re-checked after the transformation).
select sales_line_id
from {{ ref('fct_sales_line') }}
where net_cents <> gross_cents - discount_cents
