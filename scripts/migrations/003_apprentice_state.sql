-- Run after 001 and 002. Keeps bounded agent evidence across backend restarts.
create table if not exists apprentice_states (
  session_id uuid primary key references sessions(id) on delete cascade,
  state jsonb not null,
  revision integer not null default 0
);
alter table apprentice_states enable row level security;

-- Commit evidence and the checkpoint together; concurrent requests cannot overwrite each other.
create or replace function save_apprentice_state(
  p_session_id uuid, p_state jsonb, p_revision integer,
  p_events jsonb default '[]', p_turns jsonb default '[]'
) returns integer language plpgsql security invoker as $$
declare current_revision integer; item jsonb;
begin
  insert into apprentice_states(session_id,state,revision)
    values(p_session_id,'{}',0) on conflict do nothing;
  select revision into current_revision from apprentice_states
    where session_id=p_session_id for update;
  if current_revision <> p_revision then
    raise exception 'apprentice_revision_conflict';
  end if;
  for item in select value from jsonb_array_elements(p_events) loop
    insert into events(session_id,t_offset_ms,type,page,field,old_value,new_value,payload)
      values(p_session_id,(item->>'t_offset_ms')::integer,item->>'type',
        item->>'page',item->>'field',item->>'old_value',item->>'new_value',item->'payload');
  end loop;
  for item in select value from jsonb_array_elements(p_turns) loop
    insert into transcript_segments(session_id,speaker,t_start_ms,text)
      values(p_session_id,item->>'speaker',(item->>'t_start_ms')::integer,item->>'text');
  end loop;
  update apprentice_states set state=p_state,revision=current_revision+1
    where session_id=p_session_id;
  return current_revision+1;
end $$;
revoke all on function save_apprentice_state(uuid,jsonb,integer,jsonb,jsonb) from public,anon,authenticated;
grant execute on function save_apprentice_state(uuid,jsonb,integer,jsonb,jsonb) to service_role;
