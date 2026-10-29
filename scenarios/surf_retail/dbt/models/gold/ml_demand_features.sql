-- Training data of the weekly demand forecast (child scenario surf_demand_forecast): one row
-- per shop × rider group × week with what was known at the end of that week, and the units
-- sold the week after (the target). The latest week has no target yet: its row is what the
-- forecast is asked about now (is_latest). Lags are joins on week offsets, so the same SQL
-- runs on Postgres and ClickHouse.
with weekly as (
    select org_id, week, store_id, rider_group, sum(units) as units
    from {{ ref('mart_category_weekly') }}
    group by org_id, week, store_id, rider_group
),
weeks as (
    select distinct org_id, week from weekly
),
series as (
    select org_id, store_id, rider_group, min(week) as first_week
    from weekly group by org_id, store_id, rider_group
),
grid as (
    select s.org_id as org_id, s.store_id as store_id, s.rider_group as rider_group, w.week as week
    from series s
    join weeks w on w.org_id = s.org_id and w.week >= s.first_week
),
last_week as (
    -- The current week is still being sold: the latest complete week is the one before it.
    select org_id, {{ add_days('max(week)', -7) }} as week from weeks group by org_id
)
select
    g.org_id as org_id,
    concat(g.store_id, '|', g.rider_group, '|', cast(g.week as {{ dbt.type_string() }})) as row_id,
    g.store_id as store_id,
    g.rider_group as rider_group,
    coalesce(st.region, 'Online') as country,
    coalesce(st.spot, 'online') as spot,
    coalesce(st.store_size, 'online') as store_size,
    g.week as week,
    {{ week_of_year(add_days('g.week', 7)) }} as next_week_of_year,
    {{ month_of(add_days('g.week', 7)) }} as next_month,
    coalesce({{ int_div(days_between('g.week', 'st.opened'), 7) }}, 260) as store_age_weeks,
    coalesce(w0.units, 0) as units_this_week,
    coalesce(w1.units, 0) as units_last_week,
    (coalesce(w0.units, 0) + coalesce(w1.units, 0) + coalesce(w2.units, 0) + coalesce(w3.units, 0)) / 4.0
        as units_4w_avg,
    coalesce(ly.units, 0) as units_next_week_last_year,
    case when g.week = lw.week then null else coalesce(nx.units, 0) end as units_next_week,
    g.week = lw.week as is_latest
from grid g
join last_week lw on lw.org_id = g.org_id
left join {{ ref('mart_stores') }} st on st.org_id = g.org_id and st.store_id = g.store_id
left join weekly w0 on w0.org_id = g.org_id and w0.store_id = g.store_id and w0.rider_group = g.rider_group
    and w0.week = g.week
left join weekly w1 on w1.org_id = g.org_id and w1.store_id = g.store_id and w1.rider_group = g.rider_group
    and w1.week = {{ add_days('g.week', -7) }}
left join weekly w2 on w2.org_id = g.org_id and w2.store_id = g.store_id and w2.rider_group = g.rider_group
    and w2.week = {{ add_days('g.week', -14) }}
left join weekly w3 on w3.org_id = g.org_id and w3.store_id = g.store_id and w3.rider_group = g.rider_group
    and w3.week = {{ add_days('g.week', -21) }}
left join weekly ly on ly.org_id = g.org_id and ly.store_id = g.store_id and ly.rider_group = g.rider_group
    and ly.week = {{ add_days('g.week', -357) }}
left join weekly nx on nx.org_id = g.org_id and nx.store_id = g.store_id and nx.rider_group = g.rider_group
    and nx.week = {{ add_days('g.week', 7) }}
-- A shop's first four weeks are its opening ramp, not demand; nothing after the latest complete week.
where g.week <= lw.week and (st.opened is null or g.week >= {{ add_days('st.opened', 28) }})
