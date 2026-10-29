-- Training data of the board-purchase propensity model (child scenario surf_customer_propensity):
-- every loyalty customer (pseudonymised key) seen before a snapshot 90 days before the latest
-- data, described only by what they bought before it, and whether they bought a board in the
-- 90 days after (the target). Kite bars and harnesses are gear, not boards.
with lines as (
    select org_id, customer_key, doc_id, day, channel, rider_group, net_cents, qty,
           case when rider_group in ('surf', 'sup', 'kite') and category not in ('Bars & lines', 'Harnesses')
                then 1 else 0 end as is_board
    from {{ ref('fct_sales_line') }}
    where customer_key is not null and coalesce(order_status, '') <> 'cancelled'
),
snap as (
    select org_id, {{ add_days('max(day)', -90) }} as snapshot_day from lines group by org_id
),
before as (
    select l.* from lines l join snap s on s.org_id = l.org_id where l.day <= s.snapshot_day
),
after as (
    select l.org_id as org_id, l.customer_key as customer_key, max(l.is_board) as bought_board
    from lines l join snap s on s.org_id = l.org_id
    where l.day > s.snapshot_day
    group by l.org_id, l.customer_key
),
history as (
    select
        org_id, customer_key,
        count(distinct doc_id) as baskets,
        sum(net_cents) / 100.0 as spend_eur,
        sum(case when is_board = 1 then qty else 0 end) as boards_bought,
        sum(case when rider_group = 'accessory' then net_cents else 0 end) / 100.0 as accessory_eur,
        sum(case when rider_group = 'surf' then net_cents else 0 end) / nullif(sum(net_cents), 0) as surf_share,
        sum(case when rider_group = 'sup' then net_cents else 0 end) / nullif(sum(net_cents), 0) as sup_share,
        sum(case when rider_group = 'kite' then net_cents else 0 end) / nullif(sum(net_cents), 0) as kite_share,
        count(distinct case when channel = 'online' then doc_id end) as online_baskets,
        min(day) as first_day,
        max(day) as last_day,
        max(case when is_board = 1 then day end) as last_board_day
    from before
    group by org_id, customer_key
)
select
    h.org_id as org_id,
    h.customer_key as customer_key,
    s.snapshot_day as snapshot_day,
    {{ days_between('s.snapshot_day', 'h.last_day') }} as recency_days,
    {{ days_between('s.snapshot_day', 'h.first_day') }} as tenure_days,
    h.baskets as baskets,
    h.spend_eur as spend_eur,
    h.boards_bought as boards_bought,
    h.accessory_eur as accessory_eur,
    coalesce(h.surf_share, 0) as surf_share,
    coalesce(h.sup_share, 0) as sup_share,
    coalesce(h.kite_share, 0) as kite_share,
    1.0 * h.online_baskets / h.baskets as online_share,
    coalesce({{ days_between('s.snapshot_day', 'h.last_board_day') }}, 3650) as days_since_last_board,
    coalesce(a.bought_board, 0) as buys_board_next_90d
from history h
join snap s on s.org_id = h.org_id
left join after a on a.org_id = h.org_id and a.customer_key = h.customer_key
