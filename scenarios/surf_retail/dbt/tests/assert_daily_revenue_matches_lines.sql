-- Reconciliation silver → gold: daily mart revenue equals the sum of the (non-cancelled) lines.
select m.org_id
from (select org_id, sum(revenue_cents) as revenue from {{ ref('mart_sales_daily') }} group by org_id) m
join (select org_id, sum(net_cents) as revenue from {{ ref('fct_sales_line') }}
      where coalesce(order_status, '') <> 'cancelled' group by org_id) f on f.org_id = m.org_id
where m.revenue <> f.revenue
