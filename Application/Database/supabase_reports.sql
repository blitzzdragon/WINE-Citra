-- Run once in Supabase SQL Editor to enable WINE Citra reports.

create table if not exists public.report_maintainers (
    user_id uuid primary key references auth.users(id) on delete cascade
);

alter table public.report_maintainers enable row level security;
revoke all on table public.report_maintainers from anon, authenticated;

create or replace function public.is_report_maintainer()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.report_maintainers
        where user_id = (select auth.uid())
    );
$$;

revoke all on function public.is_report_maintainer() from public;
grant execute on function public.is_report_maintainer() to authenticated;

create table if not exists public.reports (
    id uuid primary key default gen_random_uuid(),
    created_by uuid not null references auth.users(id) on delete cascade,
    category text not null check (category = 'bug'),
    title text not null check (char_length(title) between 1 and 160),
    description text not null check (
        char_length(description) between 1 and 12000
    ),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists reports_created_by_created_at_idx
    on public.reports (created_by, created_at desc);

create or replace function public.report_set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;
revoke all on function public.report_set_updated_at() from public;

drop trigger if exists reports_set_updated_at on public.reports;
create trigger reports_set_updated_at
before update on public.reports
for each row execute function public.report_set_updated_at();

drop policy if exists "maintainers can update report status"
    on public.reports;
drop index if exists public.reports_status_created_at_idx;
alter table public.reports drop column if exists status;

alter table public.reports enable row level security;
revoke all on table public.reports from anon, public;
revoke insert, update on public.reports from authenticated;
grant select on public.reports to authenticated;
grant insert (created_by, category, title, description)
    on public.reports to authenticated;

drop policy if exists "report owners and maintainers can read reports"
    on public.reports;
create policy "report owners and maintainers can read reports"
on public.reports for select to authenticated
using (
    created_by = (select auth.uid())
    or (select public.is_report_maintainer())
);

drop policy if exists "authenticated users can create their own reports"
    on public.reports;
create policy "authenticated users can create their own reports"
on public.reports for insert to authenticated
with check (
    created_by = (select auth.uid())
);

create or replace function public.report_limit_submissions()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if (select public.is_report_maintainer()) then
        return new;
    end if;
    perform pg_advisory_xact_lock(hashtext(new.created_by::text));
    if (
        select count(*)
        from public.reports
        where created_by = new.created_by
          and created_at > now() - interval '1 hour'
    ) >= 5 then
        raise exception 'Report limit reached; try again later.';
    end if;
    return new;
end;
$$;
revoke all on function public.report_limit_submissions() from public;

drop trigger if exists reports_limit_submissions on public.reports;
create trigger reports_limit_submissions
before insert on public.reports
for each row execute function public.report_limit_submissions();

create table if not exists public.report_attachments (
    id uuid primary key default gen_random_uuid(),
    report_id uuid not null references public.reports(id) on delete cascade,
    storage_path text not null unique check (storage_path <> ''),
    original_name text not null check (original_name <> ''),
    content_type text not null check (
        content_type in (
            'image/png', 'image/jpeg', 'image/webp',
            'video/mp4', 'video/x-matroska', 'video/quicktime',
            'video/webm', 'video/x-msvideo'
        )
    ),
    file_size bigint not null check (file_size between 0 and 52428800),
    uploaded_by uuid not null references auth.users(id) on delete cascade,
    created_at timestamptz not null default now()
);

create index if not exists report_attachments_report_created_at_idx
    on public.report_attachments (report_id, created_at);

alter table public.report_attachments enable row level security;
revoke all on table public.report_attachments from anon, public;
revoke insert on public.report_attachments from authenticated;
grant select on public.report_attachments to authenticated;
grant insert (
    report_id, storage_path, original_name, content_type, file_size, uploaded_by
) on public.report_attachments to authenticated;

drop policy if exists "report participants can read attachment metadata"
    on public.report_attachments;
create policy "report participants can read attachment metadata"
on public.report_attachments for select to authenticated
using (
    exists (
        select r.id
        from public.reports as r
        where r.id = report_attachments.report_id
          and (
              r.created_by = (select auth.uid())
              or (select public.is_report_maintainer())
          )
    )
);

drop policy if exists "report owners can add attachment metadata"
    on public.report_attachments;
create policy "report owners can add attachment metadata"
on public.report_attachments for insert to authenticated
with check (
    uploaded_by = (select auth.uid())
    and split_part(storage_path, '/', 1) = (select auth.uid())::text
    and split_part(storage_path, '/', 2)::uuid = report_id
    and content_type in (
        'image/png', 'image/jpeg', 'image/webp',
        'video/mp4', 'video/x-matroska', 'video/quicktime',
        'video/webm', 'video/x-msvideo'
    )
    and exists (
        select r.id
        from public.reports as r
        where r.id = report_attachments.report_id
          and (
              r.created_by = (select auth.uid())
              or (select public.is_report_maintainer())
          )
    )
    and exists (
        select o.id
        from storage.objects as o
        where o.bucket_id = 'report-attachments'
          and o.name = report_attachments.storage_path
    )
);

insert into storage.buckets (
    id, name, public, file_size_limit, allowed_mime_types
) values (
    'report-attachments',
    'report-attachments',
    false,
    52428800,
    array[
        'image/png', 'image/jpeg', 'image/webp',
        'video/mp4', 'video/x-matroska', 'video/quicktime',
        'video/webm', 'video/x-msvideo'
    ]
)
on conflict (id) do update set
    public = false,
    file_size_limit = 52428800,
    allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "report participants can read private attachments"
    on storage.objects;
create policy "report participants can read private attachments"
on storage.objects for select to authenticated
using (
    bucket_id = 'report-attachments'
    and (
        (storage.foldername(name))[1] = (select auth.uid())::text
        or (select public.is_report_maintainer())
    )
);

drop policy if exists "users can upload attachments to their own folder"
    on storage.objects;
create policy "users can upload attachments to their own folder"
on storage.objects for insert to authenticated
with check (
    bucket_id = 'report-attachments'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and array_length(storage.foldername(name), 1) = 2
    and exists (
        select r.id
        from public.reports as r
        where r.id = ((storage.foldername(name))[2])::uuid
          and r.created_by = (select auth.uid())
    )
);

drop view if exists public.report_inbox;
create view public.report_inbox
with (security_invoker = true)
as
select
    r.id as report_id,
    r.created_at as submitted_at,
    r.title,
    r.description,
    coalesce(
        string_agg(a.original_name, ', ' order by a.created_at)
            filter (where a.id is not null),
        'No attachments'
    ) as attachments
from public.reports as r
left join public.report_attachments as a on a.report_id = r.id
group by r.id, r.created_at, r.title, r.description;

revoke all on public.report_inbox from public, anon, authenticated;

drop view if exists public.report_inbox_files;
create view public.report_inbox_files
with (security_invoker = true)
as
select
    r.id as report_id,
    r.created_at as submitted_at,
    r.title,
    a.original_name as attachment_name,
    a.file_size as attachment_size_bytes,
    a.storage_path
from public.reports as r
join public.report_attachments as a on a.report_id = r.id;

revoke all on public.report_inbox_files from public, anon, authenticated;
