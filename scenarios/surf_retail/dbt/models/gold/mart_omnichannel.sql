-- Online orders per placing day and store: click & collect by collecting store, home
-- deliveries under the virtual WEB store.
select
    o.org_id,
    o.day,
    coalesce(o.store_id, 'WEB') as store_id,
    coalesce(max(s.store_name), 'Online shop') as store_name,
    sum(case when o.fulfilment = 'click_collect' then 1 else 0 end) as click_collect_orders,
    sum(case when o.fulfilment = 'click_collect' then o.grand_total_cents else 0 end) as click_collect_revenue_cents,
    sum(case when o.fulfilment = 'click_collect' and o.status in ('collected', 'returned') then 1 else 0 end)
        as collected_orders,
    sum(case when o.fulfilment = 'home_delivery' then 1 else 0 end) as delivery_orders,
    sum(case when o.fulfilment = 'home_delivery' then o.grand_total_cents else 0 end) as delivery_revenue_cents
from {{ ref('stg_web_orders') }} o
left join {{ ref('dim_store') }} s on s.org_id = o.org_id and s.store_id = o.store_id
where o.status <> 'cancelled'
group by o.org_id, o.day, coalesce(o.store_id, 'WEB')
