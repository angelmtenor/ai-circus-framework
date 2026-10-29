-- Completed online orders per week, fulfilment and return reason: every completed order sits
-- in exactly one row ("not_returned" for those kept), so rates add up at any grouping.
select
    org_id,
    {{ week_start('day') }} as week,
    fulfilment,
    coalesce(return_reason, 'not_returned') as reason,
    count(*) as completed_orders,
    sum(case when status = 'returned' then 1 else 0 end) as returned_orders
from {{ ref('stg_web_orders') }}
where status in ('delivered', 'collected', 'returned')
group by org_id, {{ week_start('day') }}, fulfilment, coalesce(return_reason, 'not_returned')
